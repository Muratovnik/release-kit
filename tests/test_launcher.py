from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from releasekit import __version__, launcher

ROOT = Path(__file__).resolve().parents[1]
# Runs the installed command's entry point exactly as the console script does.
ENTRY = "from releasekit.launcher import main; raise SystemExit(main())"


def _projection(root: Path, record: Path, code: int = 7) -> Path:
    """A stand-in pinned projection that records how it was invoked."""
    path = root / ".github" / "relkit.pyz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "__main__.py",
            "import json, os, sys\n"
            f"with open({str(record)!r}, 'w', encoding='utf-8') as stream:\n"
            "    json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd()}, stream)\n"
            f"raise SystemExit({code})\n",
        )
    return path


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
            timeout=120,
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
        # The projection resolves `--root .` itself, as `python .github/relkit.pyz` would.
        self.assertEqual(subdirectory, Path(invocation["cwd"]).resolve())

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
