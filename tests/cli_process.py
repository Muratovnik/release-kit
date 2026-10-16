"""Shared fixture for tests that run `relkit` as its own process and read its real streams."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_notes import VALID

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = re.compile(
    r"^relkit (?P<command>[a-z]+(?: [a-z]+)?): (?P<status>pass|fail|refused|pending|error) "
    r"\(exit (?P<code>\d+), (?P<seconds>\d+\.\d)s\)$"
)
POLICY = "[exposure]\ncheck_secrets = false\ncheck_links = false\n"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def relkit(root: Path, *args: str, merge_stderr: bool = False) -> subprocess.CompletedProcess:
    """Run the CLI as its own process, exactly as a pipe-driven caller would."""
    return subprocess.run(
        [sys.executable, "-B", "-m", "releasekit.cli", *args],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


class Repository(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cli contract ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "project"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Example Maintainer")
        git(self.root, "config", "user.email", "maintainer@example.invalid")
        git(self.root, "config", "commit.gpgSign", "false")
        git(self.root, "config", "core.hooksPath", str(self.root / ".git/hooks"))
        (self.root / "relkit.toml").write_text(POLICY, encoding="utf-8")
        (self.root / "README.md").write_text("# Example\n", encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text(VALID, encoding="utf-8", newline="")
        self.commit()

    def commit(self):
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "feat: fixture")

    def leak(self):
        """A tracked file that makes the policy audit fail, with invented names."""
        (self.root / "notes.md").write_text("see C:\\Users\\Someone\\project\n", encoding="utf-8")
        self.commit()


def last_line(text: str) -> str:
    return text.splitlines()[-1]
