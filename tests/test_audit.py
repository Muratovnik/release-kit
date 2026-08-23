from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from releasekit.exposure import audit, rules

LEAK = 'command = "python C:\\\\Users\\\\someone\\\\adapter.py"\n'


def _repository(files: dict[str, str]) -> tempfile.TemporaryDirectory[str]:
    """A real Git repository, because the scan reports on what Git would publish."""
    handle = tempfile.TemporaryDirectory()
    root = Path(handle.name)
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for arguments in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)
    return handle


class TrackedOnlyTests(unittest.TestCase):
    def test_an_untracked_file_is_not_this_gate_s_business(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            (Path(name) / "scratch.md").write_text(LEAK, encoding="utf-8")
            report = audit.scan(Path(name))

        self.assertTrue(report.ok, report.failures)


class RatchetTests(unittest.TestCase):
    def test_a_new_finding_fails(self) -> None:
        with _repository({"config.toml": LEAK}) as name:
            report = audit.scan(Path(name))

        self.assertFalse(report.ok)
        self.assertEqual(["config.toml: home-directory"], report.failures)

    def test_a_recorded_finding_passes_and_is_reported(self) -> None:
        with _repository({"config.toml": LEAK}) as name:
            report = audit.scan(Path(name), baseline={"config.toml": [rules.HOME_DIRECTORY]})

        self.assertTrue(report.ok, report.failures)
        self.assertEqual([audit.Finding("config.toml", rules.HOME_DIRECTORY)], report.baselined)

    def test_a_second_kind_in_a_recorded_file_still_fails(self) -> None:
        """Recording one finding is not a licence for the next one in the same file."""
        with _repository({"config.toml": LEAK + 'args = ["../neighbour/tool.ps1"]\n'}) as name:
            report = audit.scan(Path(name), baseline={"config.toml": [rules.HOME_DIRECTORY]})

        self.assertEqual(["config.toml: escapes-repository"], report.failures)

    def test_a_record_that_no_longer_matches_fails(self) -> None:
        with _repository({"config.toml": "clean = true\n"}) as name:
            report = audit.scan(Path(name), baseline={"config.toml": [rules.HOME_DIRECTORY]})

        self.assertFalse(report.ok)
        self.assertIn("no longer carries", report.failures[0])

    def test_the_baseline_can_only_shrink(self) -> None:
        """Fixing a finding forces the record to be updated in the same change."""
        with _repository({"config.toml": LEAK}) as name:
            root = Path(name)
            before = audit.scan(root, baseline={"config.toml": [rules.HOME_DIRECTORY]})
            (root / "config.toml").write_text("clean = true\n", encoding="utf-8")
            after = audit.scan(root, baseline={"config.toml": [rules.HOME_DIRECTORY]})
            settled = audit.scan(root, baseline={})

        self.assertTrue(before.ok)
        self.assertFalse(after.ok)
        self.assertTrue(settled.ok, settled.failures)


class DeclaredNameTests(unittest.TestCase):
    def test_names_come_from_the_caller(self) -> None:
        with _repository({"README.md": "built on Someservice\n"}) as name:
            without = audit.scan(Path(name))
            with_names = audit.scan(Path(name), names=("Someservice",))

        self.assertTrue(without.ok)
        self.assertEqual(["README.md: declared-name"], with_names.failures)

    def test_a_clone_without_the_list_does_not_fail_on_records_it_cannot_check(self) -> None:
        """A checkout that has no name list runs the structural rules and stays green."""
        with _repository({"README.md": "built on Someservice\n"}) as name:
            report = audit.scan(Path(name), baseline={"README.md": [rules.DECLARED_NAME]})

        self.assertTrue(report.ok, report.failures)


class ExclusionTests(unittest.TestCase):
    def test_an_excluded_path_is_not_scanned(self) -> None:
        """A repository must be able to hold a fixture of what its rules detect."""
        with _repository({"tests/fixture.toml": LEAK}) as name:
            report = audit.scan(Path(name), exclude=["tests/*"])

        self.assertTrue(report.ok, report.failures)
        self.assertEqual(["tests/fixture.toml"], report.excluded)

    def test_exclusion_does_not_leave_a_stale_baseline_behind(self) -> None:
        with _repository({"tests/fixture.toml": LEAK}) as name:
            report = audit.scan(
                Path(name),
                exclude=["tests/*"],
                baseline={"tests/fixture.toml": [rules.HOME_DIRECTORY]},
            )

        self.assertTrue(report.ok, report.failures)

    def test_a_path_outside_the_pattern_is_still_scanned(self) -> None:
        with _repository({"tests/fixture.toml": LEAK, "config.toml": LEAK}) as name:
            report = audit.scan(Path(name), exclude=["tests/*"])

        self.assertEqual(["config.toml: home-directory"], report.failures)


class PathTests(unittest.TestCase):
    def test_a_forbidden_extension_is_reported_even_when_unreadable_as_text(self) -> None:
        with _repository({"placeholder.md": "x\n"}) as name:
            root = Path(name)
            (root / "store.sqlite3").write_bytes(b"\x00\x01binary\xff")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
            report = audit.scan(root)

        self.assertEqual(["store.sqlite3: forbidden-kind"], report.failures)


if __name__ == "__main__":
    unittest.main()
