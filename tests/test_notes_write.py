"""`notes --draft --write` composes the validated draft into the changelog for review."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from releasekit import cli, generation

ENTRY = (
    "## [1.1.0](https://example.invalid/compare/v1.0.0...v1.1.0) (2026-02-03)\n"
    "\n"
    "### Features\n"
    "\n"
    "- add widgets ([abc1234](https://example.invalid/commit/abc1234567))\n"
)
OLDER = (
    "## [1.0.0](https://example.invalid/releases/tag/v1.0.0) (2026-01-01)\n"
    "\n"
    "### Features\n"
    "\n"
    "- initial API ([def5678](https://example.invalid/commit/def5678901))\n"
)


class WriteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="notes write ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        body = f"import sys; sys.stdout.write({ENTRY!r})"
        (self.root / "relkit.toml").write_text(
            '[changelog]\nprofile = "conventional-changelog"\nfirst_version = "1.0.0"\n'
            "[changelog.generator]\ncommand = "
            + json.dumps([sys.executable, "-B", "-c", body])
            + "\n",
            encoding="utf-8",
        )
        self.changelog = self.root / "CHANGELOG.md"

    def notes(self, *arguments: str) -> tuple[int, dict, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(["--json", "notes", "1.1.0", "--root", str(self.root), *arguments])
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_the_entry_goes_after_the_title_and_unreleased_and_before_the_newest_entry(self):
        head = "# Changelog\n\nAll notable changes.\n\n## [Unreleased]\n\n- pending work\n\n"
        self.changelog.write_bytes((head + OLDER).encode("utf-8"))
        code, result, stderr = self.notes("--draft", "--write")
        self.assertEqual(0, code, stderr)
        self.assertEqual((head + ENTRY + "\n" + OLDER).encode("utf-8"), self.changelog.read_bytes())
        self.assertEqual(str(self.changelog), result["data"]["written"])
        self.assertIn(f"wrote the 1.1.0 entry into {self.changelog}", stderr)
        self.assertIn("Highlights", stderr)

    def test_crlf_changelog_keeps_its_newline_style(self):
        text = "# Changelog\r\n\r\n" + OLDER.replace("\n", "\r\n")
        self.changelog.write_bytes(text.encode("utf-8"))
        code, _, stderr = self.notes("--draft", "--write")
        self.assertEqual(0, code, stderr)
        written = self.changelog.read_bytes()
        expected = "# Changelog\r\n\r\n" + (ENTRY + "\n" + OLDER).replace("\n", "\r\n")
        self.assertEqual(expected.encode("utf-8"), written)
        self.assertEqual(written.count(b"\n"), written.count(b"\r\n"))

    def test_a_changelog_without_entries_gets_the_entry_at_its_end(self):
        self.changelog.write_bytes(b"# Changelog")
        code, _, stderr = self.notes("--draft", "--write")
        self.assertEqual(0, code, stderr)
        self.assertEqual(("# Changelog\n\n" + ENTRY).encode("utf-8"), self.changelog.read_bytes())

    def assert_refused(self, code: int, result: dict, error: str, before: bytes | None) -> None:
        self.assertEqual(2, code)
        self.assertEqual(error, result["errors"][0]["code"])
        if before is None:
            self.assertFalse(self.changelog.exists())
        else:
            self.assertEqual(before, self.changelog.read_bytes())

    def test_an_existing_entry_for_the_version_is_refused_and_the_file_untouched(self):
        before = ("# Changelog\n\n" + ENTRY + "\n" + OLDER).encode("utf-8")
        self.changelog.write_bytes(before)
        code, result, stderr = self.notes("--draft", "--write")
        self.assert_refused(code, result, "write_refused", before)
        self.assertIn("already has an entry for 1.1.0", stderr)

    def test_a_missing_changelog_is_refused_and_not_created(self):
        code, result, stderr = self.notes("--draft", "--write")
        self.assert_refused(code, result, "write_refused", None)
        self.assertIn("needs an existing changelog", stderr)

    def test_write_without_draft_is_refused_before_reading_anything(self):
        before = ("# Changelog\n\n" + OLDER).encode("utf-8")
        self.changelog.write_bytes(before)
        code, result, stderr = self.notes("--write")
        self.assert_refused(code, result, "generation_error", before)
        self.assertIn("--write is only valid with --draft", stderr)

    def test_a_composition_the_profile_would_not_find_is_refused(self):
        # An unclosed fence swallows everything after it, the inserted entry included.
        before = b"# Changelog\n\n```text\nan example left open\n"
        self.changelog.write_bytes(before)
        code, result, stderr = self.notes("--draft", "--write")
        self.assert_refused(code, result, "write_refused", before)
        self.assertIn("would still have no entry for 1.1.0", stderr)

    def test_a_draft_without_write_leaves_the_changelog_alone(self):
        before = ("# Changelog\n\n" + OLDER).encode("utf-8")
        self.changelog.write_bytes(before)
        code, result, _ = self.notes("--draft")
        self.assertEqual(0, code)
        self.assertIsNone(result["data"]["written"])
        self.assertEqual(before, self.changelog.read_bytes())


class ComposeTests(unittest.TestCase):
    def test_headings_inside_code_do_not_count_as_the_first_entry(self):
        changelog = "# Changelog\n\n```\n## [0.9.0]\n```\n\n" + OLDER
        composed = generation.compose(changelog, ENTRY)
        self.assertEqual(changelog.replace(OLDER, ENTRY + "\n" + OLDER), composed)

    def test_entry_newlines_follow_the_file_not_the_generator(self):
        composed = generation.compose("# Changelog\r\n", ENTRY.replace("\n", "\r\n") + "\r\n\r\n")
        self.assertEqual("# Changelog\r\n\r\n" + ENTRY.replace("\n", "\r\n"), composed)


if __name__ == "__main__":
    unittest.main()
