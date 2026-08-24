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


def _commit(root: Path, message: str = "test: fixture") -> None:
    for key, value in (
        ("user.name", "Example Writer"),
        ("user.email", "writer@example.invalid"),
    ):
        subprocess.run(["git", "config", key, value], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)


class ScopeTests(unittest.TestCase):
    def test_an_unstaged_deletion_is_absent_from_the_worktree_boundary(self) -> None:
        with _repository({"gone.md": LEAK}) as name:
            (Path(name) / "gone.md").unlink()
            report = audit.scan(Path(name))

        self.assertTrue(report.ok, report.failures)
        self.assertEqual([], report.unreadable)

    def test_the_same_deletion_does_not_change_the_staged_boundary(self) -> None:
        with _repository({"gone.md": LEAK}) as name:
            (Path(name) / "gone.md").unlink()
            report = audit.scan(Path(name), staged=True, include_candidates=False)

        self.assertEqual(["gone.md: home-directory"], report.failures)

    def test_an_untracked_unignored_file_is_this_gate_s_business(self) -> None:
        """Uncommitted is not safe. It is one `git add -A` from the history."""
        with _repository({"kept.md": "clean\n"}) as name:
            (Path(name) / "scratch.md").write_text(LEAK, encoding="utf-8")
            report = audit.scan(Path(name))

        self.assertEqual(["scratch.md: home-directory"], report.failures)

    def test_an_ignored_file_is_not(self) -> None:
        with _repository({"kept.md": "clean\n", ".gitignore": "scratch.md\n"}) as name:
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


class PrivateValueTests(unittest.TestCase):
    def test_names_come_from_the_caller(self) -> None:
        with _repository({"README.md": "built on Someservice\n"}) as name:
            without = audit.scan(Path(name))
            with_names = audit.scan(Path(name), names=("Someservice",))

        self.assertTrue(without.ok)
        self.assertEqual(["README.md: private-value"], with_names.failures)

    def test_names_are_case_insensitive_and_cover_paths(self) -> None:
        with _repository({"internalservice/README.md": "clean\n"}) as name:
            report = audit.scan(Path(name), names=("InternalService",))

        self.assertEqual(["internalservice/README.md: private-value"], report.failures)

    def test_provider_contract_is_public_but_owner_data_is_private(self) -> None:
        with _repository(
            {
                "provider.md": "AgentMemory supplies opaque memory references.\n",
                "example.md": "namespace = owner/private-workflow\n",
            }
        ) as name:
            report = audit.scan(Path(name), names=("owner/private-workflow",))

        self.assertEqual(["example.md: private-value"], report.failures)

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

    def test_staged_scan_reads_the_index_not_a_cleaner_worktree_copy(self) -> None:
        with _repository({"config.toml": LEAK}) as name:
            root = Path(name)
            (root / "config.toml").write_text("clean = true\n", encoding="utf-8")
            report = audit.scan(root, staged=True)

        self.assertEqual(["config.toml: home-directory"], report.failures)

    def test_png_metadata_can_be_forbidden_once_for_all_fixtures(self) -> None:
        with _repository({"placeholder.md": "x\n"}) as name:
            root = Path(name)
            payload = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00tEXt" + b"\x00\x00\x00\x00"
            (root / "fixture.png").write_bytes(payload)
            subprocess.run(["git", "add", "fixture.png"], cwd=root, check=True)
            report = audit.scan(root, forbid_png_metadata=True, staged=True)

        self.assertEqual(["fixture.png: png-metadata"], report.failures)


class HistoryTests(unittest.TestCase):
    def test_history_checks_commit_messages_for_declared_names(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root, "docs: explain InternalService workflow")
            failures = audit.history_failures(root, names=("InternalService",))

        self.assertTrue(any("commit-message: private-value" in item for item in failures))

    def test_history_commit_messages_may_describe_a_traversal_fixture(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root, "test: reject an ../ traversal attempt")
            failures = audit.history_failures(root)

        self.assertFalse(any("commit-message" in item for item in failures), failures)

    def test_history_scope_ignores_synthetic_client_checkpoint_refs(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(["git", "switch", "-q", "-c", "client-checkpoint"], cwd=root, check=True)
            private = root / ".someclient" / "settings.json"
            private.parent.mkdir()
            private.write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            _commit(root)
            checkpoint = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(["git", "switch", "-q", branch], cwd=root, check=True)
            subprocess.run(
                ["git", "branch", "-D", "client-checkpoint"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "update-ref", "refs/codex/checkpoints/test", checkpoint],
                cwd=root,
                check=True,
            )

            failures = audit.history_failures(root, private_paths=[".someclient"])

        self.assertEqual([], failures)

    def test_history_verdict_can_require_a_clean_worktree(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            self.assertEqual((), audit.worktree_changes(root))
            (root / "kept.md").write_text("changed\n", encoding="utf-8")

            changes = audit.worktree_changes(root)

        self.assertEqual(1, len(changes))
        self.assertIn("kept.md", changes[0])

    def test_history_checks_paths_content_and_identities(self) -> None:
        with _repository(
            {
                ".someclient/settings.json": "{}\n",
                "config.toml": LEAK,
            }
        ) as name:
            root = Path(name)
            _commit(root)
            failures = audit.history_failures(
                root,
                private_paths=[".someclient"],
                allowed_identities=["Somebody Else <else@example.invalid>"],
            )

        self.assertTrue(any("private-path" in failure for failure in failures), failures)
        self.assertTrue(any("home-directory" in failure for failure in failures), failures)
        self.assertTrue(any("identity is not allowed" in failure for failure in failures), failures)

    def test_history_exclusions_are_for_deliberate_rule_fixtures(self) -> None:
        with _repository({"tests/fixture.toml": LEAK}) as name:
            root = Path(name)
            _commit(root)
            failures = audit.history_failures(root, exclude=["tests/*"])

        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
