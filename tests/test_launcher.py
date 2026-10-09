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
from unittest.mock import patch

from releasekit import __version__, launcher, processes

ROOT = Path(__file__).resolve().parents[1]
# Runs the installed command's entry point exactly as the console script does.
ENTRY = "from releasekit.launcher import main; raise SystemExit(main())"
COMMAND_TIMEOUT = 120


def _script_projection(root: Path, script: str) -> Path:
    path = root / ".github" / "relkit.pyz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("__main__.py", script)
    return path


def _projection(root: Path, record: Path, code: int = 7) -> Path:
    """A stand-in pinned projection that records how it was invoked."""
    return _script_projection(
        root,
        "import json, os, sys\n"
        f"with open({str(record)!r}, 'w', encoding='utf-8') as stream:\n"
        "    json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd()}, stream)\n"
        f"raise SystemExit({code})\n",
    )


def _git_init(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    return root


class PinnedProjectionTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        self.scratch = Path(handle.name).resolve()
        # Git must not find a repository above the scratch directory by accident.
        ceiling = patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.scratch)})
        ceiling.start()
        self.addCleanup(ceiling.stop)
        self.repository = _git_init(self.scratch / "project")
        self.pinned = _projection(self.repository, self.scratch / "record.json")

    def found(self, argv, cwd, environment=None):
        answer = launcher.pinned_projection(argv, environment or {}, cwd)
        return None if answer is None else answer.resolve()

    def test_the_repository_root_and_its_subdirectories_find_the_pinned_projection(self):
        (self.repository / "web" / "src").mkdir(parents=True)
        for cwd in (self.repository, self.repository / "web" / "src"):
            self.assertEqual(self.pinned, self.found(["audit"], cwd))

    def test_an_explicit_root_names_the_repository_whose_projection_runs(self):
        other = _git_init(self.scratch / "other")
        pinned = _projection(other, self.scratch / "other.json")
        for argv in (["audit", "--root", str(other)], ["audit", "--root=../other"]):
            self.assertEqual(pinned, self.found(argv, self.repository))

    def test_arguments_after_the_separator_are_not_read_as_a_root(self):
        other = _git_init(self.scratch / "other")
        _projection(other, self.scratch / "other.json")
        argv = ["release", "run", "--", "--root", str(other)]
        self.assertEqual(self.pinned, self.found(argv, self.repository))

    def test_no_projection_no_repository_or_no_directory_runs_the_installed_version(self):
        bare = _git_init(self.scratch / "bare")
        outside = self.scratch / "outside"
        outside.mkdir()
        self.assertIsNone(self.found(["audit"], bare))
        self.assertIsNone(self.found(["audit"], outside))
        self.assertIsNone(self.found(["audit", "--root", "missing"], self.repository))

    def test_a_directory_named_like_the_projection_is_not_run(self):
        bare = _git_init(self.scratch / "bare")
        (bare / ".github" / "relkit.pyz").mkdir(parents=True)
        self.assertIsNone(self.found(["audit"], bare))

    def test_the_opt_out_keeps_the_installed_version(self):
        self.assertIsNone(self.found(["audit"], self.repository, {launcher.ENVIRONMENT: "0"}))
        self.assertEqual(
            self.pinned, self.found(["audit"], self.repository, {launcher.ENVIRONMENT: "1"})
        )


class InstalledCommandTests(unittest.TestCase):
    """The console script's process boundary: arguments, directory and exit code."""

    def setUp(self):
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        self.scratch = Path(handle.name).resolve()
        self.environment = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "GIT_CEILING_DIRECTORIES": str(self.scratch),
        }
        self.environment.pop(launcher.ENVIRONMENT, None)

    def run_installed(self, argv, cwd, **extra):
        return subprocess.run(
            [sys.executable, "-c", ENTRY, *argv],
            cwd=cwd,
            env={**self.environment, **extra},
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )

    def test_the_whole_invocation_goes_to_the_pinned_projection(self):
        repository = _git_init(self.scratch / "project")
        record = self.scratch / "record.json"
        _projection(repository, record, code=7)
        subdirectory = repository / "web"
        subdirectory.mkdir()
        argv = ["audit", "--history", "--json", "--", "with space"]
        completed = self.run_installed(argv, subdirectory)
        self.assertEqual(7, completed.returncode, completed.stderr)
        invocation = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(argv, invocation["argv"])
        # Every command takes `.` as its repository, so it runs where the hooks run it.
        self.assertEqual(repository, Path(invocation["cwd"]).resolve())

    def test_an_explicit_root_is_resolved_where_it_was_typed(self):
        repository = _git_init(self.scratch / "project")
        record = self.scratch / "record.json"
        _projection(repository, record, code=0)
        subdirectory = repository / "web"
        subdirectory.mkdir()
        argv = ["protect", "check", "--root", ".."]
        completed = self.run_installed(argv, subdirectory)
        self.assertEqual(0, completed.returncode, completed.stderr)
        invocation = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(argv, invocation["argv"])
        self.assertEqual(subdirectory, Path(invocation["cwd"]).resolve())

    def test_managed_projection_calls_preserve_status_arguments_and_cwd(self):
        repository = _git_init(self.scratch / "project")
        subdirectory = repository / "web"
        subdirectory.mkdir()
        record = self.scratch / "record.json"
        cases = (
            (["audit", "--", "with space"], 7, repository),
            (["protect", "check", "--root", ".."], 0, subdirectory),
        )
        for argv, code, expected_cwd in cases:
            with self.subTest(argv=argv):
                _projection(repository, record, code=code)
                completed = processes.run(
                    [sys.executable, "-c", ENTRY, *argv],
                    cwd=subdirectory,
                    env=self.environment,
                    timeout=COMMAND_TIMEOUT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(code, completed.returncode, completed.stderr)
                invocation = json.loads(record.read_text(encoding="utf-8"))
                self.assertEqual(argv, invocation["argv"])
                self.assertEqual(expected_cwd, Path(invocation["cwd"]).resolve())

    def failing_cleanup_projection(self):
        repository = _git_init(self.scratch / "project")
        ready = self.scratch / "worker-pid"
        worker = (
            "import os,time\nfrom pathlib import Path\n"
            f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
            "time.sleep(20)\n"
        )
        _script_projection(
            repository,
            "import subprocess,sys\nfrom releasekit import processes\n"
            "def denied(*args): raise PermissionError('controlled projection cleanup denial')\n"
            "processes._group=denied\n"
            f"processes.run([sys.executable,'-S','-c',{worker!r}], timeout=0.5, "
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n",
        )

        def stop_worker():
            if ready.is_file():
                try:
                    os.killpg(int(ready.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass

        self.addCleanup(stop_worker)
        return repository, ready

    @unittest.skipIf(os.name == "nt", "POSIX lifetime propagation across the pinned projection")
    def test_a_projection_cannot_hide_its_own_failed_nested_cleanup(self):
        repository, ready = self.failing_cleanup_projection()
        with self.assertRaises(processes.CleanupError):
            processes.run(
                [sys.executable, "-c", ENTRY, "audit"],
                cwd=repository,
                env=self.environment,
                timeout=5,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertTrue(ready.is_file(), "the cleanup fault must concern a started child")

    @unittest.skipIf(os.name == "nt", "POSIX projection lifetime diagnostic")
    def test_unconfirmed_projection_cleanup_has_an_explicit_launcher_diagnostic(self):
        repository, ready = self.failing_cleanup_projection()
        completed = self.run_installed(["audit"], repository)
        self.assertTrue(ready.is_file(), "the cleanup fault must concern a started child")
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertIn("relkit: projection process cleanup is unconfirmed", completed.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX terminal group interruption policy")
    def test_projection_stdin_and_interrupt_cleanup_wait_are_preserved(self):
        repository = _git_init(self.scratch / "project")
        ready = self.scratch / "ready"
        interrupted = self.scratch / "interrupted"
        record = self.scratch / "input.txt"
        _script_projection(
            repository,
            "import signal,sys\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGINT, "
            f"lambda *args: Path({str(interrupted)!r}).touch())\n"
            f"Path({str(ready)!r}).touch()\n"
            f"Path({str(record)!r}).write_text(sys.stdin.readline())\n"
            "raise SystemExit(7)\n",
        )
        process = subprocess.Popen(
            [sys.executable, "-c", ENTRY, "audit"],
            cwd=repository,
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "the projection must be waiting for input")
            os.killpg(process.pid, signal.SIGINT)
            deadline = time.monotonic() + 5
            while not interrupted.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(interrupted.exists(), "the projection must receive the interrupt")
            self.assertIsNone(process.poll(), "the launcher must let projection cleanup finish")
            _, errors = process.communicate(input=b"synthetic terminal input\n", timeout=5)
            self.assertEqual(7, process.returncode, errors)
            self.assertEqual("synthetic terminal input\n", record.read_text())
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def test_without_a_pinned_projection_the_installed_version_answers(self):
        outside = self.scratch / "outside"
        outside.mkdir()
        completed = self.run_installed(["--version"], outside)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(completed.stdout.startswith(f"release-kit {__version__} "))

    def test_the_opt_out_answers_with_the_installed_version_inside_a_pinned_repository(self):
        repository = _git_init(self.scratch / "project")
        record = self.scratch / "record.json"
        _projection(repository, record)
        completed = self.run_installed(["--version"], repository, **{launcher.ENVIRONMENT: "0"})
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(completed.stdout.startswith(f"release-kit {__version__} "))
        self.assertFalse(record.exists())


if __name__ == "__main__":
    unittest.main()
