"""The pre-push guard judges what a push publishes, not the checkout it runs in.

Field case: a clean commit could not be pushed because another session had left
tracked files modified in the shared checkout, and a second push was refused for a
private value in another pull request's remote-tracking branch. The guard audited the
whole history and every remote-tracking ref, so the verdict depended on work the push
did not carry.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from releasekit import cli, owner, protection, publication
from releasekit.exposure import audit

ZERO = "0" * 40
PRIVATE_VALUE = "Someservice"
POLICY = "[exposure]\ncheck_secrets = false\ncheck_links = false\n"
SOURCE = Path(__file__).resolve().parents[1] / "src"
OWNER_IDENTITY = "Example Writer <writer@example.invalid>"
CONTRIBUTOR_ENVIRONMENT = {
    "GIT_AUTHOR_NAME": "Outside Contributor",
    "GIT_AUTHOR_EMAIL": "contributor@example.invalid",
    "GIT_COMMITTER_NAME": "Outside Contributor",
    "GIT_COMMITTER_EMAIL": "contributor@example.invalid",
}
AGENT_TRAILER = "Co-" + "Authored-By: " + "Clau" + "de <bot@example.invalid>"


def update(local_ref: str, local_sha: str, remote_ref: str, remote_sha: str) -> str:
    return f"{local_ref} {local_sha} {remote_ref} {remote_sha}\n"


class PushRepository(unittest.TestCase):
    """A working repository with a bare `origin` and a sibling owner policy."""

    def setUp(self) -> None:
        override = patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: ""})
        override.start()
        self.addCleanup(override.stop)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name).resolve()
        self.root = self.base / "work"
        self.remote = self.base / "origin.git"
        private = self.base / "work-private"
        private.mkdir()
        (private / ".publication-private-values").write_text(f"{PRIVATE_VALUE}\n", encoding="utf-8")
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Example Writer")
        self.git("config", "user.email", "writer@example.invalid")
        self.git("config", "core.hooksPath", str(self.root / ".git" / "hooks"))
        self.write("relkit.toml", POLICY)
        self.write("README.md", "An example project.\n")
        self.write(
            ".github/relkit.pyz",
            "from releasekit.cli import main\nraise SystemExit(main())\n",
        )
        self.commit("chore: start the project")
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(self.remote)],
            check=True,
            capture_output=True,
        )
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "main")

    def git(
        self, *arguments: str, root: Path | None = None, environment: dict[str, str] | None = None
    ) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root or self.root,
            check=True,
            capture_output=True,
            encoding="utf-8",
            env={**os.environ, **(environment or {})},
        )
        return completed.stdout.strip()

    def write(self, relative: str, text: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message: str, *, environment: dict[str, str] | None = None) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, environment=environment)
        return self.git("rev-parse", "HEAD")

    def install_guard(self) -> Path:
        """The managed hook, pinning the policy and projection as they are now."""
        hook = protection.install(self.root)
        self.assertIsNone(protection.problem(self.root))
        return hook

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def remote_main(self) -> str:
        return self.git("rev-parse", "refs/remotes/origin/main")

    def fetched_branch_with_private_value(self, name: str) -> str:
        """Another pull request's branch this checkout has fetched, and nothing else."""
        start = self.head()
        self.git("switch", "-q", "-c", "side")
        self.write("side.txt", f"mentions {PRIVATE_VALUE}\n")
        commit = self.commit("feat: a different change")
        self.git("switch", "-q", "main")
        self.git("update-ref", f"refs/remotes/origin/{name}", commit)
        self.git("branch", "-q", "-D", "side")
        self.assertEqual(start, self.head())
        return commit

    def scope(self, text: str, remote: str = "origin") -> audit.PushScope:
        return audit.push_scope(self.root, remote, audit.parse_push_updates(text))

    def failures(self, text: str, remote: str = "origin", **options) -> list[str]:
        options.setdefault("names", (PRIVATE_VALUE,))
        return audit.history_failures(self.root, scope=self.scope(text, remote), **options)

    def audit_push(self, text: str, *extra: str) -> tuple[int, str, str]:
        arguments = ["audit", "--push", "--remote", "origin", "--owner", "--no-download", *extra]
        arguments += ["--root", str(self.root)]
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "stdin", io.StringIO(text)),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = cli.main(arguments)
        return code, out.getvalue(), err.getvalue()


class PushRangeTests(PushRepository):
    def test_an_update_judges_only_the_commits_the_remote_does_not_have(self) -> None:
        self.write("notes.txt", f"early draft mentions {PRIVATE_VALUE}\n")
        early = self.commit("docs: early draft")
        self.git("push", "-q", "origin", "main")
        self.write("notes.txt", "rewritten\n")
        clean = self.commit("docs: rewrite the notes")

        text = update("refs/heads/main", clean, "refs/heads/main", early)

        self.assertEqual(1, self.scope(text).commits)
        self.assertEqual([], self.failures(text))
        # Control: a destination with nothing of this history would be given all of it.
        everything = self.failures(
            update("refs/heads/main", clean, "refs/heads/main", ZERO), remote="elsewhere"
        )
        self.assertTrue(any(":notes.txt" in item for item in everything), everything)

    def test_a_private_value_in_a_pushed_commit_names_the_commit_and_the_path(self) -> None:
        self.write("notes.txt", f"talks about {PRIVATE_VALUE}\n")
        leaked = self.commit("docs: add notes")
        self.write("notes.txt", "talks about nothing\n")
        tip = self.commit("docs: remove the mention")

        failures = self.failures(
            update("refs/heads/main", tip, "refs/heads/main", self.remote_main())
        )

        blob = self.git("rev-parse", f"{leaked}:notes.txt")
        self.assertIn(f"history blob {blob[:12]}:notes.txt: private-value", failures)

    def test_a_private_value_in_a_pushed_commit_message_names_the_commit(self) -> None:
        tip = self.commit(f"docs: describe {PRIVATE_VALUE}")

        failures = self.failures(
            update("refs/heads/main", tip, "refs/heads/main", self.remote_main())
        )

        self.assertEqual([f"history {tip[:12]}: commit-message: private-value"], failures)

    def test_a_remote_ref_name_carrying_a_private_value_is_refused(self) -> None:
        tip = self.commit("docs: a clean change")

        failures = self.failures(
            update("refs/heads/main", tip, f"refs/heads/{PRIVATE_VALUE}-fix", ZERO)
        )

        self.assertIn(f"history ref refs/heads/{PRIVATE_VALUE}-fix: private-value", failures)

    def test_a_deletion_pushes_nothing_and_passes(self) -> None:
        self.fetched_branch_with_private_value("other")
        text = update("(delete)", ZERO, "refs/heads/other", self.remote_main())

        self.assertEqual((), self.scope(text).references)
        self.assertEqual([], self.failures(text))
        code, out, err = self.audit_push(text)
        self.assertEqual((0, ""), (code, err), out)

    def test_nothing_on_standard_input_passes_without_reading_any_policy(self) -> None:
        (self.root / "relkit.toml").write_text("this is not toml [", encoding="utf-8")

        code, out, err = self.audit_push("")

        self.assertEqual((0, ""), (code, err))
        self.assertIn("nothing is being pushed", out)

    def test_the_first_push_to_an_empty_remote_judges_every_commit(self) -> None:
        self.write("notes.txt", f"mentions {PRIVATE_VALUE}\n")
        tip = self.commit("docs: add notes")
        empty = self.base / "empty.git"
        subprocess.run(["git", "init", "-q", "--bare", str(empty)], check=True)
        self.git("remote", "add", "fresh", str(empty))
        text = update("refs/heads/main", tip, "refs/heads/main", ZERO)

        scope = self.scope(text, "fresh")

        self.assertEqual(2, scope.commits)
        failures = self.failures(text, "fresh")
        self.assertTrue(any(":notes.txt: private-value" in item for item in failures), failures)

    def test_a_new_branch_judges_what_its_remote_does_not_already_hold(self) -> None:
        self.write("notes.txt", f"mentions {PRIVATE_VALUE}\n")
        leaked = self.commit("docs: add notes")
        self.git("push", "-q", "origin", "main")
        self.git("switch", "-q", "-c", "topic")
        self.write("topic.txt", "a clean topic\n")
        tip = self.commit("feat: a topic")
        text = update("refs/heads/topic", tip, "refs/heads/topic", ZERO)

        scope = self.scope(text)

        self.assertEqual(1, scope.commits)
        self.assertNotIn(leaked, self.git("rev-list", *scope.revisions).split())
        self.assertEqual([], self.failures(text))

    def test_a_tip_this_checkout_never_fetched_falls_back_to_the_remote_tracking_refs(self) -> None:
        self.write("topic.txt", "clean\n")
        tip = self.commit("feat: a topic")
        unknown = "1" * 40

        scope = self.scope(update("refs/heads/main", tip, "refs/heads/main", unknown))

        self.assertEqual(1, scope.commits)
        self.assertEqual((tip, "--not", "--remotes=origin"), scope.revisions)

    def test_a_remote_named_by_url_has_no_tracking_refs_to_subtract(self) -> None:
        self.write("topic.txt", "clean\n")
        tip = self.commit("feat: a topic")
        text = update("refs/heads/topic", tip, "refs/heads/topic", ZERO)

        scope = self.scope(text, str(self.remote))

        self.assertEqual((tip,), scope.revisions)
        self.assertEqual(2, scope.commits)

    def test_a_shallow_checkout_is_not_refused_for_being_shallow(self) -> None:
        clone = self.base / "shallow"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", self.remote.as_uri(), str(clone)],
            check=True,
            capture_output=True,
        )
        self.git("config", "user.name", "Example Writer", root=clone)
        self.git("config", "user.email", "writer@example.invalid", root=clone)
        (clone / "topic.txt").write_text("clean\n", encoding="utf-8")
        self.git("add", "-A", root=clone)
        self.git("commit", "-q", "-m", "feat: a topic", root=clone)
        tip = self.git("rev-parse", "HEAD", root=clone)
        base = self.git("rev-parse", "origin/main", root=clone)
        scope = audit.push_scope(
            clone,
            "origin",
            audit.parse_push_updates(update("refs/heads/main", tip, "refs/heads/main", base)),
        )

        self.assertEqual([], audit.history_failures(clone, names=(PRIVATE_VALUE,), scope=scope))
        self.assertTrue(
            any(
                item.startswith("Git history audit requires a complete non-shallow repository")
                for item in audit.history_failures(clone, names=(PRIVATE_VALUE,))
            )
        )

    def test_malformed_standard_input_is_rejected_rather_than_read_as_nothing(self) -> None:
        for text in ("refs/heads/main\n", f"a {ZERO} b\n", f"a nothex b {ZERO}\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                audit.parse_push_updates(text)
        code, _out, err = self.audit_push("garbage\n")
        self.assertEqual(2, code)
        self.assertIn("pre-push input line 1", err)


class PushedTagTests(PushRepository):
    def test_a_tag_message_with_a_private_value_is_refused(self) -> None:
        self.git("tag", "-a", "v1.0.0", "-m", f"release for {PRIVATE_VALUE}")
        tag = self.git("rev-parse", "v1.0.0")

        failures = self.failures(update("refs/tags/v1.0.0", tag, "refs/tags/v1.0.0", ZERO))

        self.assertIn(f"history tag {tag[:12]}: private-value", failures)

    def test_a_tag_on_a_published_commit_pushes_no_commits_and_a_clean_tag_passes(self) -> None:
        self.git("tag", "-a", "v1.0.0", "-m", "release 1.0.0")
        tag = self.git("rev-parse", "v1.0.0")
        text = update("refs/tags/v1.0.0", tag, "refs/tags/v1.0.0", ZERO)

        self.assertEqual(0, self.scope(text).commits)
        self.assertEqual([], self.failures(text))

    def test_a_tag_on_an_unpushed_commit_judges_that_commit(self) -> None:
        self.write("notes.txt", f"mentions {PRIVATE_VALUE}\n")
        tip = self.commit("docs: add notes")
        self.git("tag", "v1.0.0")

        failures = self.failures(update("refs/tags/v1.0.0", tip, "refs/tags/v1.0.0", ZERO))

        self.assertTrue(any(":notes.txt: private-value" in item for item in failures), failures)

    def test_a_tag_pointing_at_a_tree_is_still_read(self) -> None:
        self.write("notes.txt", f"mentions {PRIVATE_VALUE}\n")
        self.commit("docs: add notes")
        tree = self.git("rev-parse", "HEAD^{tree}")
        self.git("tag", "tree-tag", tree)
        text = update("refs/tags/tree-tag", tree, "refs/tags/tree-tag", ZERO)

        scope = self.scope(text)
        failures = audit.history_failures(self.root, names=(PRIVATE_VALUE,), scope=scope)

        self.assertEqual(0, scope.commits)
        self.assertTrue(any("notes.txt: private-value" in item for item in failures), failures)


class PushIsolationTests(PushRepository):
    def setUp(self) -> None:
        super().setUp()
        self.install_guard()

    def test_another_branchs_remote_tracking_finding_does_not_block_a_clean_push(self) -> None:
        # Field case: a private-policy finding in one pull request's remote-tracking
        # branch refused the push of a different, clean branch.
        foreign = self.fetched_branch_with_private_value("pr-9")
        self.write("clean.txt", "nothing private\n")
        tip = self.commit("feat: the clean change")
        text = update("refs/heads/main", tip, "refs/heads/main", self.remote_main())

        full = audit.history_failures(self.root, names=(PRIVATE_VALUE,))
        pushed = self.failures(text)

        self.assertTrue(any(foreign[:12] in item or "side.txt" in item for item in full), full)
        self.assertEqual([], pushed)

    def test_a_commit_the_remote_already_holds_through_another_branch_is_not_rejudged(self) -> None:
        foreign = self.fetched_branch_with_private_value("pr-9")
        self.git("switch", "-q", "-c", "topic", foreign)
        self.write("topic.txt", "clean\n")
        tip = self.commit("feat: build on the fetched branch")
        text = update("refs/heads/topic", tip, "refs/heads/topic", ZERO)

        self.assertEqual(1, self.scope(text).commits)
        self.assertEqual([], self.failures(text))

    def test_a_local_branch_that_is_never_pushed_is_outside_the_verdict(self) -> None:
        self.git("switch", "-q", "-c", "scratch")
        self.write("scratch.txt", f"mentions {PRIVATE_VALUE}\n")
        self.commit("wip: scratch")
        self.git("switch", "-q", "main")
        self.write("clean.txt", "clean\n")
        tip = self.commit("feat: the clean change")

        failures = self.failures(
            update("refs/heads/main", tip, "refs/heads/main", self.remote_main())
        )

        self.assertEqual([], failures)

    def test_a_dirty_worktree_does_not_block_pushing_a_clean_commit(self) -> None:
        self.write("clean.txt", "clean\n")
        tip = self.commit("feat: the clean change")
        self.write("README.md", f"someone else's edit mentions {PRIVATE_VALUE}\n")
        self.write("untracked.txt", f"{PRIVATE_VALUE}\n")
        text = update("refs/heads/main", tip, "refs/heads/main", self.remote_main())

        code, out, err = self.audit_push(text)

        self.assertEqual((0, ""), (code, err), out)
        self.assertIn("passed (1 pushed commit(s) checked)", out)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as history_err:
            history = cli.main(["audit", "--history", "--root", str(self.root), "--no-download"])
        self.assertEqual(1, history, "control: the whole-history audit still needs a clean tree")
        self.assertIn("requires a clean tracked tree", history_err.getvalue())

    def test_a_push_that_carries_a_finding_is_refused_with_a_push_appropriate_fix(self) -> None:
        self.write("notes.txt", f"mentions {PRIVATE_VALUE}\n")
        tip = self.commit("docs: add notes")
        text = update("refs/heads/main", tip, "refs/heads/main", self.remote_main())

        code, _out, err = self.audit_push(text)

        self.assertEqual(1, code)
        self.assertIn(":notes.txt: private-value", err)
        self.assertIn("git commit --amend", err)
        self.assertNotIn("--staged", err)
        self.assertNotIn("clean tracked tree", err)

    def test_the_result_names_the_push_scope(self) -> None:
        self.write("clean.txt", "clean\n")
        tip = self.commit("feat: the clean change")
        text = update("refs/heads/main", tip, "refs/heads/main", self.remote_main())
        out = io.StringIO()
        with (
            patch.object(sys, "stdin", io.StringIO(text)),
            redirect_stdout(out),
            redirect_stderr(io.StringIO()),
        ):
            code = cli.main(
                [
                    "--json",
                    "audit",
                    "--push",
                    "--remote",
                    "origin",
                    "--owner",
                    "--root",
                    str(self.root),
                ]
            )

        envelope = json.loads(out.getvalue())
        self.assertEqual(0, code, envelope)
        self.assertEqual("push", envelope["data"]["scope"])
        self.assertEqual("origin", envelope["data"]["remote"])
        self.assertEqual(1, envelope["data"]["pushed_commits"])

    def test_push_options_that_cannot_apply_are_refused(self) -> None:
        for extra in (("--strict",), ("--require-overlay",)):
            with self.subTest(extra=extra):
                code, _out, err = self.audit_push("", *extra)
                self.assertEqual(2, code)
                self.assertIn("pushed commits only", err)
        for arguments, expected in (
            (["--push"], "needs --remote"),
            (["--remote", "origin"], "only valid with --push"),
        ):
            with self.subTest(arguments=arguments):
                err = io.StringIO()
                with patch.object(sys, "stdin", io.StringIO("")), redirect_stderr(err):
                    code = cli.main(["audit", *arguments, "--root", str(self.root)])
                self.assertEqual(2, code)
                self.assertIn(expected, err.getvalue())


class PushIdentityTests(PushRepository):
    def setUp(self) -> None:
        super().setUp()
        self.options = {
            "owner_identities": (OWNER_IDENTITY,),
            "forbid_ai_attribution": True,
            "names": (),
        }

    def test_pushing_only_other_peoples_commits_is_not_a_misspelt_owner(self) -> None:
        self.write("contributed.txt", "from outside\n")
        tip = self.commit("feat: an outside change", environment=CONTRIBUTOR_ENVIRONMENT)
        text = update("refs/heads/main", tip, "refs/heads/main", self.remote_main())

        self.assertEqual([], self.failures(text, **self.options))

    def test_a_misspelt_owner_still_fails_a_push(self) -> None:
        self.write("clean.txt", "clean\n")
        tip = self.commit("feat: the clean change")
        text = update("refs/heads/main", tip, "refs/heads/main", self.remote_main())

        failures = self.failures(
            text, **{**self.options, "owner_identities": ("Nobody <n@example.invalid>",)}
        )

        self.assertEqual(1, len(failures), failures)
        self.assertIn("owner_identities match no commit author", failures[0])

    def test_an_owner_commit_in_the_push_may_not_credit_an_agent(self) -> None:
        self.write("clean.txt", "clean\n")
        tip = self.commit(f"feat: the clean change\n\n{AGENT_TRAILER}")
        text = update("refs/heads/main", tip, "refs/heads/main", self.remote_main())

        failures = self.failures(text, **self.options)

        self.assertEqual(
            [f"history {tip[:12]}: commit-message: ai-attribution"],
            [item for item in failures if "commit-message" in item],
        )

    def test_an_unpushed_agent_attribution_is_not_a_finding(self) -> None:
        self.git("switch", "-q", "-c", "scratch")
        self.commit(f"wip: scratch\n\n{AGENT_TRAILER}")
        self.git("switch", "-q", "main")
        self.write("clean.txt", "clean\n")
        tip = self.commit("feat: the clean change")
        text = update("refs/heads/main", tip, "refs/heads/main", self.remote_main())

        self.assertEqual([], self.failures(text, **self.options))


class SecretEngineTests(PushRepository):
    def setUp(self) -> None:
        super().setUp()
        self.write("relkit.toml", "[exposure]\ncheck_links = false\n")
        self.write(".betterleaks.toml", "[extend]\nuseDefault = true\n")
        self.commit("chore: scan for secrets")
        self.git("push", "-q", "--no-verify", "origin", "main")
        self.install_guard()

    def test_the_secret_scanner_is_given_the_pushed_range_and_no_other_ref(self) -> None:
        self.write("clean.txt", "clean\n")
        tip = self.commit("feat: the clean change")
        text = update("refs/heads/main", tip, "refs/heads/main", self.remote_main())
        scope = self.scope(text)

        with (
            patch.object(publication.toolchain, "prepare"),
            patch.object(publication.engines, "betterleaks", return_value=0) as scanner,
        ):
            code, _out, err = self.audit_push(text)

        self.assertEqual((0, ""), (code, err))
        options = scanner.call_args.kwargs
        self.assertEqual(scope.log_opts, options["log_opts"])
        self.assertEqual(f"{tip} --not {self.remote_main()}", options["log_opts"])
        self.assertTrue(options["history"])

    def test_a_secret_finding_in_the_pushed_range_refuses_the_push(self) -> None:
        self.write("clean.txt", "clean\n")
        tip = self.commit("feat: the clean change")
        text = update("refs/heads/main", tip, "refs/heads/main", self.remote_main())

        with (
            patch.object(publication.toolchain, "prepare"),
            patch.object(
                publication.engines,
                "betterleaks",
                return_value=publication.engines.BETTERLEAKS_FINDINGS_EXIT,
            ),
        ):
            code, _out, err = self.audit_push(text)

        self.assertEqual(1, code)
        self.assertIn("Betterleaks failed", err)


if __name__ == "__main__":
    unittest.main()
