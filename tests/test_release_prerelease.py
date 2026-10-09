"""Prerelease release identities and their stable publication boundary."""

import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from test_release import ReleaseFixture
from test_release_local import LocalFixture

from releasekit import canonical, config, storage
from releasekit.release import candidate, coordinator, versions
from releasekit.release.backend import GitHub, ReleaseError, remote_refs
from releasekit.release.directory import Directory
from releasekit.result import Result


def invoke(fixture, version, action, **kwargs):
    result = Result()
    output = io.StringIO()
    with (
        contextlib.redirect_stdout(output),
        contextlib.redirect_stderr(output),
        patch.object(coordinator, "_audit"),
    ):
        code = coordinator.run(
            fixture.root,
            action,
            version,
            publish=action in {"run", "resume"},
            runner=fixture.runner,
            github=fixture.github,
            result=result,
            **kwargs,
        )
    return code, result, output.getvalue()


class ReleaseVersionBoundaryTests(unittest.TestCase):
    def test_explicit_semver_and_optional_tag_prefix_are_accepted(self):
        for text in ["1.2.3-rc", "v1.2.3-rc.1", "V1.2.3-alpha.2+build.003"]:
            with self.subTest(text=text):
                version = text.removeprefix("v").removeprefix("V")
                self.assertEqual((version, "v" + version), coordinator.version_tag(text))

    def test_non_ascii_numbers_and_malformed_prereleases_are_rejected(self):
        for text in ["1.2.3١", "v1.2.3-rc.01", "1.2.3-rc_1", "1.2.3+meta..1"]:
            with self.subTest(text=text), self.assertRaises(ReleaseError):
                coordinator.version_tag(text)


class PrereleaseBoundaryTests(ReleaseFixture):
    def publication(self, tag):
        self.runner.git("tag", tag)
        self.runner.git("push", "origin", f"refs/tags/{tag}")
        identifier = len(self.github.published) + 30
        self.github.published.append(
            {
                "tag_name": tag,
                "id": identifier,
                "draft": False,
                "prerelease": "-" in tag.split("+", 1)[0],
            }
        )
        return identifier

    def previous(self, version, first="1.0.0"):
        return versions.predecessor(
            self.runner, self.github, remote_refs(self.runner, "origin"), version, self.sha, first
        )

    def next(self, prerelease="", bump="major", first="1.0.0"):
        return versions.next_version(
            self.runner, self.github, config.load(self.root).release, first, bump, prerelease
        )

    def test_stable_and_prerelease_predecessors_follow_distinct_publication_boundaries(self):
        self.publication("v0.9.0")
        self.publication("v1.0.0-rc.2")
        self.publication("v1.0.0-rc.10")
        self.publication("v2.0.0-rc.1")
        self.assertEqual("v0.9.0", self.previous("1.0.0")["tag"])
        self.assertEqual("v1.0.0-rc.10", self.previous("1.0.0-rc.11")["tag"])
        self.assertEqual("v0.9.0", self.previous("0.10.0-beta")["tag"])

    def test_initial_stable_declaration_allows_rc_and_initial_rc_allows_final(self):
        self.assertIsNone(self.previous("1.0.0-rc"))
        self.publication("v1.0.0-rc")
        self.assertIsNone(self.previous("1.0.0", first="1.0.0-rc"))
        with self.assertRaisesRegex(ReleaseError, "initial release line"):
            self.previous("2.0.0-rc")

    def test_prerelease_must_advance_even_when_only_build_metadata_changes(self):
        self.publication("v0.9.0")
        self.publication("v1.0.0-rc.2+build.1")
        for target in ["1.0.0-rc.1", "1.0.0-rc.2", "1.0.0-rc.2+build.2"]:
            with self.subTest(target=target), self.assertRaisesRegex(ReleaseError, "must advance"):
                self.previous(target)
        self.assertEqual("v1.0.0-rc.2+build.1", self.previous("1.0.0-rc.3")["tag"])

    def test_equal_precedence_publications_need_an_explicit_identity_reconciliation(self):
        self.publication("v0.9.0+build.1")
        self.publication("v0.9.0+build.2")
        with self.assertRaisesRegex(ReleaseError, "equal SemVer precedence"):
            self.next()

    def test_next_rc_increments_published_numbers_and_reports_an_occupied_retry(self):
        self.publication("v0.9.0")
        self.publication("v1.0.0-rc.2")
        self.publication("v1.0.0-rc.10")
        self.runner.git("tag", "v1.0.0-rc.11")
        value = self.next("rc")
        self.assertEqual("v1.0.0-rc.11", value["tag"])
        self.assertEqual("occupied", value["tag_state"])
        self.assertEqual("v1.0.0-rc.10", value["previous"]["tag"])
        stable = self.next()
        self.assertEqual("v1.0.0", stable["tag"])
        self.assertEqual("v0.9.0", stable["previous"]["tag"])

    def test_next_starts_at_declared_core_and_respects_a_declared_rc_floor(self):
        self.assertEqual("v1.0.0-rc.1", self.next("rc")["tag"])
        self.assertEqual("v1.0.0-rc.9", self.next("rc", first="1.0.0-rc.9")["tag"])
        self.assertEqual("v1.0.0", self.next(first="1.0.0-rc.9")["tag"])
        self.publication("v1.0.0-rc")
        self.assertEqual("v1.0.0-rc.1", self.next("rc")["tag"])

    def test_next_channel_cannot_move_backwards_or_accept_an_invalid_identifier(self):
        for label in ["1", "rc.1", "rc_1", "候选", " rc"]:
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(ReleaseError, "one nonnumeric ASCII"),
            ):
                self.next(label)
        self.publication("v1.0.0-rc.1")
        with self.assertRaisesRegex(ReleaseError, "cannot advance"):
            self.next("beta")

    def test_deleted_recorded_rc_is_detected_even_when_selecting_a_stable_release(self):
        self.publication("v0.9.0")
        identifier = self.publication("v1.0.0-rc.1")
        value = {"root": str(self.root), "tag": "v1.0.0-rc.1"}
        storage.atomic_json(
            coordinator._state_path(self.root, "v1.0.0-rc.1"),
            {
                "plan": value,
                "plan_sha256": canonical.fingerprint(value),
                "publication": "published",
                "release_id": identifier,
            },
        )
        self.github.published.pop()
        with self.assertRaisesRegex(ReleaseError, "disappeared"):
            self.next()

    def test_final_notes_must_carry_commit_links_from_published_rcs(self):
        self.publication("v0.9.0")
        (self.root / "change.txt").write_text("change shipped first in an RC")
        self.commit()
        change = self.sha
        self.publication("v1.0.0-rc.1")
        previous = self.previous("1.0.0")
        linked = f"- Feature ([{change[:8]}](https://github.com/example/project/commit/{change}))."
        history = "## [1.0.0-rc.1]\n\n### Features\n\n" + linked
        with self.assertRaisesRegex(ReleaseError, "omits change"):
            versions.check_carried_changes(
                self.runner, history, "## [1.0.0]\n\n### Highlights\n\n- Final.", "1.0.0", previous
            )
        versions.check_carried_changes(
            self.runner, history, "## [1.0.0]\n\n### Features\n\n" + linked, "1.0.0", previous
        )


class DirectoryPrereleaseTests(ReleaseFixture):
    def setUp(self):
        super().setUp()
        policy = self.root / "relkit.toml"
        policy.write_text(
            "[exposure]\ncheck_secrets = false\ncheck_links = false\n"
            '[changelog]\nprofile = "conventional-changelog"\nfirst_version = "1.0.0"\n'
            '[release]\npublisher = "directory"\nversion_file = "VERSION"\n'
            'version_pattern = "^(.+)$"\nassets = []\n'
            'checks = [["{python}", "check.py", "{version}"]]\n'
            'smoke = [["{python}", "smoke.py", "{assets}", "{version}"]]\n'
            'smoke_platforms = ["linux", "darwin", "win32"]\n',
            encoding="utf-8",
        )
        (self.root / "check.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "assert Path('VERSION').read_text().strip() == sys.argv[1]\n"
        )
        (self.root / "smoke.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "assert Path('VERSION').read_text().strip() == sys.argv[2]\n"
            "assert not Path('.git').exists()\n"
            "assert list(Path(sys.argv[1]).iterdir()) == []\n"
        )
        self.history = ""

    def release_source(self, version, previous=None):
        heading = f"## [{version}]"
        if previous:
            heading += f"(https://example.invalid/project/compare/{previous}...v{version})"
        notes = heading + " (2026-10-09)\n\n### Highlights\n\n- Reviewed release."
        (self.root / "VERSION").write_text(version + "\n")
        (self.root / "CHANGELOG.md").write_text(notes + "\n\n" + self.history)
        self.commit()
        self.history = (self.root / "CHANGELOG.md").read_text()

    def test_rc_releases_and_final_are_published_verified_and_advanced_offline(self):
        self.release_source("1.0.0-rc.1")
        code, result, output = invoke(self, "1.0.0-rc.1", "run", prepare_here=True)
        self.assertEqual(0, code, output)
        self.assertEqual("published", result.data["release"]["publication"])
        store = Directory(self.runner, config.load(self.root).release)
        self.assertTrue(store.release("v1.0.0-rc.1")["prerelease"])
        code, _, output = invoke(self, "1.0.0-rc.1", "verify")
        self.assertEqual(0, code, output)
        code, result, output = invoke(self, "", "next", bump="patch", prerelease="rc")
        self.assertEqual(0, code, output)
        self.assertEqual("v1.0.0-rc.2", result.data["next"]["tag"])
        self.assertEqual("v1.0.0-rc.1", result.data["next"]["previous"]["tag"])

        self.release_source("1.0.0-rc.2", "v1.0.0-rc.1")
        code, _, output = invoke(self, "1.0.0-rc.2", "run", prepare_here=True)
        self.assertEqual(0, code, output)
        self.release_source("1.0.0")
        code, result, output = invoke(self, "1.0.0", "run", prepare_here=True)
        self.assertEqual(0, code, output)
        self.assertIsNone(result.data["release"]["plan"]["previous"])
        self.assertFalse(store.release("v1.0.0")["prerelease"])
        code, result, output = invoke(self, "", "next", bump="patch")
        self.assertEqual(0, code, output)
        self.assertEqual("v1.0.1", result.data["next"]["tag"])
        self.assertEqual("v1.0.0", result.data["next"]["previous"]["tag"])
        self.assertEqual(0, self.runner.pushes)


class GitHubPrereleaseTests(LocalFixture):
    def setUp(self):
        super().setUp()
        self.version = "1.0.0-rc.1"
        self.notes = self.notes.replace("1.0.0", self.version)
        for name in ["VERSION", "CHANGELOG.md", "check.py", "smoke.py"]:
            path = self.root / name
            path.write_text(path.read_text().replace("1.0.0", self.version))
        self.commit()

    def test_interrupted_github_rc_publication_resumes_without_duplicate_writes(self):
        code, _, output = invoke(self, self.version, "prepare")
        self.assertEqual(0, code, output)
        self.github.lose = "publish"
        code, _, output = invoke(self, self.version, "run")
        self.assertEqual(3, code, output)
        self.assertTrue(self.github.remote_release["prerelease"])
        code, _, output = invoke(self, self.version, "resume")
        self.assertEqual(0, code, output)
        code, _, output = invoke(self, self.version, "verify")
        self.assertEqual(0, code, output)
        self.assertEqual(1, self.github.create_calls)
        self.assertEqual(1, self.github.publish_calls)
        self.assertEqual(1, self.runner.pushes)
        self.github.remote_release["prerelease"] = False
        code, _, output = invoke(self, self.version, "verify")
        self.assertEqual(1, code, output)
        self.assertIn("planned prerelease status", output)
        self.assertEqual(1, self.github.publish_calls)

    def test_native_create_request_derives_prerelease_flag_from_exact_tag(self):
        github = GitHub(self.runner, "example/project")
        payloads = []

        def create(_path, *, method, payload):
            self.assertEqual("POST", method)
            value = json.loads(payload.read_text())
            payloads.append(value)
            return {"id": 45, **value}

        with (
            patch.object(github, "tag_object", return_value="a" * 40),
            patch.object(github, "api", side_effect=create),
        ):
            for tag in ["v1.0.0-rc.1", "v1.0.0"]:
                github.create_draft(
                    tag, self.sha, tag, self.root / "CHANGELOG.md", tag_oid="a" * 40
                )
        self.assertTrue(payloads[0]["prerelease"])
        self.assertFalse(payloads[1]["prerelease"])
        self.assertEqual("v1.0.0-rc.1", payloads[0]["tag_name"])


class ActionsPrereleaseTests(unittest.TestCase):
    def test_tagless_rc_candidate_promotes_and_validates_a_prerelease_draft(self):
        from test_release_candidate import CandidateTests

        with contextlib.ExitStack() as cleanup:
            fixture = CandidateTests()
            cleanup.callback(fixture.doCleanups)
            fixture.setUp()
            version = "1.0.0-rc.1"
            fixture.notes = fixture.notes.replace("1.0.0", version)
            for name in ["VERSION", "CHANGELOG.md", "check.py", "smoke.py"]:
                path = fixture.root / name
                path.write_text(path.read_text().replace("1.0.0", version))
            fixture.commit()
            (fixture.output / candidate.MANIFEST).unlink()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "GITHUB_SHA": fixture.sha,
                    "GITHUB_REPOSITORY": "example/project",
                    "GITHUB_RUN_ID": "35",
                    "GITHUB_RUN_ATTEMPT": "1",
                },
            ):
                fixture.record = candidate.bundle(fixture.runner, version, fixture.output)
            fixture.run_data["head_sha"] = fixture.sha
            code, _, output = invoke(fixture, version, "prepare", ci_run=35)
            self.assertEqual(0, code, output)
            value = coordinator.plan(fixture.runner, version, github=fixture.github)
            self.assertEqual("", fixture.runner.git("tag", "--list"))
            fixture.runner.git(
                "tag", "--annotate", "v" + version, "--message", coordinator._tag_message(value)
            )
            target = fixture.root / ".cache/promoted-rc"
            self.assertEqual(
                fixture.record, candidate.promote(fixture.runner, fixture.github, version, target)
            )
            fixture.github.orphan_release = fixture.github.draft = True
            original = fixture.github.release
            fixture.github.release = lambda tag: {**original(tag), "prerelease": True}
            self.assertEqual(
                "passed",
                candidate.draft(fixture.runner, fixture.github, version, target)["verification"],
            )
            fixture.github.release = original
            with self.assertRaisesRegex(ReleaseError, "planned prerelease status"):
                candidate.draft(fixture.runner, fixture.github, version, target)
