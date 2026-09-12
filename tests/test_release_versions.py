from __future__ import annotations

from test_release import ReleaseFixture

from releasekit import canonical, config, storage
from releasekit.release import coordinator, versions
from releasekit.release.backend import ReleaseError, remote_refs


class PublishedVersionsTests(ReleaseFixture):
    def test_deleted_known_publication_cannot_become_a_failed_candidate(self):
        self.publish_previous()
        value = {"root": str(self.root), "tag": "v0.9.0"}
        path = coordinator._state_path(self.root, "v0.9.0")
        storage.atomic_json(
            path,
            {
                "plan": value,
                "plan_sha256": canonical.fingerprint(value),
                "publication": "published",
                "release_id": 20,
            },
        )
        self.github.published.clear()
        with self.assertRaisesRegex(ReleaseError, "disappeared"):
            self.next()

    def test_skipped_candidate_changes_must_be_in_curated_current_entry(self):
        self.publish_previous()
        (self.root / "change.txt").write_text("change from failed candidate")
        self.commit()
        change = self.sha
        old = f"## [0.9.1]\n\n- Change ([{change[:8]}](https://github.com/example/project/commit/{change})).\n"
        previous = self.next()["previous"]
        with self.assertRaisesRegex(ReleaseError, "omits change"):
            versions.check_carried_changes(self.runner, old, "## [1.0.0]\n", "1.0.0", previous)
        versions.check_carried_changes(
            self.runner, old, old.replace("0.9.1", "1.0.0"), "1.0.0", previous
        )

    def test_other_destination_receipt_does_not_redefine_current_history(self):
        self.publish_previous()
        value = {"root": str(self.root), "tag": "v0.8.0", "repository_id": "directory:elsewhere"}
        storage.atomic_json(
            coordinator._state_path(self.root, "v0.8.0"),
            {
                "plan": value,
                "plan_sha256": canonical.fingerprint(value),
                "publication": "published",
                "release_id": "elsewhere",
            },
        )
        self.assertEqual("v0.9.1", self.next()["tag"])

    def publish_previous(self, tag="v0.9.0"):
        self.runner.git("tag", tag)
        self.runner.git("push", "origin", f"refs/tags/{tag}")
        self.github.published.append(
            {"id": 20, "tag_name": tag, "draft": False, "prerelease": False}
        )

    def next(self, bump="patch"):
        policy = config.load(self.root)
        return versions.next_version(
            self.runner, self.github, policy.release, policy.changelog.first_version, bump
        )

    def test_failed_tag_does_not_consume_version_and_collision_is_reported(self):
        self.publish_previous()
        self.runner.git("tag", "v0.9.1")
        self.runner.git("push", "origin", "refs/tags/v0.9.1")
        value = self.next()
        self.assertEqual("v0.9.1", value["tag"])
        self.assertEqual("occupied", value["tag_state"])
        self.assertEqual("v0.9.0", value["previous"]["tag"])
        self.assertEqual("v0.10.0", self.next("minor")["tag"])
        self.assertEqual("v1.0.0", self.next("major")["tag"])

    def test_draft_prerelease_and_unpublished_higher_tags_are_not_boundaries(self):
        self.publish_previous()
        for tag, draft, prerelease in [("v0.10.0", True, False), ("v0.11.0", False, True)]:
            self.github.published.append(
                {"id": 25, "tag_name": tag, "draft": draft, "prerelease": prerelease}
            )
        self.runner.git("tag", "v99.0.0")
        self.assertEqual("v0.9.1", self.next()["tag"])

    def test_published_tag_requires_matching_local_and_remote_identity(self):
        self.publish_previous()
        self.runner.git("tag", "--delete", "v0.9.0")
        with self.assertRaisesRegex(ReleaseError, "differs locally/remotely"):
            self.next()

    def test_external_publication_is_used_without_a_local_receipt(self):
        self.publish_previous()
        self.assertEqual(20, self.next()["previous"]["release_id"])

    def test_first_release_is_explicit_and_invalid_bump_is_rejected(self):
        self.assertEqual("v1.0.0", self.next()["tag"])
        with self.assertRaisesRegex(ReleaseError, "--bump"):
            self.next("auto")
        policy = config.load(self.root)
        with self.assertRaisesRegex(ReleaseError, "first_version"):
            versions.next_version(self.runner, self.github, policy.release, "", "patch")

    def test_plan_ignores_unpublished_higher_tag(self):
        self.runner.git("tag", "v99.0.0")
        value = coordinator.plan(self.runner, "1.0.0", github=self.github)
        self.assertIsNone(value["previous"])
        self.assertEqual("published-release", value["previous_source"])

    def test_unreachable_published_line_is_explicitly_rejected(self):
        self.publish_previous()
        (self.root / "feature.txt").write_text("later")
        self.commit()
        self.runner.git("tag", "v0.10.0")
        self.runner.git("push", "origin", "refs/tags/v0.10.0")
        self.github.published.append(
            {"id": 22, "tag_name": "v0.10.0", "draft": False, "prerelease": False}
        )
        with self.assertRaisesRegex(ReleaseError, "release ancestry"):
            versions.published(
                self.runner,
                self.github,
                remote_refs(self.runner, "origin"),
                self.runner.git("rev-parse", "v0.9.0"),
            )
