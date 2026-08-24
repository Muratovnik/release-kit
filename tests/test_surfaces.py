"""Surface-level rules: what may not be tracked at all, and what must be ignored."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from releasekit.exposure import audit, rules


def _repository(files: dict[str, str], *, ignore: str = "") -> tempfile.TemporaryDirectory[str]:
    handle = tempfile.TemporaryDirectory()
    root = Path(handle.name)
    if ignore:
        (root / ".gitignore").write_text(ignore, encoding="utf-8")
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    return handle


class PrivatePathTests(unittest.TestCase):
    def test_a_file_under_a_private_surface_is_a_finding_whatever_it_contains(self) -> None:
        with _repository({".someclient/settings.json": "{}\n"}) as name:
            report = audit.scan(Path(name), private_paths=[".someclient/"])

        self.assertEqual([".someclient/settings.json: private-path"], report.failures)

    def test_the_surface_covers_files_added_later(self) -> None:
        """The reason a surface is declared rather than each file judged on content."""
        with _repository({".someclient/a.json": "{}\n", ".someclient/nested/b.md": "hi\n"}) as name:
            report = audit.scan(Path(name), private_paths=[".someclient"])

        self.assertEqual(2, len(report.new))

    def test_an_exact_private_file_is_a_finding(self) -> None:
        with _repository({".env": "TOKEN=x\n", "keep.md": "x\n"}) as name:
            report = audit.scan(Path(name), private_files=[".env"])

        self.assertEqual([".env: private-path"], report.failures)

    def test_a_private_suffix_is_a_finding(self) -> None:
        with _repository({"AGENTS.local.md": "internal\n"}) as name:
            report = audit.scan(Path(name), private_suffixes=[".local.md"])

        self.assertEqual(["AGENTS.local.md: private-path"], report.failures)

    def test_a_similarly_named_sibling_is_not_swept_in(self) -> None:
        with _repository({".someclientele/notes.md": "x\n"}) as name:
            report = audit.scan(Path(name), private_paths=[".someclient"])

        self.assertTrue(report.ok, report.failures)

    def test_owner_policy_files_are_private_without_project_configuration(self) -> None:
        for relative in (".publication-owner.toml", ".publication-private-values"):
            with self.subTest(relative=relative), _repository({relative: "private\n"}) as name:
                report = audit.scan(Path(name))

            self.assertEqual([f"{relative}: private-path"], report.failures)


class RequiredIgnoreTests(unittest.TestCase):
    def test_a_surface_that_is_not_ignored_is_a_finding(self) -> None:
        with _repository({"keep.md": "x\n"}) as name:
            report = audit.scan(Path(name), required_ignores=[".someclient"])

        self.assertEqual([".someclient: not-ignored"], report.failures)

    def test_an_ignored_surface_passes(self) -> None:
        with _repository({"keep.md": "x\n"}, ignore="/.someclient\n") as name:
            report = audit.scan(Path(name), required_ignores=[".someclient"])

        self.assertTrue(report.ok, report.failures)

    def test_a_tracked_surface_is_still_answered_against_the_rules(self) -> None:
        """Mid-migration the surface is both tracked and ignored; --no-index is why."""
        with _repository({".someclient/x.json": "{}\n"}, ignore="/.someclient\n") as name:
            report = audit.scan(Path(name), required_ignores=[".someclient"])

        self.assertNotIn(".someclient: not-ignored", report.failures)


class CandidateTests(unittest.TestCase):
    def test_an_untracked_unignored_file_is_scanned(self) -> None:
        """Not committed is not the same as safe: it is one `git add -A` away."""
        with _repository({"keep.md": "x\n"}) as name:
            (Path(name) / "scratch.local.md").write_text("internal\n", encoding="utf-8")
            report = audit.scan(Path(name), private_suffixes=[".local.md"])

        self.assertEqual(["scratch.local.md: private-path"], report.failures)

    def test_an_ignored_file_is_not(self) -> None:
        with _repository({"keep.md": "x\n"}, ignore="*.local.md\n") as name:
            (Path(name) / "scratch.local.md").write_text("internal\n", encoding="utf-8")
            report = audit.scan(Path(name), private_suffixes=[".local.md"])

        self.assertTrue(report.ok, report.failures)

    def test_candidates_can_be_switched_off(self) -> None:
        with _repository({"keep.md": "x\n"}) as name:
            (Path(name) / "scratch.local.md").write_text("internal\n", encoding="utf-8")
            report = audit.scan(
                Path(name), private_suffixes=[".local.md"], include_candidates=False
            )

        self.assertTrue(report.ok, report.failures)


class KindNameTests(unittest.TestCase):
    def test_the_reported_kinds_are_the_documented_ones(self) -> None:
        """These strings are the public contract: they appear in every baseline."""
        self.assertEqual(
            {
                "home-directory",
                "escapes-repository",
                "forbidden-kind",
                "private-value",
                "private-path",
                "not-ignored",
                "png-metadata",
                "owner-workflow",
                "personal-data",
                "internal-planning",
                "ai-attribution",
                "machine-observation",
                "provider-surface",
                "provenance-missing",
                "provenance-conflict",
                "machine-derived",
                "archive-path",
                "archive-limit",
            },
            {
                rules.HOME_DIRECTORY,
                rules.ESCAPES_REPOSITORY,
                rules.FORBIDDEN_KIND,
                rules.DECLARED_NAME,
                rules.PRIVATE_PATH,
                rules.NOT_IGNORED,
                audit.PNG_METADATA,
                rules.OWNER_WORKFLOW,
                rules.PERSONAL_DATA,
                rules.INTERNAL_PLANNING,
                rules.AI_ATTRIBUTION,
                rules.MACHINE_OBSERVATION,
                rules.PROVIDER_SURFACE,
                audit.PROVENANCE_MISSING,
                audit.PROVENANCE_CONFLICT,
                audit.MACHINE_DERIVED,
                audit.ARCHIVE_PATH,
                audit.ARCHIVE_LIMIT,
            },
        )


if __name__ == "__main__":
    unittest.main()
