"""The commit check judges what one commit adds against the owner's private policy.

Field case: private values reached commits in several public repositories and were found
only by the pre-push guard or at release preparation, after which the history had to be
rewritten. The only per-commit workaround, `audit --staged --owner`, read the whole
index and ran both engines, 9 to 14 seconds a commit, so it was used for one session and
dropped. These tests hold the two halves of the fix: the check reads only what the
commit adds, and it runs on every commit through the guard `protect install` writes.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_update import artifact_bytes

from releasekit import cli, owner, protection
from releasekit.exposure import rules

PRIVATE_VALUE = "Someservice"
# The stand-in projection below is a script, not an archive the full audit could open.
POLICY = "[exposure]\ncheck_secrets = false\ncheck_links = false\ninspect_archives = false\n"
SOURCE = Path(__file__).resolve().parents[1] / "src"
PROJECTION = "from releasekit.cli import main\nraise SystemExit(main())\n"


class CommitRepository(unittest.TestCase):
    """A repository with a sibling owner policy declaring one private value."""

    def setUp(self) -> None:
        override = patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: ""})
        override.start()
        self.addCleanup(override.stop)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name).resolve()
        self.root = self.base / "work"
        self.private = self.base / "work-private"
        self.private.mkdir()
        (self.private / ".publication-private-values").write_text(
            f"{PRIVATE_VALUE}\n", encoding="utf-8"
        )
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Example Writer")
        self.git("config", "user.email", "writer@example.invalid")
        self.git("config", "core.hooksPath", str(self.root / ".git" / "hooks"))
        self.write("relkit.toml", POLICY)
        self.write(".github/relkit.pyz", PROJECTION)
        self.write("notes.md", "first line\nsecond line\n")
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", "chore: start the project")

    def git(self, *arguments: str, root: Path | None = None) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root or self.root,
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
        return completed.stdout.strip()

    def write(self, relative: str, text: str, root: Path | None = None) -> Path:
        target = (root or self.root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        return target

    def append(self, relative: str, text: str) -> None:
        with (self.root / relative).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(text)

    def committed(self, message: str) -> None:
        """History made before the check existed: setup, not the behaviour under test."""
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", message)

    def audit(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["audit", "--commit", *extra, "--root", str(self.root)])
        return code, out.getvalue(), err.getvalue()


class CommitScopeTests(CommitRepository):
    def test_a_private_value_on_an_added_line_refuses_the_commit(self) -> None:
        self.append("notes.md", f"third line names {PRIVATE_VALUE}\n")
        self.git("add", "notes.md")

        code, _out, err = self.audit("--owner")

        self.assertEqual(1, code, err)
        self.assertIn("notes.md: private-value", err)
        self.assertIn("only what this commit adds was checked", err)

    def test_a_value_already_in_the_file_does_not_refuse_an_unrelated_edit(self) -> None:
        self.append("notes.md", f"old line names {PRIVATE_VALUE}\n")
        self.committed("docs: an old note")
        self.append("notes.md", "a clean new line\n")
        self.git("add", "notes.md")

        code, out, err = self.audit("--owner")

        self.assertEqual(0, code, err)
        self.assertIn("1 staged path(s) checked", out)

    def test_a_change_that_only_removes_lines_is_not_judged_by_what_remains(self) -> None:
        self.write("notes.md", f"first line\nsecond line\nnames {PRIVATE_VALUE}\n")
        self.committed("docs: an old note")
        self.write("notes.md", f"first line\nnames {PRIVATE_VALUE}\n")
        self.git("add", "notes.md")

        code, _out, err = self.audit("--owner")

        self.assertEqual(0, code, err)

    def stage_blob(self, relative: str, text: str) -> None:
        """Stage content under a name the filesystem may refuse, through the index alone."""
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=self.root,
            input=text.encode(),
            check=True,
            capture_output=True,
        ).stdout.decode()
        self.git("update-index", "--add", "--cacheinfo", f"100644,{blob.strip()},{relative}")

    def test_a_quoted_name_is_judged_by_its_added_lines(self) -> None:
        # Git quotes this name in its diff. A name that is not matched back to its
        # change would be judged whole, and the old line would refuse a clean edit.
        relative = 'dir with space/файл "q".md'
        # Git for Windows refuses such a name in the index unless told the index alone
        # will hold it; it never reaches the filesystem here.
        self.git("config", "core.protectNTFS", "false")
        old = f"old line names {PRIVATE_VALUE}\n"
        self.stage_blob(relative, old)
        self.git("commit", "-q", "--no-verify", "-m", "docs: an old note")
        self.stage_blob(relative, old + "a clean new line\n")
        self.assertEqual(0, self.audit("--owner")[0])

        self.stage_blob(relative, old + f"a new line names {PRIVATE_VALUE}\n")
        code, _out, err = self.audit("--owner")
        self.assertEqual(1, code, err)
        self.assertIn(f"{relative}: private-value", err)

    def test_a_new_path_whose_name_carries_the_value_refuses(self) -> None:
        self.write(f"docs/{PRIVATE_VALUE.lower()}-guide.md", "clean content\n")
        self.git("add", "-A")

        code, _out, err = self.audit("--owner")

        self.assertEqual(1, code, err)
        self.assertIn(f"docs/{PRIVATE_VALUE.lower()}-guide.md: private-value", err)

    def test_a_renamed_file_is_judged_by_its_new_name_not_its_old_content(self) -> None:
        self.write("old.md", f"names {PRIVATE_VALUE}\n")
        self.committed("docs: an old note")
        self.git("mv", "old.md", "renamed.md")

        code, _out, err = self.audit("--owner")

        self.assertEqual(0, code, err)

    def test_an_owner_policy_file_entering_the_index_refuses(self) -> None:
        self.write(".publication-private-values", "nothing private in the text\n")
        self.git("add", "-A")

        code, _out, err = self.audit("--owner")

        self.assertEqual(1, code, err)
        self.assertIn(".publication-private-values: private-path", err)

    def test_a_changed_archive_is_judged_whole(self) -> None:
        archive = self.root / "bundle.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("inner/readme.txt", f"names {PRIVATE_VALUE} " * 40)
        self.git("add", "-A")

        code, _out, err = self.audit("--owner")

        self.assertEqual(1, code, err)
        self.assertIn("bundle.zip: private-value", err)

    def test_public_findings_are_left_to_the_full_audit(self) -> None:
        # A repository that already fails a public rule must still be able to commit.
        line = "read C:/Users/jdoe/project/notes.md\n"
        self.assertEqual({rules.HOME_DIRECTORY}, rules.kinds_in_text(line))
        self.append("notes.md", line)
        self.git("add", "notes.md")

        code, _out, err = self.audit("--owner")

        self.assertEqual(0, code, err)

    def test_the_message_is_judged_without_its_comments_or_verbose_diff(self) -> None:
        message = self.write(
            "message.txt",
            "docs: a clean subject\n\n"
            f"# Please enter the commit message; branch {PRIVATE_VALUE}-work\n"
            "# ------------------------ >8 ------------------------\n"
            f"-removed line naming {PRIVATE_VALUE}\n",
            root=self.base,
        )
        self.assertEqual(0, self.audit("--owner", "--message", str(message))[0])

        message.write_text(f"docs: mention {PRIVATE_VALUE}\n", encoding="utf-8")
        code, _out, err = self.audit("--owner", "--message", str(message))
        self.assertEqual(1, code, err)
        self.assertIn("commit message: private-value", err)

    def test_nothing_staged_passes(self) -> None:
        code, out, err = self.audit("--owner")

        self.assertEqual(0, code, err)
        self.assertIn("0 staged path(s) checked", out)

    def test_the_commit_scope_takes_no_strict_and_needs_its_own_message_option(self) -> None:
        self.assertEqual(2, self.audit("--owner", "--strict")[0])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["audit", "--message", "m.txt", "--root", str(self.root)])
        self.assertEqual(2, code)
        self.assertIn("--message is only valid with --commit", err.getvalue())


class ConfiguredPolicyTests(CommitRepository):
    """The policy applies without `--owner` once the checkout names its private root."""

    def configure(self, value: Path) -> None:
        self.git("config", "--local", owner.PRIVATE_ROOT_CONFIG, str(value))

    def full_audit(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["audit", "--no-download", *extra, "--root", str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def test_without_a_configured_policy_the_commit_check_judges_nothing(self) -> None:
        # The sibling exists, but a sibling is a guess: only `--owner` follows it.
        self.append("notes.md", f"names {PRIVATE_VALUE}\n")
        self.git("add", "notes.md")

        code, out, err = self.audit()

        self.assertEqual(0, code, err)
        self.assertIn("configures no owner policy", out)

    def test_a_configured_policy_applies_to_the_commit_check_without_the_flag(self) -> None:
        self.configure(self.private)
        self.append("notes.md", f"names {PRIVATE_VALUE}\n")
        self.git("add", "notes.md")

        code, _out, err = self.audit()

        self.assertEqual(1, code, err)
        self.assertIn("notes.md: private-value", err)

    def test_the_process_override_also_turns_the_policy_on(self) -> None:
        self.append("notes.md", f"names {PRIVATE_VALUE}\n")
        self.git("add", "notes.md")

        with patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: str(self.private)}):
            code, _out, err = self.audit()

        self.assertEqual(1, code, err)

    def test_a_configured_root_that_is_missing_fails_closed(self) -> None:
        self.configure(self.base / "moved-away")

        code, _out, err = self.audit()

        self.assertEqual(2, code)
        self.assertIn("unavailable", err)

    def test_the_full_audit_applies_a_configured_policy_but_not_the_guard_requirement(
        self,
    ) -> None:
        self.configure(self.private)
        self.assertEqual(0, self.full_audit()[0])
        code, _out, err = self.full_audit("--owner")
        self.assertEqual(1, code)
        self.assertIn("pre-push guard", err)

        self.append("notes.md", f"names {PRIVATE_VALUE}\n")
        code, _out, err = self.full_audit()
        self.assertEqual(1, code, err)
        self.assertIn("notes.md: private-value", err)

    def test_the_full_audit_ignores_an_unconfigured_sibling(self) -> None:
        self.append("notes.md", f"names {PRIVATE_VALUE}\n")

        code, _out, err = self.full_audit()

        self.assertEqual(0, code, err)

    def test_exposure_applies_a_configured_policy(self) -> None:
        self.configure(self.private)
        self.append("notes.md", f"names {PRIVATE_VALUE}\n")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["exposure", "--root", str(self.root)])

        self.assertEqual(1, code)
        self.assertIn("notes.md: private-value", err.getvalue())


class CommitGuardTests(CommitRepository):
    """Real `git commit` through the guard `protect install` writes."""

    def setUp(self) -> None:
        super().setUp()
        # Linked worktrees share this absolute setting; the sibling convention would
        # look for a private root beside each worktree instead.
        self.git("config", "--local", owner.PRIVATE_ROOT_CONFIG, str(self.private))
        protection.install(self.root)
        self.assertIsNone(protection.commit_guard_problem(self.root))

    def commit(self, *arguments: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONPATH": str(SOURCE),
        }
        environment.pop(owner.PRIVATE_ROOT_ENV, None)
        return subprocess.run(
            ["git", "commit", "-q", *arguments],
            cwd=root or self.root,
            check=False,
            capture_output=True,
            encoding="utf-8",
            env=environment,
            timeout=120,
        )

    def head(self, root: Path | None = None) -> str:
        return self.git("rev-parse", "HEAD", root=root)

    def test_the_guard_refuses_a_commit_that_adds_a_private_value(self) -> None:
        before = self.head()
        self.append("notes.md", f"names {PRIVATE_VALUE}\n")
        self.git("add", "notes.md")

        refused = self.commit("-m", "docs: a note")

        self.assertNotEqual(0, refused.returncode)
        self.assertIn("notes.md: private-value", refused.stderr)
        self.assertEqual(before, self.head())

    def test_a_clean_commit_passes_the_guard(self) -> None:
        self.append("notes.md", "a clean line\n")
        self.git("add", "notes.md")

        committed = self.commit("-m", "docs: a clean note")

        self.assertEqual(0, committed.returncode, committed.stderr)
        # A hook that runs on every commit says nothing when there is nothing to say.
        self.assertEqual(("", ""), (committed.stdout, committed.stderr))

    def test_the_guard_judges_the_message(self) -> None:
        refused = self.commit("--allow-empty", "-m", f"docs: about {PRIVATE_VALUE}")

        self.assertNotEqual(0, refused.returncode)
        self.assertIn("commit message: private-value", refused.stderr)

    def test_commit_all_and_a_pathspec_are_judged_on_the_index_git_commits(self) -> None:
        # Both hand the hook a temporary index; the default index still holds HEAD.
        self.append("notes.md", f"names {PRIVATE_VALUE}\n")
        for arguments in (("-a",), ("notes.md",)):
            with self.subTest(arguments=arguments):
                refused = self.commit(*arguments, "-m", "docs: a note")
                self.assertNotEqual(0, refused.returncode)
                self.assertIn("notes.md: private-value", refused.stderr)

    def test_an_amend_is_judged_by_what_it_adds(self) -> None:
        self.append("notes.md", f"names {PRIVATE_VALUE}\n")
        self.git("add", "notes.md")

        refused = self.commit("--amend", "--no-edit")

        self.assertNotEqual(0, refused.returncode)
        self.assertIn("notes.md: private-value", refused.stderr)

    def test_a_linked_worktree_is_guarded_by_the_same_hook(self) -> None:
        linked = self.base / "linked"
        self.git("worktree", "add", "-q", "-b", "side", str(linked))
        self.write("side.md", f"names {PRIVATE_VALUE}\n", root=linked)
        self.git("add", "-A", root=linked)

        refused = self.commit("-m", "docs: a side note", root=linked)

        self.assertNotEqual(0, refused.returncode)
        self.assertIn("side.md: private-value", refused.stderr)

    def test_a_branch_with_another_projection_is_judged_by_the_installed_copy(self) -> None:
        # A branch on an older release-kit carries a projection without `--commit`.
        self.write(".github/relkit.pyz", "raise SystemExit('an older projection')\n")
        self.write("clean.md", "clean\n")
        self.git("add", "-A")
        self.assertEqual(0, self.commit("-m", "chore: an older projection").returncode)

        self.append("notes.md", f"names {PRIVATE_VALUE}\n")
        self.git("add", "notes.md")
        refused = self.commit("-m", "docs: a note")
        self.assertIn("notes.md: private-value", refused.stderr)

    def test_the_first_commit_of_a_repository_is_judged(self) -> None:
        fresh = self.base / "fresh"
        fresh.mkdir()
        self.git("init", "-q", "-b", "main", root=fresh)
        self.git("config", "core.hooksPath", str(fresh / ".git" / "hooks"), root=fresh)
        self.git("config", "--local", owner.PRIVATE_ROOT_CONFIG, str(self.private), root=fresh)
        self.write("relkit.toml", POLICY, root=fresh)
        self.write(".github/relkit.pyz", PROJECTION, root=fresh)
        protection.install(fresh)
        self.write("notes.md", f"names {PRIVATE_VALUE}\n", root=fresh)
        self.git("add", "-A", root=fresh)

        refused = self.commit("-m", "chore: start", root=fresh)

        self.assertNotEqual(0, refused.returncode)
        self.assertIn("notes.md: private-value", refused.stderr)


class CommitGuardInstallTests(CommitRepository):
    """`protect install` adds the commit guard where it can and says why where it cannot."""

    def hook(self) -> Path:
        return protection.hook_path(self.root, "commit-msg")

    def test_install_copies_the_projection_the_guard_runs(self) -> None:
        projection = artifact_bytes(protection.COMMIT_AUDIT_VERSION)
        (self.root / ".github" / "relkit.pyz").write_bytes(projection)

        plan = protection.install_plan(self.root)
        protection.install(self.root)

        self.assertEqual("install", plan["commit_guard"]["status"])
        self.assertEqual(projection, (self.hook().parent / protection.COMMIT_COPY).read_bytes())
        self.assertEqual(protection.commit_hook_content(), self.hook().read_text(encoding="utf-8"))
        self.assertIsNone(protection.commit_guard_problem(self.root))

    def test_a_projection_without_the_commit_check_installs_only_the_push_guard(self) -> None:
        (self.root / ".github" / "relkit.pyz").write_bytes(artifact_bytes("0.33.0"))

        plan = protection.install_plan(self.root)
        protection.install(self.root)

        self.assertEqual("skipped", plan["commit_guard"]["status"])
        self.assertIn(protection.COMMIT_AUDIT_VERSION, plan["commit_guard"]["reason"])
        self.assertTrue(protection.hook_path(self.root).is_file())
        self.assertFalse(self.hook().exists())
        self.assertIn("not installed", protection.commit_guard_problem(self.root) or "")

    def test_an_unmanaged_commit_msg_hook_is_kept_and_may_chain_the_check(self) -> None:
        own = "#!/bin/sh\nexit 0\n"
        self.hook().parent.mkdir(parents=True, exist_ok=True)
        self.hook().write_text(own, encoding="utf-8", newline="\n")

        plan = protection.install_plan(self.root)
        protection.install(self.root)

        self.assertEqual("skipped", plan["commit_guard"]["status"])
        self.assertEqual(own, self.hook().read_text(encoding="utf-8"))
        self.assertIn("does not run", protection.commit_guard_problem(self.root) or "")

        chained = own.replace("exit 0", 'relkit audit --commit --owner --message "$1"')
        self.hook().write_text(chained, encoding="utf-8", newline="\n")
        self.assertIsNone(protection.commit_guard_problem(self.root))

    def test_a_missing_copy_is_reported(self) -> None:
        protection.install(self.root)
        (self.hook().parent / protection.COMMIT_COPY).unlink()

        self.assertIn("missing", protection.commit_guard_problem(self.root) or "")

    def test_a_shared_dispatcher_must_declare_that_it_runs_the_commit_guard(self) -> None:
        shared = self.base / "shared-hooks"
        shared.mkdir()
        self.git("config", "core.hooksPath", str(shared))
        for name, marker in (
            ("pre-push", protection.COMPATIBLE_DISPATCHER_MARKER),
            ("commit-msg", "# a hook runtime that says nothing about repository hooks"),
        ):
            (shared / name).write_text(f"#!/bin/sh\n{marker}\nexit 0\n", encoding="utf-8")
            os.chmod(shared / name, 0o755)
        protection.install(self.root)

        self.assertIn(
            protection.COMMIT_DISPATCHER_MARKER, protection.commit_guard_problem(self.root) or ""
        )

        (shared / "commit-msg").write_text(
            f"#!/bin/sh\n{protection.COMMIT_DISPATCHER_MARKER}\nexit 0\n", encoding="utf-8"
        )
        self.assertIsNone(protection.commit_guard_problem(self.root))

    def test_protect_reports_the_commit_guard(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            installed = cli.main(["protect", "install", "--root", str(self.root)])
            checked = cli.main(["protect", "check", "--root", str(self.root)])

        self.assertEqual((0, 0), (installed, checked), err.getvalue())
        self.assertIn(f"runs {self.hook().parent / protection.COMMIT_COPY}", out.getvalue())
        self.assertIn("owner commit guard is installed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
