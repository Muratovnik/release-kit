"""The owner policy's findings in history the remote already holds are listed, not refused.

Field case: a repository's owner extended the private policy with values and patterns
that its published history already carried, in every tag back to its first release.
Those releases were immutable and their tags could not move, so no rewrite could take
the values back, and `audit --history` and `release prepare` would have refused every run
from then on. A dry run of the next release's history rewrite also found thousands of
findings in unpublished commits that only inherited those published files unchanged:
judging by commit instead of by object would have kept the audit red there too.
"""

from __future__ import annotations

import io
import json
import types
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from test_push_audit import PRIVATE_VALUE, PushRepository

from releasekit import cli, config, publication
from releasekit.exposure import audit, rules
from releasekit.release import coordinator
from releasekit.result import Result

HOSTED = "https://example.invalid/project.git"
POLICY = "[exposure]\ncheck_secrets = false\ncheck_links = false\ninspect_archives = false\n"
OBSERVATION = rules.PrivatePattern(
    "owner-observation", rules.MACHINE_OBSERVATION, r"seen on the maintainer's desk"
)


class PublishedRepository(PushRepository):
    """`origin` is a bare repository on disk, named by a hosted URL once pushed to."""

    def setUp(self) -> None:
        super().setUp()
        self.write("relkit.toml", POLICY)
        self.commit("chore: read no archives")
        self.push()

    def push(self, *refs: str) -> None:
        """Push to the bare repository, then name `origin` as a host would be named."""
        self.git("remote", "set-url", "origin", str(self.remote))
        self.git("push", "-q", "--no-verify", "origin", *(refs or ("main",)))
        self.git("remote", "set-url", "origin", HOSTED)

    def verdict(
        self, *, forbid_machine_observations: bool = False, **options
    ) -> tuple[list[str], audit.PublishedFindings]:
        published, basis = audit.published_history(self.root, "origin")
        self.assertIsNotNone(published, basis)
        assert published is not None
        listed = audit.PublishedFindings(
            published, forbid_machine_observations=forbid_machine_observations
        )
        options.setdefault("names", (PRIVATE_VALUE,))
        # The fixture's projection is a script standing in for an archive.
        options.setdefault("inspect_archives", False)
        failures = audit.history_failures(
            self.root,
            forbid_machine_observations=forbid_machine_observations,
            listed=listed,
            **options,
        )
        return failures, listed

    def publish_value(self) -> str:
        self.write("notes.md", f"first line\nnames {PRIVATE_VALUE}\n")
        commit = self.commit(f"docs: mention {PRIVATE_VALUE}")
        self.push()
        return commit


class PublishedHistoryTests(PublishedRepository):
    def test_a_value_the_remote_holds_is_listed_and_not_refused(self) -> None:
        self.publish_value()

        failures, listed = self.verdict()

        self.assertEqual([], failures)
        subjects = {subject for subject, kind, _count in listed.entries()}
        self.assertEqual({"notes.md", "commit message"}, subjects)
        self.assertEqual({rules.DECLARED_NAME}, {kind for _s, kind, _c in listed.entries()})
        # The same history without the split is refused: the rule itself still holds.
        self.assertTrue(
            audit.history_failures(self.root, names=(PRIVATE_VALUE,), inspect_archives=False)
        )

    def test_a_value_only_in_unpushed_commits_is_refused(self) -> None:
        self.write("notes.md", f"names {PRIVATE_VALUE}\n")
        commit = self.commit("docs: an unpushed note")

        failures, listed = self.verdict()

        self.assertIn(f"history {commit[:12]}:notes.md:1: {rules.DECLARED_NAME}", failures)
        self.assertTrue(
            any(item.endswith(f":notes.md: {rules.DECLARED_NAME}") for item in failures),
            failures,
        )
        self.assertEqual([], listed.entries())

    def test_an_unpushed_commit_that_inherits_a_published_file_lists_it(self) -> None:
        # The field case: the search reads whole trees, so every later commit carries the
        # published file, and a merge lists the other parent's blobs as changed.
        start = self.head()
        self.publish_value()
        self.write("other.txt", "an unrelated change\n")
        self.commit("feat: change something else")
        self.git("switch", "-q", "-c", "side", start)
        self.write("side.txt", "a side change\n")
        self.commit("feat: a side change")
        self.git("merge", "-q", "--no-edit", "--no-verify", "main")

        failures, listed = self.verdict()

        self.assertEqual([], failures)
        self.assertIn("notes.md", {subject for subject, _kind, _count in listed.entries()})

    def test_a_new_blob_that_keeps_a_published_line_is_refused(self) -> None:
        self.publish_value()
        self.write("notes.md", f"first line\nnames {PRIVATE_VALUE}\nthird line\n")
        self.commit("docs: add a line")

        failures, _listed = self.verdict()

        self.assertTrue(
            any(
                item.startswith("history blob ")
                and item.endswith(f":notes.md: {rules.DECLARED_NAME}")
                for item in failures
            ),
            failures,
        )

    def test_a_local_tag_does_not_make_its_commit_published(self) -> None:
        self.git("switch", "-q", "-c", "candidate")
        self.write("notes.md", f"names {PRIVATE_VALUE}\n")
        commit = self.commit("docs: the candidate's note")
        self.git("tag", "-a", "v1.0.0", "-m", "release v1.0.0")
        self.git("switch", "-q", "main")
        self.git("branch", "-q", "-D", "candidate")

        failures, listed = self.verdict()

        self.assertIn(f"history {commit[:12]}:notes.md:1: {rules.DECLARED_NAME}", failures)
        self.assertEqual([], listed.entries())

    def test_a_name_in_a_published_path_is_listed_but_a_new_one_is_refused(self) -> None:
        self.write(f"docs/{PRIVATE_VALUE}.md", "a page\n")
        self.commit("docs: add a page")
        self.push()
        self.write(f"guides/{PRIVATE_VALUE}.md", "another page\n")
        self.commit("docs: add another page")

        failures, listed = self.verdict()

        self.assertEqual([f"history guides/{PRIVATE_VALUE}.md: {rules.DECLARED_NAME}"], failures)
        self.assertIn(
            (f"docs/{PRIVATE_VALUE}.md", rules.DECLARED_NAME),
            {(subject, kind) for subject, kind, _count in listed.entries()},
        )

    def test_public_rules_still_refuse_in_published_content(self) -> None:
        self.write("setup.md", "run /home/alice/project/start.sh\n")
        self.write(".someclient/settings.json", "{}\n")
        self.commit("docs: describe the setup")
        self.push()

        failures, _listed = self.verdict(private_paths=[".someclient"])

        self.assertTrue(any(rules.HOME_DIRECTORY in item for item in failures), failures)
        self.assertIn(f"history .someclient/settings.json: {rules.PRIVATE_PATH}", failures)

    def test_an_observation_is_listed_only_while_the_public_rule_is_off(self) -> None:
        # The public rule reports the same kind, and a finding cannot say which rule made it.
        self.write("journal.md", "This was seen on the maintainer's desk.\n")
        self.commit("docs: keep a journal")
        self.push()

        allowed, _listed = self.verdict(private_patterns=(OBSERVATION,))
        refused, _listed = self.verdict(
            private_patterns=(OBSERVATION,), forbid_machine_observations=True
        )

        self.assertEqual([], allowed)
        self.assertTrue(any(rules.MACHINE_OBSERVATION in item for item in refused), refused)

    def test_a_push_lists_nothing(self) -> None:
        published, _basis = audit.published_history(self.root, "origin")
        assert published is not None
        listed = audit.PublishedFindings(published, forbid_machine_observations=False)
        scope = self.scope(f"refs/heads/main {self.head()} refs/heads/main {self.head()}\n")

        with self.assertRaises(ValueError):
            audit.history_failures(self.root, scope=scope, listed=listed)


class NothingPublishedTests(PushRepository):
    """Where the remote cannot be shown to hold anything, every finding is judged."""

    def test_a_remote_at_a_path_on_this_machine_publishes_nothing(self) -> None:
        # A clone of the working checkout has the checkout's local branches as `origin`.
        published, basis = audit.published_history(self.root, "origin")

        self.assertIsNone(published)
        self.assertIn("path on this machine", basis)

    def test_a_remote_without_remote_tracking_refs_publishes_nothing(self) -> None:
        self.git("remote", "set-url", "origin", HOSTED)
        for reference in self.git("for-each-ref", "--format=%(refname)", "refs/remotes").split():
            self.git("update-ref", "-d", reference)

        published, basis = audit.published_history(self.root, "origin")

        self.assertIsNone(published)
        self.assertIn("no remote-tracking refs", basis)

    def test_an_unconfigured_remote_publishes_nothing(self) -> None:
        published, basis = audit.published_history(self.root, "upstream")

        self.assertIsNone(published)
        self.assertIn("not a configured remote", basis)

    def test_urls_are_read_as_git_reads_them(self) -> None:
        for url, local in (
            ("https://example.invalid/project.git", False),
            ("ssh://git@example.invalid/project.git", False),
            ("git@example.invalid:owner/project.git", False),
            ("file:///srv/project.git", True),
            ("/srv/project.git", True),
            ("../project.git", True),
            ("project.git", True),
        ):
            with self.subTest(url=url):
                self.assertIs(local, audit._local_url(url))


class AuditCommandTests(PublishedRepository):
    """`audit --history` lists what the remote holds and refuses what it does not."""

    def audit(self) -> tuple[int, dict, str]:
        out, err = io.StringIO(), io.StringIO()
        arguments = ["audit", "--history", "--owner", "--sandbox", "--no-download", "--json"]
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([*arguments, "--root", str(self.root)])
        return code, json.loads(out.getvalue()), err.getvalue()

    def publish_and_drop_value(self) -> None:
        """The value stays in what the remote holds; the tree a release ships drops it."""
        self.publish_value()
        self.write("notes.md", "first line\n")
        self.commit("docs: drop the mention")

    def test_the_history_audit_passes_and_lists_what_the_remote_holds(self) -> None:
        self.publish_and_drop_value()

        code, envelope, err = self.audit()

        self.assertEqual(0, code, err)
        published = envelope["data"]["already_published"]
        self.assertEqual("origin", published["remote"])
        self.assertIn(
            {"subject": "notes.md", "kind": rules.DECLARED_NAME},
            [{"subject": item["subject"], "kind": item["kind"]} for item in published["findings"]],
        )
        self.assertIn("already_published", [item["code"] for item in envelope["warnings"]])
        self.assertIn("refs/remotes/origin/", err)
        self.assertIn(f"  notes.md: {rules.DECLARED_NAME} (", err)

    def test_the_tree_a_release_ships_is_still_judged(self) -> None:
        # Listing covers history only: a release of this tree would publish the value anew.
        self.publish_value()

        code, envelope, err = self.audit()

        self.assertEqual(1, code, err)
        self.assertIn(
            f"notes.md: {rules.DECLARED_NAME}", [item["message"] for item in envelope["errors"]]
        )

    def test_the_history_audit_refuses_a_value_the_remote_does_not_hold(self) -> None:
        self.publish_and_drop_value()
        self.write("draft.md", f"names {PRIVATE_VALUE}\n")
        self.commit("docs: an unpushed draft")

        code, envelope, err = self.audit()

        self.assertEqual(1, code, err)
        messages = [item["message"] for item in envelope["errors"]]
        self.assertTrue(any("draft.md" in message for message in messages), messages)
        self.assertFalse(any(":notes.md" in message for message in messages), messages)

    def test_the_release_remote_decides_what_counts_as_published(self) -> None:
        self.publish_value()
        # Only `upstream` now holds the value; `origin` points back at this machine.
        self.git("remote", "add", "upstream", "https://example.invalid/upstream.git")
        self.git("update-ref", "refs/remotes/upstream/main", "refs/remotes/origin/main")
        self.git("remote", "set-url", "origin", str(self.remote))
        self.write(
            "relkit.toml",
            POLICY
            + "\n[release]\nversion_file = \"VERSION\"\nversion_pattern = '^(.+)$'\n"
            + 'assets = []\nchecks = [["python", "-c", "pass"]]\n'
            + 'smoke = [["python", "-c", "pass"]]\nsmoke_platforms = ["linux"]\n'
            + 'remote = "upstream"\n',
        )
        self.commit("chore: release from upstream")
        settings = config.load(self.root)

        failures, listed, basis = publication._history_with_published(
            self.root,
            settings,
            publication.owner.OwnerRules(private_values=(PRIVATE_VALUE,)),
            {"names": (PRIVATE_VALUE,), "inspect_archives": False},
        )

        self.assertEqual([], failures)
        assert listed is not None
        self.assertEqual("upstream", listed.published.remote)
        self.assertIn("refs/remotes/upstream/", basis)


class ReleaseConsoleTests(PublishedRepository):
    """`release prepare` writes the audit to its log, so the listing is named on screen."""

    def test_the_release_names_the_listing_and_where_it_is(self) -> None:
        log = self.base / "release.log"
        runner = types.SimpleNamespace(root=self.root, log=log)
        release = types.SimpleNamespace(owner_audit=True)

        def audited(*_arguments, result: Result, **_options) -> int:
            result.warnings.append({"code": "already_published", "message": "3 listed"})
            return 0

        out = io.StringIO()
        with (
            patch.object(coordinator.publication, "run", side_effect=audited),
            redirect_stdout(out),
        ):
            coordinator._audit(runner, release, history=True, no_download=True)

        self.assertIn(f"relkit release: 3 listed; listed in {log}", out.getvalue())
