"""Real local child processes exercise timeout, failure and nested cleanup."""

from __future__ import annotations

import contextlib
import errno
import io
import json
import multiprocessing
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import processes, storage
from releasekit.release.backend import CommandError, Pending, Runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_distribution
from smoke_onboarding import create_project


@contextlib.contextmanager
def timeout_after_ready(*paths: Path):
    """Start a real command's operation clock after its fixture has started."""
    original = subprocess.Popen.communicate
    waiting = True
    established = False

    def communicate(process, *args, **kwargs):
        nonlocal waiting, established
        if waiting:
            waiting = False
            deadline = time.monotonic() + 10
            while not all(path.is_file() for path in paths):
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise AssertionError("fixture did not become ready before its operation")
                time.sleep(0.01)
            established = True
        return original(process, *args, **kwargs)

    # The owned runner still spawns, times out, and cleans its real process. Only
    # its first communicate waits for startup before forwarding the same timeout.
    with patch.object(subprocess.Popen, "communicate", communicate):
        yield
    # Expected cleanup refusal must not consume a failed startup precondition.
    if not established:
        raise AssertionError("fixture readiness did not complete before its operation")


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
            f"pending=Path({str(ready.with_suffix('.tmp'))!r}); pending.write_text('ready')\n"
            f"pending.replace({str(ready)!r})\n"
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
        with timeout_after_ready(ready), self.assertRaises(subprocess.TimeoutExpired):
            processes.run(command, cwd=self.root, env=self.environment, timeout=2)
        self.assert_no_late_write(ready, late)

    @unittest.skipIf(os.name == "nt", "POSIX process-group permission boundary")
    def test_transient_group_permission_error_waits_for_disappearance(self):
        native = os.killpg
        for denied_signal in (signal.SIGTERM, 0):
            with self.subTest(signal=denied_signal):
                ready = self.root / f"permission-ready-{denied_signal}"
                release = self.root / f"permission-exit-{denied_signal}"
                command = self.command(
                    "import os,time\nfrom pathlib import Path\n"
                    f"pending=Path({str(ready.with_suffix('.tmp'))!r})\n"
                    "pending.write_text(str(os.getpid()))\n"
                    f"pending.replace({str(ready)!r})\n"
                    f"while not Path({str(release)!r}).exists(): time.sleep(0.01)\n"
                )
                denied, disappeared, signals = [], [], []

                def observed(
                    pid,
                    signum,
                    *,
                    denied_signal=denied_signal,
                    denied=denied,
                    disappeared=disappeared,
                    signals=signals,
                    release=release,
                ):
                    signals.append(signum)
                    if signum == denied_signal and not denied:
                        denied.append(pid)
                        # A signal denial need not keep a process alive. Let the
                        # real child exit independently, then require native ESRCH.
                        release.touch()
                        raise PermissionError(errno.EPERM, "controlled transient group state")
                    try:
                        return native(pid, signum)
                    except ProcessLookupError:
                        disappeared.append(signum)
                        raise

                try:
                    with (
                        patch.object(os, "killpg", observed),
                        timeout_after_ready(ready),
                        self.assertRaises(subprocess.TimeoutExpired),
                    ):
                        processes.run(command, env=self.environment, timeout=0.05)
                    self.assertEqual([int(ready.read_text())], denied)
                    self.assertIn(0, disappeared, "only native absence confirms group cleanup")
                    self.assertNotIn(signal.SIGKILL, signals)
                    with self.assertRaises(ProcessLookupError):
                        native(int(ready.read_text()), 0)
                finally:
                    if ready.is_file():
                        try:
                            native(int(ready.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    @unittest.skipIf(os.name == "nt", "POSIX non-permission signal errors")
    def test_other_group_errors_are_not_treated_as_presence(self):
        for signum in (0, signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=signum):
                error = OSError(errno.EINVAL, "controlled non-permission error")
                with (
                    patch.object(os, "killpg", side_effect=error),
                    self.assertRaises(OSError) as seen,
                ):
                    processes._group(123, signum)
                self.assertIs(error, seen.exception)

    def test_successful_parent_cannot_leave_background_worker(self):
        command, ready, late = self.tree(exit_parent=True)
        with timeout_after_ready(ready):
            result = processes.run(command, cwd=self.root, env=self.environment, timeout=5)
        self.assertEqual(0, result.returncode)
        self.assert_no_late_write(ready, late)

    def test_coordinator_keeps_timeout_unknown_but_stops_owned_workers(self):
        command, ready, late = self.tree()
        with timeout_after_ready(ready), self.assertRaises(Pending):
            Runner(self.root).call(command, cwd=self.root, timeout=2)
        self.assert_no_late_write(ready, late)

    def test_coordinator_preserves_native_failure_and_binary_output(self):
        runner = Runner(self.root)
        output = self.root / "output.bin"
        runner.call(
            self.command("import sys; sys.stdout.buffer.write(bytes([0,255,1]))"), output=output
        )
        self.assertEqual(bytes([0, 255, 1]), output.read_bytes())
        with self.assertRaises(CommandError) as raised:
            runner.call(self.command("import sys; sys.stderr.write('diagnostic'); sys.exit(7)"))
        self.assertEqual(7, raised.exception.result.returncode)
        self.assertEqual(b"diagnostic", raised.exception.result.stderr)

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
        with timeout_after_ready(ready), self.assertRaises(subprocess.TimeoutExpired):
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

    def test_timeout_relays_through_multiple_nested_runners(self):
        command, ready, late = self.tree()
        for _ in range(3):
            command = self.command(
                f"from releasekit import processes\nprocesses.run({command!r}, timeout=25)\n"
            )
        with timeout_after_ready(ready), self.assertRaises(subprocess.TimeoutExpired):
            processes.run(
                command,
                env=self.environment,
                cwd=self.root,
                timeout=2,
                stderr=subprocess.PIPE,
            )
        self.assert_no_late_write(ready, late)

    @unittest.skipIf(os.name == "nt", "POSIX relay failure; Windows owns one native job")
    def test_failed_inner_cleanup_is_not_reported_as_a_confirmed_outer_timeout(self):
        ready = self.root / "worker-pid"
        worker = (
            "import os,time\nfrom pathlib import Path\n"
            f"pending=Path({str(ready.with_suffix('.tmp'))!r})\n"
            "pending.write_text(str(os.getpid()))\n"
            f"pending.replace({str(ready)!r})\n"
            "time.sleep(20)\n"
        )
        relay = (
            "import subprocess\nfrom releasekit import processes\n"
            "def denied(*args): raise PermissionError('controlled cleanup denial')\n"
            "processes._group=denied\n"
            f"processes.run({self.command(worker)!r}, timeout=25, "
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        )
        try:
            with timeout_after_ready(ready), self.assertRaises(processes.CleanupError):
                processes.run(
                    self.command(relay),
                    env=self.environment,
                    cwd=self.root,
                    timeout=2,
                    stderr=subprocess.PIPE,
                )
            self.assertTrue(ready.is_file(), "the denied cleanup must concern a started worker")
        finally:
            if ready.is_file():
                try:
                    os.killpg(int(ready.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass

    @unittest.skipIf(os.name == "nt", "POSIX nested lifetime; Windows owns one native job")
    def test_inner_timeout_cannot_hide_a_live_worker_behind_an_ordinary_failure(self):
        ready = self.root / "early-worker-pid"
        worker = (
            "import os,time\nfrom pathlib import Path\n"
            f"pending=Path({str(ready.with_suffix('.tmp'))!r})\n"
            "pending.write_text(str(os.getpid()))\n"
            f"pending.replace({str(ready)!r})\n"
            "time.sleep(20)\n"
        )
        relay = (
            "import subprocess,sys\nfrom pathlib import Path\nfrom releasekit import processes\n"
            f"sys.path.insert(0,{str(ROOT / 'tests')!r})\n"
            "from test_owned_processes import timeout_after_ready\n"
            "def denied(*args): raise PermissionError('controlled cleanup denial')\n"
            "processes._group=denied\n"
            f"with timeout_after_ready(Path({str(ready)!r})):\n"
            f" processes.run({self.command(worker)!r}, timeout=2, env={{}}, "
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        )
        try:
            with (
                patch.object(processes, "_GRACE", 0.3),
                timeout_after_ready(ready),
                self.assertRaises(processes.CleanupError),
            ):
                processes.run(
                    self.command(relay),
                    env=self.environment,
                    cwd=self.root,
                    timeout=8,
                    stderr=subprocess.PIPE,
                )
            self.assertTrue(ready.is_file(), "the inner command must fail after its worker starts")
        finally:
            if ready.is_file():
                try:
                    os.killpg(int(ready.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass

    @unittest.skipIf(os.name == "nt", "POSIX descriptor inheritance")
    def test_stale_lifetime_metadata_cannot_inherit_an_unrelated_pipe(self):
        reader, writer = os.pipe()
        try:
            os.set_inheritable(writer, True)
            identity = os.fstat(writer)
            body = (
                "import os\n"
                f"try: value=os.fstat({writer})\n"
                "except OSError: print('not inherited')\n"
                f"else: print((value.st_dev,value.st_ino)=={(identity.st_dev, identity.st_ino)!r})\n"
            )
            with patch.dict(
                os.environ,
                {
                    processes._LIFETIME_ENV: json.dumps(
                        [[writer, identity.st_dev, identity.st_ino + 1]]
                    )
                },
            ):
                result = processes.run(self.command(body), timeout=5, stdout=subprocess.PIPE)
            self.assertNotIn(b"True", result.stdout)
            self.assertEqual(identity.st_ino, os.fstat(writer).st_ino)
        finally:
            os.close(writer)
            os.close(reader)

    @unittest.skipIf(os.name == "nt", "POSIX cancellation outcomes")
    def test_cooperative_interruption_statuses_and_abnormal_exits_are_distinct(self):
        for code in (3, 130, 2):
            with self.subTest(code=code):
                ready = self.root / f"ready-{code}"
                command = self.command(
                    "import signal,sys,time\nfrom pathlib import Path\n"
                    f"signal.signal(signal.SIGTERM,lambda *args: sys.exit({code}))\n"
                    f"Path({str(ready)!r}).touch()\n"
                    "time.sleep(20)\n"
                )
                exception = processes.CleanupError if code == 2 else subprocess.TimeoutExpired
                with timeout_after_ready(ready), self.assertRaises(exception):
                    processes.run(command, timeout=2, stderr=subprocess.PIPE)
                self.assertTrue(ready.exists())

    @unittest.skipIf(os.name == "nt", "POSIX hard kill cannot acknowledge nested cleanup")
    def test_forced_termination_requires_retaining_state(self):
        ready = self.root / "ignoring-term"
        command = self.command(
            "import signal,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            f"Path({str(ready)!r}).touch()\n"
            "time.sleep(20)\n"
        )
        with (
            patch.object(processes, "_GRACE", 0.2),
            timeout_after_ready(ready),
            self.assertRaisesRegex(processes.CleanupError, "forced or abnormal"),
        ):
            processes.run(command, timeout=2, stderr=subprocess.PIPE)
        self.assertTrue(ready.exists())

    def checked_source(self):
        artifact = self.root / "fixture.pyz"
        artifact.write_bytes(b"setup only; never execute")
        source = self.root / "source"
        create_project(source, artifact)
        declaration = source / "src/releasekit/__init__.py"
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
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
        with timeout_after_ready(ready), self.assertRaises(subprocess.TimeoutExpired):
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

    @unittest.skipIf(os.name == "nt", "POSIX denied group must retain its distribution lock")
    def test_persistent_group_permission_error_retains_distribution_lock(self):
        source = self.checked_source()
        command, ready, late = self.tree()
        run_stages = check_distribution.run_stages
        stop = processes._stop
        denied, children = [], []

        def refusal(pid, signum):
            denied.append((pid, signum))
            raise PermissionError(errno.EPERM, "controlled persistent group denial")

        def short_stage(commands, **kwargs):
            kwargs["timeout"] = 0.05
            with timeout_after_ready(ready):
                try:
                    return run_stages(commands, **kwargs)
                except processes.CleanupError as error:
                    failure = error
            raise failure

        def observed_stop(child, *args, **kwargs):
            if child not in children:
                children.append(child)
            return stop(child, *args, **kwargs)

        try:
            with (
                patch.object(check_distribution, "ROOT", source),
                patch.object(check_distribution, "source_checks", return_value=[("base", command)]),
                patch.object(check_distribution.shutil, "which", return_value="uv"),
                patch.object(check_distribution, "run_stages", short_stage),
                patch.object(processes, "_GRACE", 0.05),
                patch.object(processes, "_stop", observed_stop),
                patch.object(os, "killpg", refusal),
                patch.object(sys, "path", list(sys.path)),
                patch.dict(os.environ, self.environment, clear=True),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(1, check_distribution.main(["--source-only"]))
            self.assertTrue(ready.is_file())
            self.assertEqual({0, signal.SIGTERM, signal.SIGKILL}, {sig for _, sig in denied})
            state = source / ".cache/release-kit-checks"
            self.assertTrue((state / "check.lock").is_file())
            report = json.loads(next((state / "reports").glob("*.json")).read_text())
            self.assertEqual("unconfirmed", report["process_cleanup"])
            self.assertEqual("cleanup-unconfirmed", report["checks"][0]["status"])
            # The descendant closes inherited lifetime descriptors, but still
            # belongs to this denied group and can use the retained workspace.
            (self.root / "allow-late-write").touch()
            deadline = time.monotonic() + 5
            while not late.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(late.exists(), "the retained lock must protect a real live worker")
        finally:
            for pid in {pid for pid, _ in denied}:
                processes._group(pid, signal.SIGKILL)
            for child in children:
                child.wait(timeout=5)

    @unittest.skipIf(os.name == "nt", "POSIX sequential source-gate cleanup propagation")
    def test_source_gate_preserves_failed_sequential_worker_cleanup(self):
        source = self.checked_source()
        suite = self.root / "gate-tests"
        suite.mkdir()
        ready = self.root / "gate-worker-pid"
        leaf = (
            "import os,time\nfrom pathlib import Path\n"
            f"pending=Path({str(ready.with_suffix('.tmp'))!r})\n"
            "pending.write_text(str(os.getpid()))\n"
            f"pending.replace({str(ready)!r})\n"
            "time.sleep(20)\n"
        )
        (suite / "test_gate.py").write_text(
            "import subprocess,sys,unittest\nfrom releasekit import processes\n"
            "class GateTests(unittest.TestCase):\n"
            " def test_owned_worker(self):\n"
            "  def denied(*args): raise PermissionError('controlled inner cleanup denial')\n"
            "  processes._group=denied\n"
            f"  processes.run({self.command(leaf)!r}, timeout=25, "
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n",
            encoding="utf-8",
        )
        checker = self.root / "run-check.py"
        checker.write_text(
            "import sys\n"
            f"sys.path.insert(0,{str(ROOT / 'tools')!r})\n"
            "import check\n"
            f"check.GATES=([{str(ROOT / 'tools/parallel_tests.py')!r}, "
            f"'--jobs','1','--start-dir',{str(suite)!r}],)\n"
            "raise SystemExit(check.main())\n",
            encoding="utf-8",
        )
        script = (
            "from pathlib import Path\nfrom releasekit import processes\n"
            "import check_distribution\n"
            f"check_distribution.ROOT=Path({str(source)!r})\n"
            "check_distribution.source_checks=lambda root,uv: "
            f"[('base',{[sys.executable, str(checker)]!r})]\n"
            "check_distribution.shutil.which=lambda name:'uv'\n"
            "with processes.termination_handler():\n"
            " raise SystemExit(check_distribution.main(['--source-only']))\n"
        )
        environment = dict(self.environment)
        environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "tools")))
        try:
            with timeout_after_ready(ready), self.assertRaises(processes.CleanupError):
                processes.run(
                    self.command(script),
                    env=environment,
                    cwd=self.root,
                    timeout=2,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            self.assertTrue(ready.is_file(), "the cleanup fault must concern a started worker")
            state = source / ".cache/release-kit-checks"
            self.assertTrue((state / "check.lock").is_file())
            report = json.loads(next((state / "reports").glob("*.json")).read_text())
            self.assertEqual("unconfirmed", report["process_cleanup"])
            self.assertEqual("cleanup-unconfirmed", report["checks"][0]["status"])
        finally:
            if ready.is_file():
                try:
                    os.killpg(int(ready.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass

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

    @unittest.skipIf(os.name == "nt", "POSIX writer transfer into spawned source-test workers")
    def test_spawned_source_workers_preserve_cleanup_ownership(self):
        self._source_workers_preserve_cleanup_ownership("spawn")

    @unittest.skipIf(os.name == "nt", "POSIX forkserver writer transfer")
    def test_forkserver_source_workers_preserve_cleanup_ownership(self):
        if "forkserver" not in multiprocessing.get_all_start_methods():
            self.skipTest("this interpreter does not provide the forkserver start method")
        # Distribution checks nest TMPDIR deeply. Keep this test's IPC directory
        # short and owned; change only the generated pool launcher's tempfile cache.
        cache = storage.inside(ROOT, ROOT / ".cache")
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mp-", dir=cache) as temporary:
            ipc = Path(temporary).resolve()
            # Probe the real socket operation at the same path depth as CPython's
            # pymp-<8>/listener-<8>. Do not turn worker/transfer failures into skips.
            with tempfile.TemporaryDirectory(prefix="pymp-", dir=ipc) as probe:
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                        listener.bind(str(Path(probe) / "listener-12345678"))
                        listener.listen(1)
                except OSError as error:
                    unavailable = error.errno in {
                        errno.EPERM,
                        errno.EACCES,
                        errno.EAFNOSUPPORT,
                        errno.EPROTONOSUPPORT,
                        errno.ENOSYS,
                        errno.ENOTSUP,
                        errno.ENAMETOOLONG,
                    }
                    path_limit = error.errno is None and str(error) == "AF_UNIX path too long"
                    if unavailable or path_limit:
                        self.skipTest(f"owned AF_UNIX socket capability/path unavailable: {error}")
                    raise
            self._source_workers_preserve_cleanup_ownership("forkserver", ipc=ipc)

    def _source_workers_preserve_cleanup_ownership(self, method, *, ipc=None):
        source = self.checked_source()
        suite = self.root / f"{method}-tests"
        suite.mkdir()
        denied, returned = self.root / "deny-cleanup", self.root / "source-returned"
        worker = (
            "import os,sys,time\nfrom pathlib import Path\n"
            "root=Path(sys.argv[1]);name=sys.argv[2]\n"
            "pending=root/(name+'.pid.tmp'); pending.write_text(str(os.getpid()))\n"
            "pending.replace(root/(name+'.pid'))\n"
            "deadline=time.monotonic()+20\n"
            f"while not Path({str(returned)!r}).exists() and time.monotonic()<deadline:\n"
            " time.sleep(0.01)\n"
            f"if Path({str(returned)!r}).exists(): (root/(name+'.late')).touch()\n"
        )
        (suite / "test_spawned.py").write_text(
            "import multiprocessing,os,subprocess,sys,time,unittest\nfrom pathlib import Path\n"
            "from releasekit import processes\n"
            "from test_owned_processes import timeout_after_ready\n"
            "class SpawnedTests(unittest.TestCase):\n"
            " def owned(self,name,other):\n"
            f"  root=Path({str(self.root)!r})\n"
            f"  self.assertEqual({method!r},multiprocessing.get_start_method())\n"
            "  pending=root/(name+'.runner.tmp'); pending.write_text(str(os.getpid()))\n"
            "  pending.replace(root/(name+'.runner'))\n"
            "  deadline=time.monotonic()+5\n"
            "  while not (root/(other+'.runner')).exists() and time.monotonic()<deadline:\n"
            "   time.sleep(0.01)\n"
            "  self.assertTrue((root/(other+'.runner')).exists())\n"
            f"  if Path({str(denied)!r}).exists():\n"
            "   def refusal(*args): raise PermissionError('controlled spawned worker denial')\n"
            "   processes._group=refusal\n"
            "  with timeout_after_ready(root/(name+'.pid')):\n"
            f"   processes.run([sys.executable,'-S','-c',{worker!r},str(root),name], "
            "timeout=0.5,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            " def test_first(self): self.owned('first','second')\n"
            " def test_second(self): self.owned('second','first')\n",
            encoding="utf-8",
        )
        parallel = self.root / f"run-{method}.py"
        method_setup = f" multiprocessing.set_start_method({method!r},force=True)\n"
        if ipc is not None:
            method_setup = f" tempfile.tempdir={str(ipc)!r}\n" + method_setup
        parallel.write_text(
            "import multiprocessing,sys,tempfile\n"
            f"sys.path.insert(0,{str(ROOT / 'tools')!r})\n"
            "import parallel_tests\n"
            "if __name__=='__main__':\n"
            f"{method_setup}"
            f" raise SystemExit(parallel_tests.main(['--jobs','2','--start-dir',{str(suite)!r}]))\n",
            encoding="utf-8",
        )
        checker = self.root / "run-pool-check.py"
        checker.write_text(
            "import sys\n"
            f"sys.path.insert(0,{str(ROOT / 'tools')!r})\n"
            "import check\n"
            f"check.GATES=([{str(parallel)!r}],)\n"
            "raise SystemExit(check.main())\n",
            encoding="utf-8",
        )
        script = (
            "from pathlib import Path\nfrom releasekit import processes\n"
            "import check_distribution\n"
            f"check_distribution.ROOT=Path({str(source)!r})\n"
            "check_distribution.source_checks=lambda root,uv: "
            f"[('base',{[sys.executable, str(checker)]!r})]\n"
            "check_distribution.shutil.which=lambda name:'uv'\n"
            "with processes.termination_handler():\n"
            " raise SystemExit(check_distribution.main(['--source-only']))\n"
        )
        environment = dict(self.environment)
        environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "tools")))
        state = source / ".cache/release-kit-checks"
        for refuse in (False, True):
            with self.subTest(cleanup_denied=refuse):
                for name in ("first", "second"):
                    for suffix in ("pid", "runner", "late"):
                        (self.root / f"{name}.{suffix}").unlink(missing_ok=True)
                returned.unlink(missing_ok=True)
                if refuse:
                    denied.touch()
                before = set((state / "reports").glob("*.json"))
                try:
                    expected = (
                        self.assertRaises(processes.CleanupError)
                        if refuse
                        else contextlib.nullcontext()
                    )
                    with (
                        timeout_after_ready(
                            *(self.root / f"{name}.pid" for name in ("first", "second"))
                        ),
                        expected,
                    ):
                        completed = processes.run(
                            self.command(script),
                            env=environment,
                            cwd=self.root,
                            timeout=15,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )
                        self.assertEqual(1, completed.returncode, completed.stderr)
                    self.assertTrue(
                        all((self.root / f"{name}.pid").is_file() for name in ("first", "second"))
                    )
                    self.assertNotEqual(
                        (self.root / "first.runner").read_text(),
                        (self.root / "second.runner").read_text(),
                        "both independent pool workers must execute",
                    )
                    reports = set((state / "reports").glob("*.json")) - before
                    self.assertEqual(1, len(reports))
                    report = json.loads(reports.pop().read_text())
                    self.assertEqual(refuse, (state / "check.lock").is_file())
                    self.assertEqual(refuse, report.get("process_cleanup") == "unconfirmed")
                    returned.touch()
                    time.sleep(0.2)
                    for name in ("first", "second"):
                        self.assertEqual(refuse, (self.root / f"{name}.late").exists())
                finally:
                    for name in ("first", "second"):
                        ready = self.root / f"{name}.pid"
                        if ready.is_file():
                            try:
                                os.killpg(int(ready.read_text()), signal.SIGTERM)
                            except ProcessLookupError:
                                pass

    @unittest.skipUnless(os.name == "nt", "Windows asynchronous process teardown regression")
    def test_short_timeout_releases_the_child_cwd_before_returning(self):
        for index in range(3):
            with self.subTest(index=index):
                cwd = self.root / str(index)
                cwd.mkdir()
                ready = self.root / f"cwd-ready-{index}"
                command = self.command(
                    "import time\nfrom pathlib import Path\n"
                    f"Path({str(ready)!r}).touch()\n"
                    "time.sleep(5)\n"
                )
                with timeout_after_ready(ready), self.assertRaises(subprocess.TimeoutExpired):
                    processes.run(command, cwd=cwd, timeout=0.2)
                # No retry or pause: the operation boundary must release this cwd.
                cwd.rmdir()


if __name__ == "__main__":
    unittest.main()
