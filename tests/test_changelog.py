from __future__ import annotations

import unittest

from releasekit.release import changelog

SAMPLE = """# Changelog

Preamble that belongs to no version.

## [1.2.0](https://example.invalid/compare/v1.1.0...v1.2.0) (2026-01-05)

Highlights written by a person.

### Features

- something useful ([abc1234](https://example.invalid/commit/abc1234))

## [1.1.0] (2025-12-01)

### Bug Fixes

- something repaired
"""


class EntryTests(unittest.TestCase):
    def test_it_returns_one_version_and_stops_at_the_next(self) -> None:
        entry = changelog.entry_for(SAMPLE, "1.2.0")

        assert entry is not None
        self.assertTrue(entry.startswith("## [1.2.0]"))
        self.assertIn("Highlights written by a person.", entry)
        # The compare link legitimately names the previous version; the next entry's
        # heading is what must not be swept in.
        self.assertNotIn("## [1.1.0]", entry)

    def test_the_last_entry_runs_to_the_end(self) -> None:
        entry = changelog.entry_for(SAMPLE, "1.1.0")

        assert entry is not None
        self.assertIn("something repaired", entry)

    def test_a_tag_and_a_heading_differ_only_by_the_v(self) -> None:
        self.assertEqual(changelog.entry_for(SAMPLE, "v1.2.0"), changelog.entry_for(SAMPLE, "1.2.0"))

    def test_an_unlinked_heading_is_found_too(self) -> None:
        self.assertIsNotNone(changelog.entry_for(SAMPLE, "1.1.0"))

    def test_a_version_the_changelog_never_mentions_is_absent(self) -> None:
        self.assertIsNone(changelog.entry_for(SAMPLE, "9.9.9"))

    def test_a_prefix_of_a_version_does_not_match_it(self) -> None:
        self.assertIsNone(changelog.entry_for(SAMPLE, "1.2"))

    def test_a_name_that_is_not_a_version_keeps_its_leading_letter(self) -> None:
        self.assertEqual("valentine", changelog.normalize("valentine"))


if __name__ == "__main__":
    unittest.main()
