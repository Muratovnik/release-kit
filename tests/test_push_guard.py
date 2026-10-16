"""The managed pre-push hook runs the pushed-range audit through real `git push`."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import test_push_audit as push_audit

from releasekit import cli, owner, protection

PRIVATE_VALUE = push_audit.PRIVATE_VALUE
SOURCE = push_audit.SOURCE


class GuardHookPushTests(push_audit.PushRepository):
    """Real `git push` through the installed managed hook."""

    def setUp(self) -> None:
        super().setUp()
        self.hook = self.install_guard()

    def push(self, *refs: str, remote: str = "origin") -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONPATH": str(SOURCE),
            "GIT_TERMINAL_PROMPT": "0",
        }
        environment.pop(owner.PRIVATE_ROOT_ENV, None)
        return subprocess.run(
            ["git", "push", remote, *refs],
            cwd=self.root,
            check=False,
            capture_output=True,
            encoding="utf-8",
            env=environment,
            timeout=180,
        )

    def test_the_installed_guard_audits_the_push_and_not_the_history(self) -> None:
        text = self.hook.read_text(encoding="utf-8")

        self.assertIn('audit --push --owner --remote "$1"', text)
        self.assertNotIn("--history", text)

    def test_a_clean_commit_is_pushed_from_a_dirty_worktree(self) -> None:
        self.write("clean.txt", "clean\n")
        tip = self.commit("feat: the clean change")
        self.write("README.md", f"another session's edit mentions {PRIVATE_VALUE}\n")
        self.write("untracked.txt", f"{PRIVATE_VALUE}\n")

        pushed = self.push("main")

        self.assertEqual(0, pushed.returncode, pushed.stderr)
        self.assertEqual(tip, self.git("rev-parse", "main", root=self.remote))

    def test_the_guard_of_an_earlier_release_still_refuses_a_dirty_worktree(self) -> None:
        # Control for the test above: the whole-history guard is what blocked it.
        pins = protection.recorded_digests(self.root)
        self.hook.write_text(protection._hook_v2(repr(pins)), encoding="utf-8", newline="\n")
        self.write("clean.txt", "clean\n")
        self.commit("feat: the clean change")
        self.write("README.md", "someone else's unfinished edit\n")

        pushed = self.push("main")

        self.assertNotEqual(0, pushed.returncode)
        self.assertIn("requires a clean tracked tree", pushed.stderr)

    def test_a_private_value_in_the_pushed_commit_refuses_the_push(self) -> None:
        self.write("notes.txt", f"mentions {PRIVATE_VALUE}\n")
        leaked = self.commit("docs: add notes")

        pushed = self.push("main")

        self.assertNotEqual(0, pushed.returncode)
        self.assertIn(":notes.txt: private-value", pushed.stderr)
        self.assertIn("git commit --amend", pushed.stderr)
        self.assertNotEqual(leaked, self.git("rev-parse", "main", root=self.remote))

    def test_a_fetched_branch_with_a_finding_does_not_block_a_clean_push(self) -> None:
        # Field case: another pull request's remote-tracking branch carried a finding
        # and the push of this clean branch was refused for it.
        self.fetched_branch_with_private_value("pr-9")
        self.write("clean.txt", "clean\n")
        tip = self.commit("feat: the clean change")

        pushed = self.push("main")

        self.assertEqual(0, pushed.returncode, pushed.stderr)
        self.assertEqual(tip, self.git("rev-parse", "main", root=self.remote))

    def test_a_new_branch_is_pushed_and_judged_by_its_own_commits(self) -> None:
        self.git("switch", "-q", "-c", "topic")
        self.write("notes.txt", f"mentions {PRIVATE_VALUE}\n")
        self.commit("docs: add notes")

        refused = self.push("topic")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn(":notes.txt: private-value", refused.stderr)

        self.write("notes.txt", "rewritten\n")
        self.git("reset", "-q", "--soft", "main")
        self.git("commit", "-q", "-a", "-m", "docs: add clean notes")
        accepted = self.push("topic")
        self.assertEqual(0, accepted.returncode, accepted.stderr)

    def test_the_first_push_to_an_empty_remote_is_judged_in_full(self) -> None:
        empty = self.base / "empty.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(empty)], check=True)
        self.git("remote", "add", "fresh", str(empty))
        self.write("notes.txt", f"mentions {PRIVATE_VALUE}\n")
        self.commit("docs: add notes")
        self.write("notes.txt", "rewritten\n")
        self.commit("docs: rewrite the notes")

        refused = self.push("main", remote="fresh")

        self.assertNotEqual(0, refused.returncode)
        self.assertIn(":notes.txt: private-value", refused.stderr)
        self.assertEqual("", self.git("for-each-ref", "refs/heads", root=empty))

    def test_a_tag_push_is_judged_and_a_deletion_passes(self) -> None:
        self.git("tag", "-a", "v1.0.0", "-m", f"release for {PRIVATE_VALUE}")
        refused = self.push("v1.0.0")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("private-value", refused.stderr)

        self.git("tag", "-d", "v1.0.0")
        self.git("tag", "-a", "v1.0.0", "-m", "release 1.0.0")
        self.assertEqual(0, self.push("v1.0.0").returncode)

        deleted = self.push("--delete", "v1.0.0")
        self.assertEqual(0, deleted.returncode, deleted.stderr)

    def test_a_missing_projection_refuses_instead_of_passing(self) -> None:
        (self.root / ".github" / "relkit.pyz").unlink()
        self.write("clean.txt", "clean\n")
        self.commit("feat: the clean change")

        pushed = self.push("main")

        self.assertNotEqual(0, pushed.returncode)
        self.assertIn("guarded input changed", pushed.stderr)


class GuardTemplateMigrationTests(push_audit.PushRepository):
    def setUp(self) -> None:
        super().setUp()
        self.hook = self.install_guard()
        self.pins = protection.recorded_digests(self.root)

    def test_the_whole_history_guard_of_an_earlier_release_is_still_recognized(self) -> None:
        # A frozen template: the bytes an installed 0.32 guard has, which this release
        # must keep recognizing as an intact guard rather than as tampering.
        pins = repr({"relkit.toml": "a" * 64, ".github/relkit.pyz": "b" * 64})
        digest = hashlib.sha256(protection._hook_v2(pins).encode()).hexdigest()

        self.assertEqual("5001cecfcb652a6b364a0abcfd5f84dcf34b0a87587bb4ce534e34e37cf04323", digest)
        self.assertNotEqual(protection._hook_v2(pins), protection._hook_v3(pins))

    def test_an_intact_whole_history_guard_is_refreshable_not_tampered(self) -> None:
        self.hook.write_text(protection._hook_v2(repr(self.pins)), encoding="utf-8", newline="\n")

        self.assertIsNone(protection.problem(self.root))
        self.assertTrue(protection.outdated_template(self.root))
        self.assertEqual(self.pins, protection.recorded_digests(self.root))

        protection.install(self.root)

        self.assertFalse(protection.outdated_template(self.root))
        self.assertIn("--push", self.hook.read_text(encoding="utf-8"))

    def test_protect_check_names_the_exact_command_to_adopt_the_push_guard(self) -> None:
        self.hook.write_text(protection._hook_v2(repr(self.pins)), encoding="utf-8", newline="\n")
        out = io.StringIO()

        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["--json", "protect", "check", "--root", str(self.root)])

        envelope = json.loads(out.getvalue())
        self.assertEqual(0, code, envelope)
        self.assertEqual("valid", envelope["data"]["guard"])
        (warning,) = envelope["warnings"]
        self.assertEqual("guard_template_outdated", warning["code"])
        self.assertIn("`relkit protect install`", warning["message"])
        self.assertIn("whole history", warning["message"])

    def test_a_current_guard_reports_no_template_warning(self) -> None:
        out = io.StringIO()

        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["--json", "protect", "check", "--root", str(self.root)])

        self.assertEqual(0, code)
        self.assertEqual([], json.loads(out.getvalue())["warnings"])


if __name__ == "__main__":
    unittest.main()
