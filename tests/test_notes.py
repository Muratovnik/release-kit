from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from releasekit import cli
from releasekit.release.changelog import entry_for

ROOT = Path(__file__).resolve().parents[1]
VALID = (
    "# Changelog\n\n## [Unreleased]\n\n"
    "## [1.2.0](https://example.invalid/compare/v1.1.0...v1.2.0) (2026-01-05)\n\n"
    "### Highlights\n\nEdited by hand: café.\n\n### Features\n\n"
    "- a change ([abc1234](https://example.invalid/commit/abc1234567))\n"
)


class NotesTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "CHANGELOG.md"
        self.source.write_text(VALID, encoding="utf-8", newline="")
        self.policy = self.root / "relkit.toml"
        self.policy.write_text('[changelog]\nprofile = "vue-like"\n', encoding="utf-8")

    def run_notes(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["notes", "v1.2.0", "--root", str(self.root), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_stdout_and_file_export_are_the_validated_entry_not_a_regeneration(self) -> None:
        expected = entry_for(VALID, "v1.2.0", profile="vue-like") + "\n"
        code, stdout, stderr = self.run_notes()
        self.assertEqual((0, expected, ""), (code, stdout, stderr))
        code, stdout, stderr = self.run_notes("--output", "notes.md")
        self.assertEqual((0, "", ""), (code, stdout, stderr))
        self.assertEqual(expected.encode(), (self.root / "notes.md").read_bytes())

    def test_format_error_reports_file_line_and_never_creates_or_overwrites_output(self) -> None:
        self.source.write_text(
            VALID.replace(" ([abc1234](https://example.invalid/commit/abc1234567))", ""),
            encoding="utf-8",
        )
        output = self.root / "notes.md"
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    output.write_bytes(b"previous release\r\n")
                code, stdout, stderr = self.run_notes("--output", "notes.md")
                self.assertEqual((1, ""), (code, stdout))
                self.assertIn(f"{self.source}:13:", stderr)
                self.assertIn("commit link", stderr)
                if existing:
                    self.assertEqual(b"previous release\r\n", output.read_bytes())
                else:
                    self.assertFalse(output.exists())

    def test_strict_option_is_format_neutral_but_cannot_weaken_configured_profile(self) -> None:
        self.source.write_text("## [1.2.0]\nHand-written notes.\n", encoding="utf-8")
        self.assertEqual(1, self.run_notes("--strict")[0])
        self.policy.unlink()
        self.assertEqual(0, self.run_notes("--strict")[0])
        self.source.write_text("## [1.2.0]\n", encoding="utf-8")
        self.assertEqual(0, self.run_notes()[0])
        self.assertEqual(1, self.run_notes("--strict")[0])
        self.source.write_text("## [1.2.0]\nOne.\n## [1.2.0]\nTwo.\n", encoding="utf-8")
        self.assertEqual(0, self.run_notes()[0])
        self.assertEqual(1, self.run_notes("--strict")[0])

    def test_cliff_config_is_not_implicit_policy_and_no_git_repository_is_required(self) -> None:
        self.policy.unlink()
        (self.root / "cliff.toml").write_text('[changelog]\nbody = "unused"\n', encoding="utf-8")
        self.source.write_text("## [1.2.0]\n", encoding="utf-8")
        self.assertEqual(0, self.run_notes()[0])

    def test_absent_version_has_a_diagnostic_and_no_output(self) -> None:
        self.source.write_text("## [1.1.0]\nOlder.\n", encoding="utf-8")
        code, stdout, stderr = self.run_notes("--output", "notes.md")
        self.assertEqual((1, ""), (code, stdout))
        self.assertIn(f"{self.source}:1: no entry", stderr)
        self.assertFalse((self.root / "notes.md").exists())

    def test_config_and_read_errors_return_operational_status_without_output(self) -> None:
        for payload in (b'[changelog]\nprofile = "typo"', b"[changelog", b"\xff"):
            with self.subTest(payload=payload):
                self.policy.write_bytes(payload)
                code, stdout, stderr = self.run_notes("--output", "notes.md")
                self.assertEqual((2, ""), (code, stdout))
                self.assertIn("relkit.toml", stderr)
                self.assertFalse((self.root / "notes.md").exists())
        self.policy.unlink()
        self.source.write_bytes(b"\xff")
        self.assertEqual(2, self.run_notes()[0])
        self.source.unlink()
        self.assertEqual(2, self.run_notes()[0])

    def test_output_errors_preserve_existing_notes_and_remove_temporary_file(self) -> None:
        output = self.root / "notes.md"
        output.write_bytes(b"previous")
        with mock.patch.object(Path, "replace", side_effect=OSError("replacement refused")):
            code, stdout, stderr = self.run_notes("--output", "notes.md")
        self.assertEqual((2, ""), (code, stdout))
        self.assertIn("replacement refused", stderr)
        self.assertEqual(b"previous", output.read_bytes())
        self.assertEqual([], list(self.root.glob("*.tmp")))
        self.assertEqual(2, self.run_notes("--output", "absent/notes.md")[0])

    def test_output_must_not_replace_source_policy_or_a_hardlink_to_source(self) -> None:
        for name in ("CHANGELOG.md", "relkit.toml"):
            with self.subTest(name=name):
                original = (self.root / name).read_bytes()
                self.assertEqual(2, self.run_notes("--output", name)[0])
                self.assertEqual(original, (self.root / name).read_bytes())
        os.link(self.source, self.root / "alias.md")
        self.assertEqual(2, self.run_notes("--output", "alias.md")[0])

    def test_crlf_and_markdown_hard_breaks_are_preserved_inside_export(self) -> None:
        text = VALID.replace("Edited by hand: café.", "Edited by hand: café.  ").replace(
            "\n", "\r\n"
        )
        self.source.write_bytes(text.encode())
        self.assertEqual(0, self.run_notes("--output", "notes.md")[0])
        expected = entry_for(text, "1.2.0", profile="vue-like") + "\n"
        self.assertEqual(expected.encode(), (self.root / "notes.md").read_bytes())

    def test_zipapp_uses_the_same_configured_validation_and_export(self) -> None:
        artifact = self.root / "relkit.pyz"
        subprocess.run(
            [sys.executable, str(ROOT / "tools/build_zipapp.py"), str(artifact)],
            check=True,
            capture_output=True,
        )
        command = [sys.executable, str(artifact), "notes", "v1.2.0", "--output", "notes.md"]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        expected = (entry_for(VALID, "1.2.0", profile="vue-like") + "\n").encode()
        self.assertEqual(expected, (self.root / "notes.md").read_bytes())
        stdout_result = subprocess.run(
            command[:-2], cwd=self.root, capture_output=True, check=False
        )
        self.assertEqual(0, stdout_result.returncode, stdout_result.stderr)
        self.assertEqual(expected, stdout_result.stdout)
        self.source.write_text("## [1.2.0]\n", encoding="utf-8")
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, check=False)
        self.assertEqual(1, result.returncode)
        self.assertIn("empty entry", result.stderr)
        self.assertEqual(expected, (self.root / "notes.md").read_bytes())


if __name__ == "__main__":
    unittest.main()
