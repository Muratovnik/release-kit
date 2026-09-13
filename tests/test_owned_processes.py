"""Real local child processes exercise timeout, failure and nested cleanup."""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import processes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_distribution
from smoke_onboarding import create_project


class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="owned processes ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "RELKIT_"))
            and key not in {"GH_TOKEN", "GITHUB_TOKEN", "PYTHONHOME", "PYTHONPATH"}
        }
        self.environment["PYTHONPATH"] = str(ROOT / "src")
        self.environment["PYTHONDONTWRITEBYTECODE"] = "1"

    def command(self, body):
        return [sys.executable, "-S", "-c", body]

    def tree(self, *, exit_parent=False):
        ready, late = self.root / "ready", self.root / "late"
        trigger = self.root / "allow-late-write"
        child = (
            "from pathlib import Path\nimport time\n"
            f"Path({str(ready)!r}).write_text('ready')\n"
            "deadline=time.monotonic()+20\n"
            f"while not Path({str(trigger)!r}).exists() and time.monotonic()<deadline:\n"
            " time.sleep(0.01)\n"
            f"if Path({str(trigger)!r}).exists(): Path({str(late)!r}).write_text('survived')\n"
        )
        body = (
            "import subprocess,sys,time\nfrom pathlib import Path\n"
            f"p=subprocess.Popen([sys.executable,'-S','-c',{child!r}], "
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            f"while not Path({str(ready)!r}).exists(): time.sleep(0.01)\n"
            + ("raise SystemExit(0)\n" if exit_parent else "p.wait()\n")
        )
        return self.command(body), ready, late

    def assert_no_late_write(self, ready, late):
        self.assertTrue(ready.exists(), "the child must have started for this control to count")
        (self.root / "allow-late-write").touch()
        time.sleep(0.5)
        self.assertFalse(late.exists(), "descendant kept running after command cleanup")

    def test_bytes_status_cwd_and_separate_arguments(self):
        command = self.command(
            "import os,sys; print(os.getcwd()); print(sys.argv[1]); "
            "sys.stderr.write('detail'); raise SystemExit(7)"
        ) + ["argument with spaces"]
        result = processes.run(
            command,
            cwd=self.root,
            env=self.environment,
            timeout=5,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(7, result.returncode)
        self.assertEqual(
            [str(self.root), "argument with spaces"], result.stdout.decode().splitlines()
        )
        self.assertEqual(b"detail", result.stderr)

    def test_timeout_stops_the_child_not_only_the_parent(self):
        command, ready, late = self.tree()
        with self.assertRaises(subprocess.TimeoutExpired):
            processes.run(command, cwd=self.root, env=self.environment, timeout=2)
        self.assert_no_late_write(ready, late)

    def test_successful_parent_cannot_leave_background_worker(self):
        command, ready, late = self.tree(exit_parent=True)
        result = processes.run(command, cwd=self.root, env=self.environment, timeout=5)
        self.assertEqual(0, result.returncode)
        self.assert_no_late_write(ready, late)

    def test_missing_executable_fails_without_running_fallback(self):
        # Windows reports the wrapper's native error as a nonzero result; it never
        # substitutes another command. POSIX reports the spawn error directly.
        command = [str(self.root / "does-not-exist")]
        if os.name == "nt":
            result = processes.run(command, cwd=self.root, timeout=5, stderr=subprocess.PIPE)
            self.assertNotEqual(0, result.returncode)
        else:
            with self.assertRaises(FileNotFoundError):
                processes.run(command, cwd=self.root, timeout=5)

    @unittest.skipIf(os.name == "nt", "POSIX signal restoration; Job tests run on Windows")
    def test_term_handler_restores_the_callers_signal_policy(self):
        before = signal.getsignal(signal.SIGTERM)
        processes.run(self.command("pass"), timeout=5)
        self.assertIs(before, signal.getsignal(signal.SIGTERM))

    def test_nested_timeout_finishes_worker_cleanup_before_unlock(self):
        command, ready, late = self.tree()
        lock, released = self.root / "lock", self.root / "released"
        middle = (
            "from pathlib import Path\nfrom releasekit import processes\n"
            f"lock=Path({str(lock)!r}); lock.write_text('owned')\n"
            "try:\n"
            f" processes.run({command!r}, timeout=20)\n"
            "finally:\n"
            f" lock.unlink(); Path({str(released)!r}).write_text('done')\n"
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            processes.run(
                self.command(middle),
                env=self.environment,
                cwd=self.root,
                timeout=2,
                stderr=subprocess.PIPE,
            )
        self.assert_no_late_write(ready, late)
        if os.name != "nt":
            self.assertTrue(released.exists(), "nested runner must finish its finally block")
            self.assertFalse(lock.exists())
        # A Windows job terminates all descendants, including the lock owner; a
        # retained lock is intentionally not auto-deleted after that abrupt stop.

    def checked_source(self):
        artifact = self.root / "fixture.pyz"
        artifact.write_bytes(b"setup only; never execute")
        source = self.root / "source"
        create_project(source, artifact)
        (source / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
        return source

    def test_distribution_timeout_cleans_workers_before_unlocking_state(self):
        source = self.checked_source()
        command, ready, late = self.tree()
        script = (
            "from pathlib import Path\nfrom releasekit import processes\n"
            "import check_distribution\n"
            f"check_distribution.ROOT=Path({str(source)!r})\n"
            f"check_distribution.source_checks=lambda root,uv: [('controlled', {command!r})]\n"
            "check_distribution.shutil.which=lambda name: 'uv'\n"
            "with processes.termination_handler():\n"
            " raise SystemExit(check_distribution.main(['--source-only']))\n"
        )
        environment = dict(self.environment)
        environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "tools")))
        with self.assertRaises(subprocess.TimeoutExpired):
            processes.run(
                self.command(script),
                env=environment,
                cwd=self.root,
                timeout=2,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assert_no_late_write(ready, late)
        state = source / ".cache/release-kit-checks"
        if os.name != "nt":
            self.assertFalse((state / "check.lock").exists())
            reports = list((state / "reports").glob("*.json"))
            self.assertEqual(1, len(reports))
            report = json.loads(reports[0].read_text())
            self.assertEqual("interrupted", report["status"])
            self.assertEqual("retained", report["cleanup"])

    def test_unconfirmed_cleanup_retains_the_actual_distribution_lock(self):
        source = self.checked_source()
        commands = [("controlled", self.command("pass"))]
        with (
            patch.object(check_distribution, "ROOT", source),
            patch.object(check_distribution, "source_checks", return_value=commands),
            patch.object(check_distribution.shutil, "which", return_value="uv"),
            patch.object(processes, "run", side_effect=processes.CleanupError("probe failure")),
            patch.object(sys, "path", list(sys.path)),
            patch.dict(os.environ, self.environment, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(1, check_distribution.main(["--source-only"]))
        state = source / ".cache/release-kit-checks"
        self.assertTrue((state / "check.lock").is_file())
        report = json.loads(next((state / "reports").glob("*.json")).read_text())
        self.assertEqual("unconfirmed", report["process_cleanup"])
        self.assertEqual("cleanup-unconfirmed", report["checks"][0]["status"])
        self.assertEqual("failed", report["status"])

    def test_phase_timeout_is_reported_as_failure(self):
        report = {"checks": []}
        with self.assertRaises(subprocess.TimeoutExpired):
            check_distribution.run_stages(
                [("controlled", self.command("import time; time.sleep(5)"))],
                root=self.root,
                environment=self.environment,
                report=report,
                timeout=0.2,
            )
        self.assertEqual("timed-out", report["checks"][0]["status"])

    @unittest.skipUnless(os.name == "nt", "Windows asynchronous process teardown regression")
    def test_short_timeout_releases_the_child_cwd_before_returning(self):
        for index in range(3):
            with self.subTest(index=index):
                cwd = self.root / str(index)
                cwd.mkdir()
                with self.assertRaises(subprocess.TimeoutExpired):
                    processes.run(self.command("import time; time.sleep(5)"), cwd=cwd, timeout=0.2)
                # No retry or pause: the operation boundary must release this cwd.
                cwd.rmdir()


if __name__ == "__main__":
    unittest.main()
