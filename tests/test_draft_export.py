"""Draft export must never replace the source changelog or its aliases."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import cli, processes


class DraftOutputSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="draft export ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.draft = "## [1.2.0]\n\nA reviewed draft.\n"
        self.original = b"# Changelog\r\n\r\n## [1.1.0]\r\n\r\nKeep the old release.\r\n"
        (self.root / "generator.py").write_text(
            "import sys\nsys.stdout.buffer.write(" + repr(self.draft.encode("utf-8")) + ")\n",
            encoding="utf-8",
        )
        (self.root / "relkit.toml").write_text(
            '[changelog]\nprofile = "strict"\n[changelog.generator]\ncommand = '
            + json.dumps([sys.executable, "generator.py"])
            + "\n",
            encoding="utf-8",
        )

    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(
                ["notes", "1.2.0", "--root", str(self.root), "--draft", "--json", *arguments]
            )
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_draft_output_cannot_replace_default_or_custom_changelog(self):
        for name in ("CHANGELOG.md", "release history.md"):
            with self.subTest(changelog=name):
                source = self.root / name
                source.write_bytes(self.original)
                code, result, _ = self.invoke("--changelog", name, "--output", name)
                self.assertEqual(2, code)
                self.assertEqual("io_error", result["errors"][0]["code"])
                self.assertEqual(self.original, source.read_bytes())
                self.assertEqual([], list(self.root.glob("*.tmp")))

    def check_alias(self, kind):
        source = self.root / "CHANGELOG.md"
        source.write_bytes(self.original)
        alias = self.root / (kind + ".md")
        try:
            if kind == "hardlink":
                os.link(source, alias)
            else:
                alias.symlink_to(source)
        except OSError:
            self.skipTest(f"creating a {kind} is not permitted on this host")
        code, result, _ = self.invoke("--output", alias.name)
        self.assertEqual(2, code)
        self.assertEqual("io_error", result["errors"][0]["code"])
        self.assertEqual(self.original, source.read_bytes())
        self.assertEqual(self.original, alias.read_bytes())
        if kind == "symlink":
            self.assertTrue(alias.is_symlink())

    def test_draft_output_cannot_replace_hardlink_to_changelog(self):
        self.check_alias("hardlink")

    def test_draft_output_cannot_replace_symlink_to_changelog(self):
        self.check_alias("symlink")

    def test_separate_output_receives_draft_and_preserves_history_and_policy(self):
        source = self.root / "CHANGELOG.md"
        source.write_bytes(self.original)
        policy = (self.root / "relkit.toml").read_bytes()
        code, result, stderr = self.invoke("--output", "release notes.md")
        self.assertEqual(0, code, stderr)
        self.assertEqual("ok", result["status"])
        self.assertEqual(self.draft.encode(), (self.root / "release notes.md").read_bytes())
        self.assertEqual(self.original, source.read_bytes())
        self.assertEqual(policy, (self.root / "relkit.toml").read_bytes())

    def test_unknown_generation_cleanup_remains_an_explicit_structured_refusal(self):
        source = self.root / "CHANGELOG.md"
        source.write_bytes(self.original)
        with patch.object(
            processes, "run", side_effect=processes.CleanupError("owned worker still unconfirmed")
        ):
            code, result, _ = self.invoke("--output", "release notes.md")
        self.assertEqual(2, code)
        self.assertEqual("generation_cleanup_unconfirmed", result["errors"][0]["code"])
        self.assertFalse((self.root / "release notes.md").exists())
        self.assertEqual(self.original, source.read_bytes())


if __name__ == "__main__":
    unittest.main()
