from __future__ import annotations

import re
import unittest
from pathlib import Path

from releasekit import config
from releasekit.release import changelog

ROOT = Path(__file__).resolve().parents[1]

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
        self.assertEqual(
            changelog.entry_for(SAMPLE, "v1.2.0"), changelog.entry_for(SAMPLE, "1.2.0")
        )

    def test_an_unlinked_heading_is_found_too(self) -> None:
        self.assertIsNotNone(changelog.entry_for(SAMPLE, "1.1.0"))

    def test_a_version_the_changelog_never_mentions_is_absent(self) -> None:
        self.assertIsNone(changelog.entry_for(SAMPLE, "9.9.9"))

    def test_a_prefix_of_a_version_does_not_match_it(self) -> None:
        self.assertIsNone(changelog.entry_for(SAMPLE, "1.2"))

    def test_a_name_that_is_not_a_version_keeps_its_leading_letter(self) -> None:
        self.assertEqual("valentine", changelog.normalize("valentine"))


class StrictEntryTests(unittest.TestCase):
    def test_legacy_still_accepts_heading_only_and_selects_first_duplicate(self) -> None:
        self.assertEqual("## [1.2.0]", changelog.entry_for("## [1.2.0]\n", "1.2.0"))
        self.assertEqual(
            "## [1.2.0]\n\nFirst.",
            changelog.entry_for("## [1.2.0]\n\nFirst.\n\n## [1.2.0]\nSecond.", "1.2.0"),
        )

    def test_heading_only_or_structurally_empty_release_is_rejected(self) -> None:
        for body in ("", "\n### Features\n", "\n<!-- pending -->\n", "\n- \n"):
            with self.subTest(body=body), self.assertRaisesRegex(changelog.ChangelogError, "empty"):
                changelog.entry_for("## [1.2.0]\n" + body, "1.2.0", profile="strict")

    def test_duplicate_versions_including_v_prefix_are_rejected_at_second_heading(self) -> None:
        for version in ("1.2.0", "v1.2.0", "V1.2.0"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(changelog.ChangelogError, "duplicate") as caught:
                    changelog.entry_for(
                        f"# Changelog\n\n## [1.2.0]\nFirst.\n\n## [{version}]\nSecond.",
                        "v1.2.0",
                        profile="strict",
                    )
                self.assertEqual(6, caught.exception.line)

    def test_strict_remains_format_neutral(self) -> None:
        text = "## [1.2.0] - 2026-01-05\n\nHuman-curated notes.\n"
        self.assertEqual(text.rstrip(), changelog.entry_for(text, "1.2.0", profile="strict"))

    def test_code_and_comments_cannot_create_or_end_release_entries(self) -> None:
        text = (
            "<!--\n## [1.2.0]\nFake.\n-->\n"
            "```md\n## [1.2.0]\nFake.\n```\n"
            "## [1.2.0]\nReal.\n```md\n## [1.2.0]\nExample.\n```\n"
        )
        self.assertEqual(
            "## [1.2.0]\nReal.\n```md\n## [1.2.0]\nExample.\n```",
            changelog.entry_for(text, "1.2.0", profile="strict"),
        )

    def test_comment_examples_inside_code_cannot_hide_the_next_release(self) -> None:
        for example in (
            "```html\n<!--\n```",
            "~~~html\n<!--\n~~~~",
            "`<!--`",
            "`multiline\n<!--\nexample`",
            "    <!--",
            "<!-- ` --> visible text `",
        ):
            with self.subTest(example=example):
                selected = "## [1.2.0]\n\nMigration example:\n\n" + example
                text = selected + "\n\n## [1.1.0]\n\nOlder release.\n"
                self.assertEqual(selected, changelog.entry_for(text, "1.2.0", profile="strict"))


class VueLikeTests(unittest.TestCase):
    HEADER = "## [1.2.0](https://example.invalid/compare/v1.1.0...v1.2.0) (2026-01-05)"
    CHANGE = "- a useful change ([abc1234](https://example.invalid/commit/abc1234567))"

    def notes(self, body: str, *, header: str | None = None) -> str:
        return (header or self.HEADER) + "\n\n" + body + "\n"

    def test_curated_entry_keeps_highlights_grouped_commits_and_breaking_explanation(self) -> None:
        text = self.notes(
            "### Highlights\n\nA hand-edited introduction.\n\n"
            "- A summary without a commit link.\n\n"
            "### Features\n\n" + self.CHANGE + "\n"
            "  and a related fix ([def5678](https://example.invalid/commit/def56789)).\n\n"
            "### BREAKING CHANGES\n\nMigration instructions stay verbatim.\n\n"
            "- Replace the old setting.\n\n```toml\nnew_setting = true\n```"
        )
        self.assertEqual(
            text.rstrip(), changelog.entry_for(text, "v1.2.0", profile="conventional-changelog")
        )

    def test_manually_written_first_release_without_commit_links_is_rejected(self) -> None:
        text = self.notes(
            "### Highlights\n\nThe first release.\n\n### Features\n\n"
            "- Compose reusable libraries and export files.\n"
            "- Install self-contained release archives.",
            header="## [1.2.0](https://example.invalid/releases/tag/v1.2.0) (2026-01-05)",
        )
        with self.assertRaisesRegex(changelog.ChangelogError, "commit link") as caught:
            changelog.entry_for(
                text, "1.2.0", profile="conventional-changelog", first_version="1.2.0"
            )
        self.assertEqual(9, caught.exception.line)

    def test_first_release_requires_explicit_version_not_missing_older_entries(self) -> None:
        for link in ("", "(https://example.invalid/releases/tag/v1.2.0)"):
            with self.subTest(link=link):
                text = self.notes(
                    "### Features\n\n" + self.CHANGE,
                    header=f"## [1.2.0]{link} (2026-01-05)",
                )
                self.assertIsNotNone(
                    changelog.entry_for(
                        text, "1.2.0", profile="conventional-changelog", first_version="v1.2.0"
                    )
                )
                with self.assertRaisesRegex(changelog.ChangelogError, "compare"):
                    changelog.entry_for(text, "1.2.0", profile="conventional-changelog")
                with self.assertRaises(changelog.ChangelogError):
                    changelog.entry_for(
                        text, "1.2.0", profile="conventional-changelog", first_version="1.0.0"
                    )

    def test_scope_and_pull_request_are_optional_and_both_bullet_styles_work(self) -> None:
        for bullet in ("-", "*", "+"):
            for scope in ("", "**core:** "):
                with self.subTest(bullet=bullet, scope=scope):
                    text = self.notes(
                        "### Bug Fixes\n\n" + self.CHANGE.replace("- ", bullet + " " + scope)
                    )
                    self.assertIsNotNone(
                        changelog.entry_for(text, "1.2.0", profile="conventional-changelog")
                    )

    def test_closed_sections_but_both_breaking_spellings_are_supported(self) -> None:
        for section in (
            "Features",
            "Bug Fixes",
            "Performance Improvements",
            "Reverts",
            "BREAKING CHANGES",
            "Breaking Changes",
            "Highlights",
        ):
            with self.subTest(section=section):
                self.assertIsNotNone(
                    changelog.entry_for(
                        self.notes(f"### {section}\n\n{self.CHANGE}"),
                        "1.2.0",
                        profile="conventional-changelog",
                    )
                )
        with self.assertRaisesRegex(changelog.ChangelogError, "section"):
            changelog.entry_for(
                self.notes("### Miscellaneous\n\n" + self.CHANGE),
                "1.2.0",
                profile="conventional-changelog",
            )

    def test_missing_sections_and_changes_outside_sections_are_rejected(self) -> None:
        for body in (
            self.CHANGE,
            "An unstructured release.",
            "### Features\n\nPlain prose.",
            "### Features\n\n  " + self.CHANGE,
        ):
            with self.subTest(body=body), self.assertRaises(changelog.ChangelogError):
                changelog.entry_for(self.notes(body), "1.2.0", profile="conventional-changelog")

    def test_invalid_dates_and_compare_targets_are_rejected(self) -> None:
        for header in (
            self.HEADER.replace("2026-01-05", "2026-02-30"),
            self.HEADER.replace("2026-01-05", "20260105"),
            self.HEADER.replace(" (2026-01-05)", ""),
            self.HEADER.replace("...v1.2.0", "...v1.3.0"),
            self.HEADER.replace("v1.1.0...", "v1.2.0..."),
            self.HEADER.replace("https://example.invalid/compare/", "not-a-url/compare/"),
            self.HEADER.replace("v1.1.0...", ""),
        ):
            with self.subTest(header=header), self.assertRaises(changelog.ChangelogError) as caught:
                changelog.entry_for(
                    self.notes("### Features\n\n" + self.CHANGE, header=header),
                    "1.2.0",
                    profile="conventional-changelog",
                )
            self.assertEqual(1, caught.exception.line)

    def test_commit_evidence_must_be_a_visible_commit_link_on_each_change(self) -> None:
        for evidence in (
            "",
            "([#42](https://example.invalid/pull/42))",
            "([abc1234](https://example.invalid/commit/def5678))",
            "([abc1234](file:///commit/abc1234))",
            "`([abc1234](https://example.invalid/commit/abc1234))`",
            "<!-- ([abc1234](https://example.invalid/commit/abc1234)) -->",
            "\n```md\n([abc1234](https://example.invalid/commit/abc1234))\n```",
            "\n  - nested detail ([abc1234](https://example.invalid/commit/abc1234))",
            "\n  - nested detail\n  ([abc1234](https://example.invalid/commit/abc1234))",
            "\\[abc1234](https://example.invalid/commit/abc1234)",
            "![abc1234](https://example.invalid/commit/abc1234)",
            "`code starts here\n([abc1234](https://example.invalid/commit/abc1234))\nends here`",
        ):
            with (
                self.subTest(evidence=evidence),
                self.assertRaisesRegex(changelog.ChangelogError, "commit link"),
            ):
                changelog.entry_for(
                    self.notes("### Features\n\n- missing trace " + evidence),
                    "1.2.0",
                    profile="conventional-changelog",
                )
        with self.assertRaisesRegex(changelog.ChangelogError, "commit link"):
            changelog.entry_for(
                self.notes("### Features\n\n" + self.CHANGE + "\n- unlinked second change"),
                "1.2.0",
                profile="conventional-changelog",
            )

    def test_commit_link_wrapped_to_a_continuation_line_is_rejected(self) -> None:
        text = self.notes(
            "### Features\n\n- a useful change\n  ([abc1234](https://example.invalid/commit/abc1234567))"
        )
        with self.assertRaisesRegex(changelog.ChangelogError, "top-level bullet line") as caught:
            changelog.entry_for(text, "1.2.0", profile="conventional-changelog")
        self.assertEqual(5, caught.exception.line)

    def test_release_candidate_versions_keep_inline_commit_links(self) -> None:
        text = self.notes("### Features\n\n" + self.CHANGE)
        text = text.replace("1.2.0", "1.2.0-rc.1")
        self.assertIsNotNone(
            changelog.entry_for(text, "v1.2.0-rc.1", profile="conventional-changelog")
        )


if __name__ == "__main__":
    unittest.main()


class OwnChangelogTests(unittest.TestCase):
    """The profile validates only the entry being released, so nothing else checked these.

    A rewrite gave every historical heading a compare link between commit ids, which
    reads fine and resolves on the host but is not what the layout accepts. The release
    passed because its own entry was well formed; `notes` for any older version failed.
    """

    def setUp(self):
        self.settings = config.load(ROOT)
        self.changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.versions = re.findall(
            r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]", self.changelog, re.MULTILINE
        )

    def test_every_entry_satisfies_the_declared_profile(self):
        self.assertTrue(self.versions)
        for version in self.versions:
            with self.subTest(version=version):
                entry = changelog.entry_for(
                    self.changelog,
                    version,
                    profile=self.settings.changelog.profile,
                    first_version=self.settings.changelog.first_version,
                )
                self.assertIsNotNone(entry)

    def test_the_declared_first_version_is_the_oldest_entry(self):
        self.assertEqual(self.settings.changelog.first_version, self.versions[-1])
