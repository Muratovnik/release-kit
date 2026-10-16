"""`--json` keeps stdout to exactly one JSON object, for every command family.

Run as real processes: descriptors and child processes are what a script reads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from cli_process import ROOT, Repository, git, relkit


class StrictJsonTests(Repository):
    """Under --json stdout is exactly one JSON object, for every command family."""

    def assert_single_object(self, *arguments):
        completed = relkit(self.root, *arguments)
        try:
            envelope = json.loads(completed.stdout)
        except ValueError:
            self.fail(
                f"stdout is not exactly one JSON object for {arguments}: {completed.stdout!r}"
            )
        self.assertIsInstance(envelope, dict)
        self.assertEqual(1, completed.stdout.count("\n"), completed.stdout)
        self.assertEqual(completed.returncode, envelope["exit_code"], completed.stderr)
        self.assertEqual(1, envelope["schema_version"])
        return envelope

    def test_every_subcommand_family_prints_one_object(self):
        (self.root / ".github").mkdir()
        (self.root / ".github/relkit.pyz").write_bytes(b"placeholder projection")
        git(self.root, "add", "-f", ".github/relkit.pyz")
        git(self.root, "commit", "-qm", "chore: pin a projection")
        for arguments in (
            ["--version", "--json"],
            ["audit", "--json", "--no-download"],
            ["audit", "--json", "--no-download", "--staged"],
            ["audit", "--json", "--no-download", "--history"],
            ["exposure", "--json"],
            ["notes", "v1.2.0", "--json"],
            ["notes", "v9.9.9", "--json"],
            ["notes", "v1.2.0", "--draft", "--json"],
            ["overlay", "--json"],
            ["protect", "check", "--json"],
            ["protect", "install", "--dry-run", "--json"],
            ["protect", "install", "--json", "--plan-hash", "0" * 64],
            ["update", "--json"],
            ["update", "--rollback", "--dry-run", "--json"],
            ["release", "next", "--json"],
            ["release", "plan", "v1.0.0", "--json"],
            ["release", "status", "v1.0.0", "--json"],
            ["release", "prepare", "v1.0.0", "--json"],
            ["release", "abandon", "v1.0.0", "--reason", "test", "--json"],
            ["--json", "release", "bogus"],
        ):
            with self.subTest(arguments=arguments):
                self.assert_single_object(*arguments)

    def test_protect_install_dry_run_is_one_object_with_the_plan(self):
        (self.root / ".github").mkdir()
        (self.root / ".github/relkit.pyz").write_bytes(b"placeholder projection")
        git(self.root, "add", "-f", ".github/relkit.pyz")
        git(self.root, "commit", "-qm", "chore: pin a projection")
        envelope = self.assert_single_object("protect", "install", "--dry-run", "--json")
        self.assertEqual(0, envelope["exit_code"])
        self.assertIn("plan_sha256", envelope["data"]["plan"])
        self.assertFalse((self.root / ".git/hooks/pre-push").exists())

    def test_descriptor_level_and_child_process_output_never_reaches_stdout(self):
        program = """
import os, subprocess, sys
from unittest.mock import patch
from releasekit import cli

def handler(arguments):
    print("python-stdout")
    os.write(1, b"descriptor-stdout\\n")
    subprocess.run([sys.executable, "-c", "print('child-stdout')"], check=True)
    return 0

with patch.object(cli, "_overlay", handler):
    raise SystemExit(cli.main(["overlay", "--json"]))
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", program],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("ok", json.loads(completed.stdout)["status"])
        for text in ("python-stdout", "descriptor-stdout", "child-stdout"):
            self.assertIn(text, completed.stderr)

    def test_stdout_is_restored_for_what_follows_the_command(self):
        program = """
import os, sys
from unittest.mock import patch
from releasekit import cli
with patch.object(cli, "_overlay", lambda arguments: 0):
    code = cli.main(["overlay", "--json"])
sys.stdout.flush()
os.write(1, b"after\\n")
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", program],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(completed.stdout.endswith("\nafter\n"), completed.stdout)


if __name__ == "__main__":
    unittest.main()
