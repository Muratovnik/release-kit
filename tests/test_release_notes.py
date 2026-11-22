"""`release notes`: republish the notes of a published release from a later changelog."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_release import FakeGitHub, ReleaseFixture
from test_release_local import DirectoryFixture

from releasekit.release import coordinator
from releasekit.release.backend import GitHub, Pending, Runner
from releasekit.result import Result

REWRITTEN = (
    "## [1.0.0] (2026-09-02)\n\n### Highlights\n\n"
    "- First useful release, now described for its readers."
)


class NotesGitHub(FakeGitHub):
    """The fixture's hosted release, with a body that an edit really replaces."""

    def __init__(self, fixture):
        super().__init__(fixture)
        self.body = None
        self.edits = []
        self.ignore_edit = False
        self.edit_times_out = False

    def release(self, tag):
        found = super().release(tag)
        if found is not None and self.body is not None:
            found = {**found, "body": self.body}
        return found

    def edit_notes(self, release_id, body, directory):
        self.fixture.assertTrue(Path(directory).is_relative_to(self.fixture.root))
        self.edits.append((release_id, body))
        if self.edit_times_out:
            # The runner's generic timeout, hint included, as `Runner.call` raises it.
            raise Pending(
                "gh timed out after 120s; if it changed remote state, "
                "reconcile with `relkit release resume VERSION --publish`"
            )
        if not self.ignore_edit:
            self.body = body
        return {"id": release_id, "body": body}


class NotesFixture(ReleaseFixture):
    def setUp(self):
        super().setUp()
        self.github = NotesGitHub(self)

    def publish_release(self):
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertEqual("published", self.receipt()["publication"])

    def commit_changelog(self, text):
        """Commit a rewritten changelog after the tag; `self.sha` stays the tag commit."""
        (self.root / "CHANGELOG.md").write_text(text + "\n", encoding="utf-8")
        self.runner.git("add", "CHANGELOG.md")
        self.runner.git("commit", "-qm", "docs(changelog): describe 1.0.0 for its readers")
        return self.runner.git("rev-parse", "HEAD")

    def run_notes(self, *, publish, **kwargs):
        result = Result()
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            code = coordinator.run(
                self.root,
                "notes",
                "v1.0.0",
                publish=publish,
                runner=self.runner,
                github=self.github,
                result=result,
                **kwargs,
            )
        return code, stream.getvalue(), result

    def receipt_bytes(self):
        return coordinator._state_path(self.root, "v1.0.0").read_bytes()


class ReleaseNotesTests(NotesFixture):
    def test_the_plan_shows_the_entry_and_changes_neither_the_release_nor_the_receipt(self):
        self.publish_release()
        head = self.commit_changelog(REWRITTEN)
        before = self.receipt_bytes()
        code, output, result = self.run_notes(publish=False)
        self.assertEqual(0, code, output)
        data = result.data["notes"]
        self.assertEqual(head, data["source"])
        self.assertEqual(REWRITTEN, data["notes"])
        self.assertEqual(coordinator.fingerprint(REWRITTEN), data["notes_sha256"])
        self.assertEqual(coordinator.fingerprint(self.notes), data["hosted_notes_sha256"])
        self.assertTrue(data["differs"])
        self.assertFalse(data["recorded"])
        self.assertFalse(data["edited"])
        self.assertIsNone(data["revision"])
        self.assertEqual([], self.github.edits)
        self.assertEqual(before, self.receipt_bytes())
        self.assertFalse((self.root / ".git/relkit/release.lock").exists())

    def test_refuses_without_a_receipt(self):
        head = self.commit_changelog(REWRITTEN)
        code, output, result = self.run_notes(publish=True)
        self.assertEqual(2, code, output)
        self.assertIn("no release receipt for v1.0.0", output)
        self.assertEqual("release_error", result.errors[0]["code"])
        self.assertEqual([], self.github.edits)
        self.assertEqual(head, self.runner.git("rev-parse", "HEAD"))

    def test_refuses_a_receipt_that_records_no_publication(self):
        self.github.draft = True
        self.assertEqual(3, self.invoke()[0])
        self.assertEqual("draft", self.receipt()["publication"])
        self.commit_changelog(REWRITTEN)
        for publish in (False, True):
            code, output, _ = self.run_notes(publish=publish)
            self.assertEqual(2, code, output)
            self.assertIn("records publication 'draft'", output)
        self.assertEqual([], self.github.edits)
        self.assertNotIn("notes_revisions", self.receipt())

    def test_refuses_a_changelog_that_is_not_committed_and_clean(self):
        self.publish_release()
        (self.root / "CHANGELOG.md").write_text(REWRITTEN + "\n", encoding="utf-8")
        code, output, _ = self.run_notes(publish=True)
        self.assertEqual(2, code, output)
        self.assertIn("CHANGELOG.md has uncommitted changes", output)
        self.runner.git("add", "CHANGELOG.md")
        code, output, _ = self.run_notes(publish=True)
        self.assertEqual(2, code, output)
        self.assertIn("CHANGELOG.md has uncommitted changes", output)
        self.assertEqual([], self.github.edits)
        self.assertNotIn("notes_revisions", self.receipt())

    def test_refuses_a_head_that_does_not_descend_from_the_tag(self):
        self.publish_release()
        self.runner.git("checkout", "-q", "--orphan", "unrelated")
        self.commit_changelog(REWRITTEN)
        code, output, _ = self.run_notes(publish=True)
        self.assertEqual(2, code, output)
        self.assertIn("does not descend from the v1.0.0 commit", output)
        self.assertEqual([], self.github.edits)

    def test_refuses_an_entry_that_fails_the_configured_profile(self):
        self.publish_release()
        self.commit_changelog(
            "## [1.0.0] (2026-09-02)\n\n### Features\n\n- a change that links no commit"
        )
        code, output, _ = self.run_notes(publish=True)
        self.assertEqual(2, code, output)
        self.assertIn("needs a commit link", output)
        self.assertEqual([], self.github.edits)
        self.assertNotIn("notes_revisions", self.receipt())

    def test_publish_edits_the_body_reads_it_back_and_records_the_revision(self):
        self.publish_release()
        head = self.commit_changelog(REWRITTEN)
        code, output, result = self.run_notes(publish=True)
        self.assertEqual(0, code, output)
        self.assertEqual([(21, REWRITTEN)], self.github.edits)
        data = result.data["notes"]
        self.assertTrue(data["edited"])
        self.assertTrue(data["recorded"])
        revisions = self.receipt()["notes_revisions"]
        self.assertEqual([data["revision"]], revisions)
        self.assertEqual(head, revisions[0]["source"])
        self.assertEqual(coordinator.fingerprint(REWRITTEN), revisions[0]["notes_sha256"])
        self.assertEqual(coordinator.fingerprint(self.notes), revisions[0]["previous_notes_sha256"])
        self.assertTrue(revisions[0]["edited"])
        self.assertRegex(revisions[0]["at"], r"^\d{4}-\d\d-\d\dT.*\+00:00$")
        self.assertFalse((self.root / ".git/relkit/release.lock").exists())
        self.assertEqual([], list((self.root / ".git/relkit/tmp").glob("notes-*")))

    def test_a_body_that_does_not_read_back_as_the_entry_records_nothing(self):
        self.publish_release()
        self.commit_changelog(REWRITTEN)
        self.github.ignore_edit = True
        code, output, _ = self.run_notes(publish=True)
        self.assertEqual(2, code, output)
        self.assertIn("differ from the committed entry after the edit", output)
        self.assertEqual(1, len(self.github.edits))
        self.assertNotIn("notes_revisions", self.receipt())

    def test_a_timed_out_edit_points_back_to_release_notes_and_records_nothing(self):
        self.publish_release()
        self.commit_changelog(REWRITTEN)
        self.github.edit_times_out = True
        code, output, _ = self.run_notes(publish=True)
        self.assertEqual(3, code, output)
        self.assertIn("run `relkit release notes v1.0.0 --publish` again", output)
        self.assertNotIn("release resume", output)
        self.assertNotIn("notes_revisions", self.receipt())
        self.assertFalse((self.root / ".git/relkit/release.lock").exists())

    def test_a_rerun_edits_nothing_and_records_the_revision_once(self):
        self.publish_release()
        self.commit_changelog(REWRITTEN)
        # The body already says what the entry says, for example after an interrupted run.
        self.github.body = REWRITTEN
        code, output, result = self.run_notes(publish=True)
        self.assertEqual(0, code, output)
        self.assertEqual([], self.github.edits)
        self.assertFalse(result.data["notes"]["edited"])
        self.assertFalse(self.receipt()["notes_revisions"][0]["edited"])
        before = self.receipt_bytes()
        code, output, result = self.run_notes(publish=True)
        self.assertEqual(0, code, output)
        self.assertEqual([], self.github.edits)
        self.assertIsNone(result.data["notes"]["revision"])
        self.assertEqual(before, self.receipt_bytes())

    def test_verify_accepts_the_recorded_revision(self):
        self.publish_release()
        self.commit_changelog(REWRITTEN)
        self.assertEqual(0, self.run_notes(publish=True)[0])
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(0, code, output)
        self.assertEqual(
            coordinator.fingerprint(REWRITTEN), self.receipt()["artifacts"]["notes_sha256"]
        )

    def test_verify_still_refuses_a_body_changed_without_the_command(self):
        self.publish_release()
        self.commit_changelog(REWRITTEN)
        self.github.body = REWRITTEN
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(1, code, output)
        self.assertIn("published notes differ from the committed changelog entry", output)
        # After a recorded revision, a further hand edit is refused the same way.
        self.github.body = None
        self.assertEqual(0, self.run_notes(publish=True)[0])
        self.github.body = REWRITTEN + "\n\nEdited by hand."
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(1, code, output)
        self.assertIn("published notes differ from the committed changelog entry", output)

    def test_verify_extracts_the_revision_again_instead_of_trusting_the_receipt(self):
        self.publish_release()
        self.commit_changelog(REWRITTEN)
        self.assertEqual(0, self.run_notes(publish=True)[0])
        path = coordinator._state_path(self.root, "v1.0.0")
        state = json.loads(path.read_text(encoding="utf-8"))
        state["notes_revisions"][0]["notes_sha256"] = coordinator.fingerprint("other notes")
        path.write_text(json.dumps(state), encoding="utf-8")
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(1, code, output)
        self.assertIn("the receipt and the repository disagree", output)

    def test_a_receipt_without_revisions_verifies_against_the_tag_entry_as_before(self):
        self.publish_release()
        self.assertNotIn("notes_revisions", self.receipt())
        artifacts = self.receipt()["artifacts"]
        # A later rewrite that was never republished changes nothing for verify.
        self.commit_changelog(REWRITTEN)
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(0, code, output)
        self.assertEqual(artifacts, self.receipt()["artifacts"])
        self.assertNotIn("notes_revisions", self.receipt())

    def test_a_malformed_revision_makes_the_receipt_unreadable(self):
        self.publish_release()
        path = coordinator._state_path(self.root, "v1.0.0")
        state = json.loads(path.read_text(encoding="utf-8"))
        state["notes_revisions"] = [{"source": "HEAD"}]
        path.write_text(json.dumps(state), encoding="utf-8")
        code, output = self.invoke("status", publish=False)
        self.assertEqual(2, code, output)
        self.assertIn("notes_revisions", output)

    def test_the_release_lock_is_held_while_publishing(self):
        self.publish_release()
        self.commit_changelog(REWRITTEN)
        lock = coordinator._acquire_lock(self.root, "v1.0.0")
        try:
            code, output, _ = self.run_notes(publish=True)
        finally:
            lock.unlink()
        self.assertEqual(2, code, output)
        self.assertIn("another release owns", output)
        self.assertEqual([], self.github.edits)

    def test_flags_of_other_actions_are_refused(self):
        self.publish_release()
        for flags in ({"plan_hash": "0" * 64}, {"no_download": True}, {"reason": "why"}):
            code, output, _ = self.run_notes(publish=True, **flags)
            self.assertEqual(2, code, output)
        self.assertEqual([], self.github.edits)


class DirectoryNotesTests(DirectoryFixture):
    def test_the_directory_publisher_refuses_to_rewrite_notes(self):
        with patch.object(coordinator, "_audit"):
            self.assertEqual(0, self.invoke("prepare", publish=False)[0])
            code, output = self.invoke()
        self.assertEqual(0, code, output)
        manifest = self.root / ".cache/releases/v1.0.0/relkit-release.json"
        before = manifest.read_bytes()
        code, output = self.invoke("notes", publish=True)
        self.assertEqual(2, code, output)
        self.assertIn("directory publisher never rewrites a publication", output)
        self.assertEqual(before, manifest.read_bytes())


class GitHubEditTests(unittest.TestCase):
    def test_the_body_is_patched_through_a_request_file_that_is_removed(self):
        runner = Runner(Path("."))
        github = GitHub(runner, "example/project")
        seen = {}

        def call(args, **_kwargs):
            request = Path(args[args.index("--input") + 1])
            seen["body"] = json.loads(request.read_text(encoding="utf-8"))
            seen["args"] = args
            return json.dumps({"id": 21, "body": seen["body"]["body"]})

        with tempfile.TemporaryDirectory() as temporary, patch.object(runner, "call", call):
            answer = github.edit_notes(21, REWRITTEN, Path(temporary))
            self.assertEqual([], list(Path(temporary).iterdir()))
        self.assertEqual({"body": REWRITTEN}, seen["body"])
        self.assertEqual("PATCH", seen["args"][seen["args"].index("--method") + 1])
        self.assertIn("repos/example/project/releases/21", seen["args"])
        self.assertNotIn(REWRITTEN, " ".join(seen["args"]))
        self.assertEqual(21, answer["id"])


if __name__ == "__main__":
    unittest.main()
