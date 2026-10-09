"""Smoke harnesses preserve candidate output and owned descendant lifetime."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import process_fixtures
from process_fixtures import stop_recorded_worker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import smoke
import smoke_onboarding
import smoke_wheel

from releasekit import processes


class SmokeProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="smoke process ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.environment = smoke_onboarding.fixture_environment()

    def candidate(self, path, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("__main__.py", source)
        return path

    def worker(self, case):
        ready, trigger, late = (case / name for name in ("ready", "trigger", "late"))
        source = (
            "import os,time\nfrom pathlib import Path\n"
            f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
            "deadline=time.monotonic()+15\n"
            f"while not Path({str(trigger)!r}).exists() and time.monotonic()<deadline:\n"
            " time.sleep(0.01)\n"
            f"if Path({str(trigger)!r}).exists(): Path({str(late)!r}).write_text('survived')\n"
        )
        return [sys.executable, "-B", "-c", source], ready, trigger, late

    def test_onboarding_executes_candidate_argv_and_reads_its_utf8_json(self):
        self.candidate(
            self.root / ".github/relkit.pyz",
            "import json,os,sys\n"
            "sys.stdout.buffer.write((json.dumps({'schema_version':1,'exit_code':0,'text':'готово',"
            "'argv':sys.argv[1:],'cwd':os.getcwd()},ensure_ascii=False)+'\\n').encode('utf-8'))\n",
        )
        result = smoke_onboarding.invoke(self.root, self.environment, 0, "notes", "with spaces")
        self.assertEqual("готово", result["text"])
        self.assertEqual(["notes", "with spaces", "--json"], result["argv"])
        self.assertEqual(str(self.root), result["cwd"])

    def test_cli_smoke_preserves_text_newlines_and_replacement_decoding(self):
        self.candidate(
            self.root / smoke.CLI,
            "import sys\nsys.stdout.buffer.write(b'first\\r\\nsecond\\r\\xff\\n')\n",
        )
        self.assertEqual("first\nsecond\n\ufffd\n", smoke._run(self.root, ["--version"]))

    def test_cli_smoke_keeps_failed_candidate_stderr_readable(self):
        self.candidate(
            self.root / smoke.CLI,
            "import sys\nsys.stderr.buffer.write('ошибка\\r\\n'.encode('utf-8'))\n"
            "raise SystemExit(7)\n",
        )
        with self.assertRaisesRegex(smoke.SmokeError, r"exit 7: ошибка$"):
            smoke._run(self.root, ["--version"])

    def test_wheel_wrapper_preserves_status_text_cwd_and_separate_arguments(self):
        source = (
            "import json,os,sys\n"
            "print(json.dumps([os.getcwd(),sys.argv[1:]]))\n"
            "sys.stderr.buffer.write(b'detail\\r\\n')\nraise SystemExit(7)\n"
        )
        command = [sys.executable, "-B", "-c", source, "one argument"]
        completed = smoke_wheel._run(command, self.environment, cwd=self.root)
        self.assertEqual(command, completed.args)
        self.assertEqual(7, completed.returncode)
        self.assertEqual([str(self.root), ["one argument"]], json.loads(completed.stdout))
        self.assertEqual("detail\n", completed.stderr)

    def test_successful_candidate_cannot_leave_its_background_worker(self):
        command, ready, trigger, late = self.worker(self.root)
        self.candidate(
            self.root / ".github/relkit.pyz",
            "import subprocess,time\nfrom pathlib import Path\n"
            f"subprocess.Popen({command!r},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            f"while not Path({str(ready)!r}).exists(): time.sleep(0.01)\n"
            'print(\'{"schema_version":1,"exit_code":0}\')\n',
        )
        try:
            result = smoke_onboarding.invoke(self.root, self.environment, 0, "audit")
            self.assertEqual(0, result["exit_code"])
            self.assertTrue(ready.is_file(), "the background worker must have started")
            trigger.touch()
            time.sleep(0.3)
            self.assertFalse(late.exists(), "worker survived a completed smoke invocation")
        finally:
            stop_recorded_worker(ready)

    @unittest.skipIf(os.name == "nt", "POSIX cleanup-denial propagation; Job tests cover Windows")
    def test_all_candidate_wrappers_propagate_inner_cleanup_failure(self):
        for wrapper in ("onboarding", "cli", "wheel"):
            with self.subTest(wrapper=wrapper):
                case = self.root / wrapper
                case.mkdir()
                command, ready, trigger, late = self.worker(case)
                path = case / (".github/relkit.pyz" if wrapper == "onboarding" else "relkit.pyz")
                self.candidate(
                    path,
                    "import subprocess,sys\n"
                    f"sys.path.insert(0,{str(ROOT / 'src')!r})\n"
                    "from releasekit import processes\n"
                    "def denied(*args): raise PermissionError('controlled cleanup denial')\n"
                    "processes._group=denied\n"
                    f"processes.run({command!r},timeout=1,"
                    "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n",
                )
                observed = None
                try:
                    with patch.object(processes, "_GRACE", 0.1):
                        try:
                            if wrapper == "onboarding":
                                smoke_onboarding.invoke(case, self.environment, 0, "audit")
                            elif wrapper == "cli":
                                smoke._run(case, ["--version"])
                            else:
                                smoke_wheel._run([sys.executable, str(path)], self.environment)
                        except RuntimeError as error:
                            observed = error
                    self.assertTrue(ready.is_file(), "the cleanup fault must concern a live worker")
                    trigger.touch()
                    deadline = time.monotonic() + 1
                    while not late.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(
                        late.is_file(), "controlled cleanup denial must leave the worker alive"
                    )
                    self.assertIsInstance(
                        observed,
                        processes.CleanupError,
                        "an inner cleanup failure must not become an ordinary candidate failure",
                    )
                finally:
                    stop_recorded_worker(ready)

    def test_recorded_worker_cleanup_distinguishes_missing_pid_from_other_errors(self):
        ready = self.root / "recorded-pid"
        ready.write_text("123")
        for platform, code, accepted in (
            ("nt", 87, True),
            ("nt", 5, False),
            ("nt", 6, False),
            ("posix", 87, False),
            ("posix", None, False),
        ):
            with self.subTest(platform=platform, winerror=code):
                error = OSError("controlled native cleanup error")
                if code is not None:
                    error.winerror = code
                boundary = SimpleNamespace(name=platform, kill=None)
                with (
                    patch.object(process_fixtures, "os", boundary),
                    patch.object(boundary, "kill", side_effect=error) as kill,
                ):
                    if accepted:
                        stop_recorded_worker(ready)
                    else:
                        with self.assertRaises(OSError) as seen:
                            stop_recorded_worker(ready)
                        self.assertIs(error, seen.exception)
                    kill.assert_called_once_with(123, signal.SIGTERM)
        with patch.object(process_fixtures.os, "kill", side_effect=ProcessLookupError):
            stop_recorded_worker(ready)

    def test_recorded_worker_cleanup_requires_a_positive_pid(self):
        ready = self.root / "recorded-pid"
        with patch.object(process_fixtures.os, "kill") as kill:
            stop_recorded_worker(ready)
            kill.assert_not_called()
            for value in ("0", "-1", "not a PID"):
                with self.subTest(value=value):
                    ready.write_text(value)
                    with self.assertRaises(ValueError):
                        stop_recorded_worker(ready)
            kill.assert_not_called()

    def test_recorded_worker_cleanup_handles_live_and_finished_native_children(self):
        ready = self.root / "recorded-pid"
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            ready.write_text(str(child.pid))
            stop_recorded_worker(ready)
            child.wait(timeout=5)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        # Release Windows' process handle before checking the now absent PID.
        del child
        stop_recorded_worker(ready)

    def test_wheel_cleanup_retains_unknown_workers_but_uninstalls_after_completed_failure(self):
        for unconfirmed in (False, True):
            with self.subTest(unconfirmed=unconfirmed):
                work = self.root / str(unconfirmed)
                marker = work / "bin" / ("relkit.exe" if sys.platform == "win32" else "relkit")
                calls = []

                def run(
                    command,
                    environment,
                    cwd=None,
                    *,
                    calls=calls,
                    marker=marker,
                    unconfirmed=unconfirmed,
                ):
                    calls.append(command)
                    if command[:3] == ["uv", "tool", "install"]:
                        marker.touch()
                        other = marker.with_name(
                            "relkit-mcp.exe" if sys.platform == "win32" else "relkit-mcp"
                        )
                        other.touch()
                        return subprocess.CompletedProcess(command, 0, "installed", "")
                    if command[:3] == ["uv", "tool", "uninstall"]:
                        marker.unlink()
                        return subprocess.CompletedProcess(command, 0, "removed", "")
                    if unconfirmed:
                        raise processes.CleanupError("worker may remain")
                    return subprocess.CompletedProcess(command, 7, "", "completed failure")

                expected = processes.CleanupError if unconfirmed else smoke_wheel.WheelSmokeError
                with (
                    patch.object(smoke_wheel, "_uv", return_value="uv"),
                    patch.object(smoke_wheel, "_run", side_effect=run),
                    self.assertRaises(expected),
                ):
                    smoke_wheel.check(self.root / "unused.whl", "1.2.3", work)
                self.assertEqual(unconfirmed, marker.exists())
                self.assertEqual(
                    not unconfirmed,
                    any(command[:3] == ["uv", "tool", "uninstall"] for command in calls),
                )


if __name__ == "__main__":
    unittest.main()
