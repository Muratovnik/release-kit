"""A refusal names the path or ref that caused it and one fix that fits where it is said.

Each case provokes a real refusal and checks that the exact cause is in the message,
together with a command that applies to that context (not, for instance, a hint about
staged content in a command that has no staged scope).
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from cli_process import Repository, git, relkit
from test_release import ReleaseFixture

from releasekit import protection, update
from releasekit.release import local
from releasekit.release.backend import ReleaseError, clean, remote_identity
from releasekit.release.directory import Directory


class ReleaseRefusalTests(ReleaseFixture):
    def refusal(self, call, *args):
        with self.assertRaises(ReleaseError) as raised:
            call(*args)
        return str(raised.exception)

    def test_a_dirty_tree_lists_the_files_that_make_it_dirty(self):
        (self.root / "scratch.txt").write_text("x", encoding="utf-8")
        (self.root / "VERSION").write_text("2.0.0\n", encoding="utf-8")
        message = self.refusal(clean, self.runner)
        self.assertIn("2 change(s)", message)
        self.assertIn("scratch.txt", message)
        self.assertIn("VERSION", message)
        self.assertIn(str(self.root), message)

    def test_a_long_dirty_list_is_capped_and_says_how_many_more(self):
        for index in range(8):
            (self.root / f"extra{index}.txt").write_text("x", encoding="utf-8")
        message = self.refusal(clean, self.runner)
        self.assertIn("8 change(s)", message)
        self.assertIn("... and 3 more", message)

    def test_a_moved_head_names_both_commits(self):
        message = self.refusal(clean, self.runner, "0" * 40)
        self.assertIn(self.sha[:12], message)
        self.assertIn("0" * 12, message)

    def test_hidden_index_flags_name_the_files_and_the_command_that_clears_them(self):
        self.runner.git("update-index", "--assume-unchanged", "VERSION")
        message = self.refusal(clean, self.runner)
        self.assertIn("VERSION", message)
        self.assertIn("git update-index --no-skip-worktree --no-assume-unchanged", message)

    def test_a_remote_that_is_not_the_configured_repository_shows_its_url_and_the_fix(self):
        message = self.refusal(remote_identity, self.runner, "origin", "someone/else")
        self.assertIn("origin fetch URL https://github.com/example/project.git", message)
        self.assertIn("someone/else", message)
        self.assertIn("git remote set-url origin https://github.com/someone/else.git", message)

    def test_a_directory_publisher_names_the_unignored_directory(self):
        store = Directory(self.runner, SimpleNamespace(directory="published"))
        message = self.refusal(store.preflight)
        self.assertIn(str(self.root / "published"), message)
        self.assertIn(".gitignore", message)

    def test_a_missing_local_candidate_names_the_tag_and_the_command_that_makes_one(self):
        message = self.refusal(local.load, self.runner, {"tag": "v1.0.0"})
        self.assertIn("relkit release prepare v1.0.0", message)


class PlanAndGuardRefusalTests(Repository):
    def pin_projection(self):
        (self.root / ".github").mkdir()
        (self.root / ".github/relkit.pyz").write_bytes(b"placeholder projection")
        git(self.root, "add", "-f", ".github/relkit.pyz")
        git(self.root, "commit", "-qm", "chore: pin a projection")

    def test_a_missing_guard_is_reported_at_its_hook_path(self):
        self.pin_projection()
        problem = protection.problem(self.root)
        self.assertIn(str(protection.hook_path(self.root)), problem)
        self.assertIn("relkit protect install", problem)

    def test_a_stale_hook_plan_names_the_command_that_produces_a_current_one(self):
        self.pin_projection()
        completed = relkit(self.root, "protect", "install", "--plan-hash", "0" * 64, "--json")
        message = json.loads(completed.stdout)["errors"][0]["message"]
        self.assertEqual(2, completed.returncode)
        self.assertIn("relkit protect install --dry-run", message)
        self.assertIn("plan_sha256", message)
        self.assertIn(str(protection.hook_path(self.root)), message)
        self.assertFalse(protection.hook_path(self.root).exists())

    def test_a_foreign_hook_is_not_replaced_and_the_message_says_what_to_do(self):
        self.pin_projection()
        hook = protection.hook_path(self.root)
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho someone else's hook\n", encoding="utf-8")
        completed = relkit(self.root, "protect", "install", "--json")
        message = json.loads(completed.stdout)["errors"][0]["message"]
        self.assertIn(str(hook), message)
        self.assertIn("move it aside", message)

    def test_protect_check_rejects_install_options_and_points_at_the_dry_run(self):
        completed = relkit(self.root, "protect", "check", "--dry-run", "--json")
        message = json.loads(completed.stdout)["errors"][0]["message"]
        self.assertIn("relkit protect install --dry-run", message)

    def test_a_json_update_without_yes_says_to_review_the_dry_run_first(self):
        completed = relkit(self.root, "update", "--json")
        message = json.loads(completed.stdout)["errors"][0]["message"]
        self.assertIn("relkit update --dry-run --json", message)
        self.assertIn("--plan-hash", message)

    def test_from_tag_without_draft_shows_the_working_invocation(self):
        completed = relkit(self.root, "notes", "v1.2.0", "--from-tag", "v1.1.0", "--json")
        message = json.loads(completed.stdout)["errors"][0]["message"]
        self.assertIn("relkit notes v1.2.0 --draft --from-tag v1.1.0", message)

    def test_an_update_refuses_a_dirty_checkout_by_naming_the_files(self):
        (self.root / "scratch.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(update.UpdateError) as raised:
            update._clean(self.root)
        self.assertIn("scratch.txt", str(raised.exception))
        self.assertIn(str(self.root), str(raised.exception))

    def test_an_untracked_projection_is_named_with_its_full_path(self):
        with self.assertRaises(update.UpdateError) as raised:
            update._tracked(self.root)
        message = str(raised.exception)
        self.assertIn(str(self.root / update.PROJECTION), message)
        self.assertIn("commit it first", message)

    def test_a_root_below_the_repository_root_names_both_paths(self):
        below = self.root / "web"
        below.mkdir()
        with self.assertRaises(update.UpdateError) as raised:
            update._repository(below.resolve())
        message = str(raised.exception)
        self.assertIn(str(below.resolve()), message)
        self.assertIn(f"--root {self.root}", message)


class ReleaseRepositoryRefusalTests(ReleaseFixture):
    def test_a_shallow_clone_gets_the_unshallow_command_for_its_path(self):
        shallow = Path(self.root.parent) / "shallow"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{self.root.as_posix()}", str(shallow)],
            check=True,
            capture_output=True,
        )
        from test_release import LocalRunner

        from releasekit.release.backend import repository

        with self.assertRaises(ReleaseError) as raised:
            repository(LocalRunner(shallow))
        message = str(raised.exception)
        self.assertIn("git fetch --unshallow", message)
        self.assertIn(str(shallow.resolve()), message)

    def test_a_root_below_the_toplevel_names_the_root_to_pass(self):
        below = self.root / "web"
        below.mkdir()
        from test_release import LocalRunner

        from releasekit.release.backend import repository

        with self.assertRaises(ReleaseError) as raised:
            repository(LocalRunner(below))
        self.assertIn(f"pass --root {self.root.resolve()}", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
