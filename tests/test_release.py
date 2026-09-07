from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from releasekit import __version__, config, protection, storage
from releasekit.release import changelog, coordinator, settings
from releasekit.release.backend import CommandError, GitHub, ReleaseError, Runner, remote_identity

ROOT = Path(__file__).resolve().parents[1]


class LocalRunner(Runner):
    """Real Git and project commands, with an isolated bare repository as server."""

    lost_push_response = False
    pushes = 0

    def call(self, args, **kwargs):
        if args[:2] == ["gh", "--version"]:
            return "gh version 2.98.0\n"
        if args[0] == "gh":
            raise AssertionError("a fixture attempted a live GitHub request")
        if args[:3] == ["git", "remote", "get-url"]:
            return "https://github.com/example/project.git\n"
        result = super().call(args, **kwargs)
        if args[:2] == ["git", "push"]:
            self.pushes += 1
            if self.lost_push_response:
                self.lost_push_response = False
                raise CommandError(
                    "git", subprocess.CompletedProcess(args, 128, b"", b"connection lost")
                )
        return result


class FakeGitHub:
    def __init__(self, fixture):
        self.fixture = fixture
        self.draft = False
        self.immutable = True
        self.ci_result = "success"
        self.signature_valid = True
        self.download_valid = True
        self.body_suffix = ""
        self.missing_job = False
        self.extra_run = False
        self.wrong_tag = False
        self.attempt = 1
        self.asset_id = 41
        self.extra_asset = False
        self.on_wait = None
        self.signatures_checked = 0
        self.no_release = False
        self.orphan_release = False
        self.attestation = True
        self.attestation_digests_differ = False
        self.attestation_extra_asset = False
        self.attestation_tag_oid = None
        self.attestation_predicate = None

    def api(self, path="", **_kwargs):
        if not path:
            return {"id": 123, "full_name": "example/project"}
        if path.startswith("/actions/workflows/"):
            return {"id": 8, "path": ".github/workflows/release.yml", "state": "active"}
        raise AssertionError(path)

    def release(self, tag):
        if self.no_release:
            return None
        refs = self.fixture.runner.git("ls-remote", "--tags", "origin")
        if (
            not self.orphan_release
            and f"refs/tags/{tag}\t" not in refs
            and f"refs/tags/{tag}\n" not in refs + "\n"
        ):
            return None
        return {
            "id": 21,
            "tag_name": tag,
            "draft": self.draft,
            "prerelease": False,
            "immutable": self.immutable,
            "body": self.fixture.notes + self.body_suffix,
        }

    def release_attestation(self, tag):
        if not self.attestation:
            raise ReleaseError(f"attestation for {tag} could not be verified: no attestations")
        subjects = [
            {
                "uri": f"pkg:github/example/project@{tag}",
                "digest": {
                    "sha1": self.attestation_tag_oid
                    or self.fixture.runner.git("rev-parse", f"refs/tags/{tag}")
                },
            }
        ]
        for name, payload in self.fixture.payloads.items():
            digest = hashlib.sha256(
                payload + (b"drift" if self.attestation_digests_differ else b"")
            ).hexdigest()
            subjects.append({"name": name, "digest": {"sha256": digest}})
        if self.attestation_extra_asset:
            subjects.append({"name": "smuggled.bin", "digest": {"sha256": "0" * 64}})
        return {
            "predicateType": self.attestation_predicate
            or coordinator.RELEASE_ATTESTATION_PREDICATE,
            "subject": subjects,
            "predicate": {
                "repository": "example/project",
                "repositoryId": "123",
                "databaseId": "21",
                "tag": tag,
                "purl": f"pkg:github/example/project@{tag}",
            },
        }

    def assets(self, _release_id):
        result = [
            {
                "id": self.asset_id + i,
                "name": name,
                "size": len(payload),
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "state": "uploaded",
            }
            for i, (name, payload) in enumerate(self.fixture.payloads.items())
        ]
        return result + ([{**result[0], "name": "unexpected.bin"}] if self.extra_asset else [])

    def download(self, asset, destination):
        data = self.fixture.payloads[asset["name"]]
        destination.write_bytes(data if self.download_valid else b"corrupt")

    def signatures(self, tag, sha, workflow, paths, *, ci, repository_id):
        self.signatures_checked += 1
        self.fixture.assertEqual("v1.0.0", tag)
        self.fixture.assertEqual(self.fixture.sha, sha)
        self.fixture.assertEqual(".github/workflows/release.yml", workflow)
        self.fixture.assertEqual({"id": 15, "attempt": self.attempt}, ci)
        self.fixture.assertEqual(123, repository_id)
        self.fixture.assertTrue(all(path.is_relative_to(self.fixture.root) for path in paths))
        if not self.signature_valid:
            raise ReleaseError("invalid signature")

    def runs(self, workflow_id, sha):
        if self.on_wait:
            callback, self.on_wait = self.on_wait, None
            callback()
        value = {
            "id": 15,
            "workflow_id": workflow_id,
            "head_sha": sha,
            "head_branch": "v0.9.0" if self.wrong_tag else "v1.0.0",
            "event": "push",
            "path": ".github/workflows/release.yml",
            "repository": {"id": 123},
            "created_at": datetime.now(UTC).isoformat(),
            "run_attempt": self.attempt,
            "status": "completed",
            "conclusion": self.ci_result,
        }
        return [value, {**value, "id": 16}] if self.extra_run else [value]

    def jobs(self, _run_id, _attempt):
        return (
            []
            if self.missing_job
            else [
                {
                    "name": "publish",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": self.fixture.sha,
                }
            ]
        )


class ReleaseFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="release fixture ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project with spaces"
        self.root.mkdir()
        self.runner = LocalRunner(self.root)
        self.runner.git("init", "-q")
        self.runner.git("config", "user.name", "Example Maintainer")
        self.runner.git("config", "user.email", "maintainer@example.invalid")
        self.runner.git("config", "core.hooksPath", str(self.root / ".git/hooks"))
        self.runner.git("config", "commit.gpgSign", "false")
        self.runner.git("config", "tag.gpgSign", "false")
        server = Path(temporary.name) / "server.git"
        self.runner.git("init", "--bare", "-q", str(server))
        self.runner.git("remote", "add", "origin", str(server))
        self.server = server
        self.notes = "## [1.0.0] (2026-09-02)\n\n### Highlights\n\n- First useful release."
        (self.root / "CHANGELOG.md").write_text(self.notes + "\n", encoding="utf-8")
        (self.root / "VERSION").write_text("1.0.0\n")
        (self.root / ".gitignore").write_text(".cache/\n__pycache__/\n")
        workflow = self.root / ".github/workflows/release.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "on: {push: {tags: ['v*']}}\njobs:\n  publish:\n    runs-on: ubuntu-latest\n"
        )
        (self.root / "check.py").write_text(
            "from pathlib import Path\nassert Path('VERSION').read_text().strip() == '1.0.0'\n"
        )
        (self.root / "smoke.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "assert Path('VERSION').read_text().strip() == '1.0.0'\n"
            "assert (Path(sys.argv[1]) / 'application.bin').read_bytes() == b'application'\n"
        )
        (self.root / "relkit.toml").write_text(
            "[exposure]\ncheck_secrets = false\ncheck_links = false\n"
            '[changelog]\nprofile = "vue-like"\nfirst_version = "1.0.0"\n'
            '[release]\nrepository = "example/project"\nworkflow = ".github/workflows/release.yml"\n'
            'required_jobs = ["publish"]\nversion_file = "VERSION"\nversion_pattern = "^(.+)$"\n'
            'assets = ["application.bin", "SHA256SUMS"]\nchecksum_file = "SHA256SUMS"\n'
            'checks = [["{python}", "check.py"]]\nsmoke = [["{python}", "smoke.py", "{assets}"]]\n'
            'smoke_platforms = ["win32", "linux", "darwin"]\ntimeout = 1\n',
            encoding="utf-8",
        )
        self.commit()
        self.payloads = {
            "application.bin": b"application",
            "SHA256SUMS": (
                hashlib.sha256(b"application").hexdigest() + "  application.bin\n"
            ).encode(),
        }
        self.github = FakeGitHub(self)

    def commit(self):
        self.runner.git("add", ".")
        self.runner.git("commit", "-qm", "feat: prepare example release")
        self.sha = self.runner.git("rev-parse", "HEAD")

    def invoke(self, action="run", **kwargs):
        stream = io.StringIO()
        publish = kwargs.pop("publish", action != "abandon")
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            code = coordinator.run(
                self.root,
                action,
                "v1.0.0",
                publish=publish,
                runner=self.runner,
                github=self.github,
                **kwargs,
            )
        return code, stream.getvalue()

    def plan_json(self, output):
        """The plan JSON precedes the operator block that `plan` prints on stderr."""
        return json.loads(output.split("\nrelkit release:", 1)[0])

    def receipt(self):
        return json.loads(coordinator._state_path(self.root, "v1.0.0").read_text())


class ReleaseTests(ReleaseFixture):
    def test_existing_checkout_with_current_required_guard_can_plan_without_a_clone(self):
        path = self.root / "relkit.toml"
        path.write_text(path.read_text() + "require_guard = true\n")
        build = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools/build_zipapp.py"))[
            "build"
        ]
        build(self.root / ".github/relkit.pyz")
        self.commit()
        protection.install(self.root)
        code, output = self.invoke("plan")
        self.assertEqual(0, code, output)
        self.assertEqual(str(self.root), self.plan_json(output)["root"])
        self.assertEqual(0, self.runner.pushes)

    def test_missing_required_guard_fails_before_project_checks(self):
        path = self.root / "relkit.toml"
        path.write_text(path.read_text() + "require_guard = true\n")
        (self.root / "check.py").write_text("raise AssertionError('check should not start')\n")
        self.commit()
        code, output = self.invoke()
        self.assertEqual(2, code, output)
        self.assertIn("protect check failed", output)
        self.assertNotIn("check should not start", output)
        self.assertEqual(0, self.runner.pushes)
        self.assertEqual("", self.runner.git("tag", "--list"))

    def test_first_release_and_repeat_resume_use_real_git_and_project_commands(self):
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertEqual("passed", self.receipt()["verification"])
        self.assertEqual("passed", self.receipt()["cleanup"])
        self.assertFalse(Path(self.receipt()["temporary"]).exists())
        self.assertEqual("tag", self.runner.git("cat-file", "-t", "v1.0.0"))
        self.assertEqual(self.sha, self.runner.git("rev-parse", "HEAD"))
        self.assertEqual("", self.runner.git("status", "--porcelain"))
        code, output = self.invoke("resume")
        self.assertEqual(0, code, output)
        self.assertEqual(1, self.runner.pushes)
        self.assertEqual(2, self.github.signatures_checked)
        self.assertEqual(2, self.invoke()[0])

    def test_plan_is_read_only_even_when_publish_is_given(self):
        before = set(self.root.rglob("*"))
        code, output = self.invoke("plan")
        self.assertEqual(0, code, output)
        self.assertEqual(before, set(self.root.rglob("*")))
        self.assertIn("plan_sha256", self.plan_json(output))
        self.assertEqual(0, self.runner.pushes)

    def test_permission_required_before_creating_service_files(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = coordinator.run(
                self.root, "run", "1.0.0", runner=self.runner, github=self.github
            )
        self.assertEqual(2, code)
        self.assertFalse((self.root / ".git/relkit").exists())

    def test_wrong_version_and_dirty_tree_fail_before_tag(self):
        (self.root / "VERSION").write_text("2.0.0\n")
        code, output = self.invoke()
        self.assertEqual(2, code, output)
        self.commit()
        self.assertIn("version_file", self.invoke()[1])
        self.assertEqual("", self.runner.git("tag", "--list"))

    def test_invalid_changelog_fails_before_tag(self):
        (self.root / "CHANGELOG.md").write_text("## [1.0.0] (2026-09-02)\n")
        self.commit()
        self.assertIn("empty", self.invoke()[1])
        self.assertEqual(0, self.runner.pushes)

    def test_first_release_is_not_inferred(self):
        path = self.root / "relkit.toml"
        path.write_text(
            path.read_text().replace('first_version = "1.0.0"', 'first_version = "0.1.0"')
        )
        self.commit()
        self.assertIn("declare changelog.first_version", self.invoke()[1])

    def test_local_remote_tag_drift_is_not_repaired(self):
        self.runner.git("tag", "v0.1.0")
        output = self.invoke()[1]
        self.assertIn("tags disagree", output)
        self.assertIn("v0.1.0", output, "the owner has to be told which tag to reconcile")
        self.assertEqual(0, self.runner.pushes)

    def test_a_tag_outside_the_stable_line_is_not_a_reason_to_refuse(self):
        # Field friction: any personal or pre-release local tag made planning refuse,
        # and the only remedy was deleting somebody's tag.
        self.runner.git("tag", "scratch/before-refactor")
        self.runner.git("tag", "--annotate", "v1.0.0-rc1", "--message", "candidate")

        code, output = self.invoke()

        self.assertEqual(0, code, output)

    def test_a_stable_tag_outside_the_release_ancestry_still_stops_the_plan(self):
        self.runner.git("checkout", "-q", "--detach", "HEAD")
        (self.root / "divergent.txt").write_text("side branch")
        self.commit()
        self.runner.git("tag", "--annotate", "v0.9.0", "--message", "Sideways release")
        self.runner.git("push", "origin", "refs/tags/v0.9.0:refs/tags/v0.9.0")
        self.runner.git("checkout", "-q", "-")

        self.assertIn("outside this release ancestry", self.invoke()[1])
        self.assertEqual(1, self.runner.pushes, "only this test's own tag push happened")

    def test_preexisting_tag_or_draft_not_adopted(self):
        self.runner.git("tag", "v1.0.0")
        self.assertIn("already exists", self.invoke()[1])

    def test_lost_push_response_is_reconciled_without_duplicate_push(self):
        self.runner.lost_push_response = True
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertEqual(1, self.runner.pushes)

    def test_ci_failure_records_publication_separately(self):
        self.github.ci_result = "failure"
        code, output = self.invoke()
        self.assertEqual(1, code, output)
        self.assertIn("CI failed", output)
        self.assertEqual("published", self.receipt()["publication"])
        self.assertEqual("not-run", self.receipt()["verification"])

    def test_draft_is_not_republished_and_can_later_resume(self):
        self.github.draft = True
        code, output = self.invoke()
        self.assertEqual(3, code, output)
        self.assertEqual("draft", self.receipt()["publication"])
        self.github.draft = False
        code, output = self.invoke("resume")
        self.assertEqual(0, code, output)
        self.assertEqual(1, self.runner.pushes)

    def test_checksum_and_signature_failures_do_not_run_smoke(self):
        for fault in ("download_valid", "signature_valid"):
            with self.subTest(fault=fault):
                setattr(self.github, fault, False)
                code, output = self.invoke(
                    "resume" if coordinator._state_path(self.root, "v1.0.0").exists() else "run"
                )
                self.assertEqual(1, code, output)
                self.assertNotIn("downloaded-application smoke", output)
                setattr(self.github, fault, True)

    def test_no_other_green_run_or_missing_job_is_accepted(self):
        self.github.extra_run = True
        self.assertIn("multiple matching", self.invoke()[1])
        self.github.extra_run = False
        self.github.missing_job = True
        self.assertIn("required CI job", self.invoke("resume")[1])

    def test_wrong_tag_times_out_even_with_same_sha(self):
        self.github.wrong_tag = True
        self.assertEqual(3, self.invoke()[0])

    def test_changed_run_attempt_is_not_silently_adopted(self):
        self.github.ci_result = "failure"
        self.invoke()
        self.github.ci_result = "success"
        self.github.attempt = 2
        self.assertIn("run/attempt changed", self.invoke("resume")[1])
        code, output = self.invoke("resume", accept_ci_attempt=2)
        self.assertEqual(0, code, output)
        self.assertEqual([{"id": 15, "attempt": 1}], self.receipt()["previous_ci"])

    def test_mutable_release_wrong_notes_and_extra_assets_fail(self):
        self.github.immutable = False
        self.assertIn("immutable", self.invoke()[1])
        self.github.immutable = True
        self.github.body_suffix = "\nChanged later"
        self.assertIn("notes differ", self.invoke("resume")[1])
        self.github.body_suffix = ""
        self.github.extra_asset = True
        self.assertIn("exact file set", self.invoke("resume")[1])

    def test_the_signed_release_attestation_decides_the_published_asset_set(self):
        # The REST asset list is unsigned. GitHub signs a statement about an immutable
        # release and its exact assets, so a rewritten API answer must not be enough.
        self.github.attestation = False
        self.assertIn("could not be verified", self.invoke()[1])
        self.github.attestation = True
        self.github.attestation_digests_differ = True
        self.assertIn("does not match the reported assets", self.invoke("resume")[1])
        self.github.attestation_digests_differ = False
        self.github.attestation_extra_asset = True
        self.assertIn("does not match the reported assets", self.invoke("resume")[1])

    def test_an_attestation_for_another_tag_or_predicate_is_refused(self):
        self.github.attestation_tag_oid = "0" * 40
        self.assertIn("different tag object", self.invoke()[1])
        self.github.attestation_tag_oid = None
        self.github.attestation_predicate = "https://slsa.dev/provenance/v1"
        self.assertIn("not a GitHub release statement", self.invoke("resume")[1])

    def test_changed_artifact_identity_is_not_adopted_on_resume(self):
        self.github.signature_valid = False
        self.invoke()
        self.github.signature_valid = True
        self.github.asset_id += 1
        self.assertIn("identity changed", self.invoke("resume")[1])

    def test_local_edits_during_ci_do_not_change_snapshot_smoke_or_commit(self):
        def edit():
            (self.root / "VERSION").write_text("2.0.0\n")
            (self.root / "smoke.py").write_text("raise RuntimeError('wrong source')\n")
            (self.root / "unrelated.txt").write_text("preserve me")

        self.github.on_wait = edit
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertTrue(self.receipt()["local_changes"])
        self.assertEqual(self.sha, self.runner.git("rev-parse", "HEAD"))
        self.assertEqual("preserve me", (self.root / "unrelated.txt").read_text())

    def test_stale_plan_and_concurrent_lock_block_writes(self):
        self.assertIn("stale", self.invoke(plan_hash="0" * 64)[1])
        lock = storage.service_root(self.root) / "release.lock"
        lock.write_text("other run")
        self.assertIn("another release", self.invoke()[1])
        self.assertEqual("other run", lock.read_text())

    def test_branch_tag_name_collision_rejected(self):
        self.runner.git("branch", "v1.0.0")
        self.assertIn("ambiguous", self.invoke()[1])

    def test_foreign_temp_files_are_preserved(self):
        def foreign():
            (Path(self.receipt()["temporary"]) / "foreign.txt").write_text("not owned")

        self.github.on_wait = foreign
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertEqual(
            "not owned", (Path(self.receipt()["temporary"]) / "foreign.txt").read_text()
        )

    def test_second_project_uses_different_commands_and_asset_names(self):
        path = self.root / "relkit.toml"
        path.write_text(
            path.read_text()
            .replace(
                'checks = [["{python}", "check.py"]]',
                'checks = [["{python}", "build_archive.py", "{temp}"]]',
            )
            .replace('version_file = "VERSION"', 'version_file = "package.data"')
            .replace('version_pattern = "^(.+)$"', 'version_pattern = "^version=(.+)$"')
            .replace('"application.bin"', '"bundle.zip"')
        )
        (self.root / "package.data").write_text("version=1.0.0\n")
        (self.root / "build_archive.py").write_text(
            "import sys, zipfile\nfrom pathlib import Path\n"
            "p = Path(sys.argv[1]) / 'bundle.zip'\n"
            "with zipfile.ZipFile(p, 'w') as z: z.writestr('example.txt', 'contents')\n"
            "assert p.is_file()\np.unlink()\n"
        )
        (self.root / "smoke.py").write_text(
            "import sys, zipfile\nfrom pathlib import Path\n"
            "with zipfile.ZipFile(Path(sys.argv[1]) / 'bundle.zip') as z:\n"
            " assert z.read('example.txt') == b'contents'\n"
        )
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("example.txt", "contents")
        self.payloads = {
            "bundle.zip": data.getvalue(),
            "SHA256SUMS": (hashlib.sha256(data.getvalue()).hexdigest() + "  bundle.zip\n").encode(),
        }
        self.commit()
        code, output = self.invoke()
        self.assertEqual(0, code, output)

    def previous_release(self):
        self.runner.git("tag", "--annotate", "v0.9.0", "--message", "Previous release")
        self.runner.git("push", "origin", "refs/tags/v0.9.0:refs/tags/v0.9.0")
        path = self.root / "relkit.toml"
        path.write_text(
            path.read_text().replace('first_version = "1.0.0"', 'first_version = "0.9.0"')
        )
        (self.root / "feature.txt").write_text("new change")
        self.commit()
        self.notes = (
            "## [1.0.0](https://github.com/example/project/compare/v0.9.0...v1.0.0) (2026-09-02)\n\n"
            "### Features\n\n- New behavior "
            f"([{self.sha[:8]}](https://github.com/example/project/commit/{self.sha}))."
        )
        (self.root / "CHANGELOG.md").write_text(self.notes + "\n", encoding="utf-8")
        self.commit()

    def test_previous_release_uses_loose_or_packed_tags_identically(self):
        self.previous_release()
        value = coordinator.plan(self.runner, "v1.0.0", github=self.github)
        self.runner.git("pack-refs", "--all")
        packed = coordinator.plan(self.runner, "v1.0.0", github=self.github)
        self.assertEqual(value, packed)
        self.assertEqual("v0.9.0", packed["previous"]["tag"])
        code, output = self.invoke()
        self.assertEqual(0, code, output)

    def test_wrong_compare_boundary_and_old_commit_are_rejected(self):
        self.previous_release()
        path = self.root / "CHANGELOG.md"
        path.write_text(self.notes.replace("v0.9.0...", "v0.8.0..."))
        self.commit()
        self.assertIn("actual Git boundary", self.invoke()[1])
        previous_sha = self.runner.git("rev-parse", "v0.9.0^{commit}")
        lines = self.notes.splitlines()
        lines[-1] = (
            f"- Old change ([{previous_sha[:8]}](https://github.com/example/project/commit/{previous_sha}))."
        )
        path.write_text("\n".join(lines) + "\n")
        self.commit()
        self.assertIn("already included", self.invoke()[1])

    def test_pre_push_hook_is_not_bypassed_and_run_can_resume(self):
        hook = self.root / ".git/hooks/pre-push"
        hook.write_bytes(b"#!/bin/sh\nexit 42\n")
        hook.chmod(0o755)
        code, output = self.invoke()
        self.assertEqual(1, code, output)
        self.assertFalse(self.receipt()["pushed"])
        self.assertTrue(self.receipt()["tag_oid"])
        hook.write_bytes(b"#!/bin/sh\nexit 0\n")
        code, output = self.invoke("resume")
        self.assertEqual(0, code, output)

    def test_snapshot_ignores_export_attributes(self):
        (self.root / ".gitattributes").write_text("smoke.py export-ignore\nVERSION export-subst\n")
        self.commit()
        code, output = self.invoke()
        self.assertEqual(0, code, output)

    def test_snapshot_keeps_literal_bracket_routes_from_git(self):
        route = self.root / "pages/[id]/[...slug].vue"
        route.parent.mkdir(parents=True)
        route.write_bytes(b"<template>literal route</template>\n")
        smoke = self.root / "smoke.py"
        smoke.write_text(
            smoke.read_text()
            + "assert Path('pages/[id]/[...slug].vue').read_bytes() == "
            + repr(route.read_bytes())
            + "\n"
        )
        self.commit()
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertEqual("passed", self.receipt()["cleanup"])

    def test_tree_paths_keep_portability_and_traversal_guards(self):
        for name in (
            "../escape",
            ".git/config",
            "a/./b",
            "a//b",
            "a?.vue",
            "a*.vue",
            "CON.txt",
            "a:",
        ):
            with self.subTest(name=name), self.assertRaises((ValueError, config.ConfigError)):
                settings.relative(name, from_tree=True)
        with self.assertRaises(config.ConfigError):
            settings.relative("pages/[id].vue")

    def test_checksum_manifest_is_not_just_trusted_from_release_digest(self):
        self.payloads["SHA256SUMS"] = ("a" * 64 + "  application.bin\n").encode()
        self.assertIn("checksum manifest does not match", self.invoke()[1])

    def test_exact_optional_branch_push_does_not_push_other_refs(self):
        branch = self.runner.git("branch", "--show-current")
        path = self.root / "relkit.toml"
        path.write_text(path.read_text() + f'branch = "{branch}"\n')
        self.commit()
        self.runner.git("branch", "unrelated")
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        refs = self.runner.git("ls-remote", "--heads", "origin")
        self.assertIn(f"refs/heads/{branch}", refs)
        self.assertNotIn("unrelated", refs)

    def test_bad_saved_plan_does_not_turn_into_a_new_run(self):
        self.github.draft = True
        self.invoke()
        path = coordinator._state_path(self.root, "v1.0.0")
        path.write_text('{"schema": 999}')
        code, _ = self.invoke("resume")
        self.assertEqual(2, code)
        self.assertEqual(1, self.runner.pushes)

    def test_completed_release_does_not_keep_passed_status_after_bad_resume(self):
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.github.body_suffix = "changed"
        self.assertEqual(1, self.invoke("resume")[0])
        self.assertEqual("failed", self.receipt()["verification"])

    def test_interruption_after_push_resumes_without_another_push(self):
        with patch.object(coordinator, "_ci", side_effect=KeyboardInterrupt):
            code, output = self.invoke()
        self.assertEqual(3, code, output)
        self.assertEqual(1, self.runner.pushes)
        code, output = self.invoke("resume")
        self.assertEqual(0, code, output)
        self.assertEqual(1, self.runner.pushes)

    def test_tampered_receipt_cannot_add_a_force_push(self):
        hook = self.root / ".git/hooks/pre-push"
        hook.write_bytes(b"#!/bin/sh\nexit 42\n")
        hook.chmod(0o755)
        self.invoke()
        path = coordinator._state_path(self.root, "v1.0.0")
        state = self.receipt()
        state["plan"]["pushes"].insert(0, "--force")
        state["plan_sha256"] = coordinator.fingerprint(state["plan"])
        storage.atomic_json(path, state)
        code, output = self.invoke("resume")
        self.assertEqual(1, code, output)
        self.assertIn("canonical exact-ref", output)
        self.assertEqual(0, self.runner.pushes)

    def test_plan_prints_the_exact_asset_set_and_caveats_outside_the_json(self):
        code, output = self.invoke("plan")
        self.assertEqual(0, code, output)
        described = self.plan_json(output)
        self.assertEqual(["application.bin", "SHA256SUMS"], described["assets"])
        self.assertEqual({"publish": "publish"}, described["workflow_jobs"]["declared"])
        self.assertEqual([], described["workflow_jobs"]["optional"])
        self.assertEqual(sys.platform, described["host"])
        self.assertEqual(coordinator.caveats(described), described["caveats"])
        notes = output.split("\nrelkit release:", 1)[1]
        self.assertIn(
            "exact asset set for v1.0.0 (2 file(s)):\n  application.bin\n  SHA256SUMS\n", notes
        )
        self.assertIn("signed release attestation only after the immutable release exists", notes)
        self.assertIn("SHA256SUMS must list every other planned asset", notes)
        self.assertIn(f"local checks run on {sys.platform} only", notes)
        self.assertNotIn("could not be read statically", notes)

    def test_required_job_missing_from_the_workflow_fails_before_any_tag(self):
        (self.root / ".github/workflows/release.yml").write_text(
            "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        )
        self.commit()
        code, output = self.invoke()
        self.assertEqual(2, code, output)
        self.assertIn(
            "required_jobs are not declared by .github/workflows/release.yml: publish", output
        )
        self.assertEqual("", self.runner.git("tag", "--list"))
        self.assertEqual(0, self.runner.pushes)
        self.assertFalse((self.root / ".git/relkit/releases").exists())

    def test_matrix_labels_literal_names_and_optional_jobs_are_read_statically(self):
        (self.root / ".github/workflows/release.yml").write_text(
            "on: push\n"
            "jobs:\n"
            "  native-smoke:\n"
            "    strategy: {matrix: {os: [ubuntu-24.04]}}\n"
            "    runs-on: ${{ matrix.os }}\n"
            "  lint:\n"
            "    runs-on: ubuntu-latest\n"
            "  publish:\n"
            "    name: 'Publish (release)'  # display name\n"
            "    needs: [native-smoke]\n"
            "    steps:\n"
            "      - name: not a job\n"
            "        run: echo\n"
        )
        path = self.root / "relkit.toml"
        path.write_text(
            path.read_text().replace(
                'required_jobs = ["publish"]',
                'required_jobs = ["native-smoke (ubuntu-24.04)", "Publish (release)"]',
            )
        )
        self.commit()
        code, output = self.invoke("plan")
        self.assertEqual(0, code, output)
        jobs = self.plan_json(output)["workflow_jobs"]
        self.assertEqual(
            {"native-smoke": "native-smoke", "lint": "lint", "publish": "Publish (release)"},
            jobs["declared"],
        )
        self.assertEqual([], jobs["unverified"])
        self.assertEqual(["lint"], jobs["optional"])
        self.assertIn("do not gate publication: lint", output)

    def test_flow_style_and_expression_job_names_are_reported_not_guessed(self):
        workflow = self.root / ".github/workflows/release.yml"
        workflow.write_text("on: push\njobs: {publish: {runs-on: ubuntu-latest}}\n")
        self.commit()
        code, output = self.invoke("plan")
        self.assertEqual(0, code, output)
        self.assertIsNone(self.plan_json(output)["workflow_jobs"]["declared"])
        self.assertIn("could not be read statically", output)
        workflow.write_text(
            "on: push\njobs:\n  build:\n    name: Build ${{ matrix.os }}\n"
            "    strategy: {matrix: {os: [ubuntu-latest]}}\n"
        )
        self.commit()
        code, output = self.invoke("plan")
        self.assertEqual(0, code, output)
        jobs = self.plan_json(output)["workflow_jobs"]
        self.assertEqual({"build": None}, jobs["declared"])
        self.assertEqual(["publish"], jobs["unverified"])
        self.assertEqual([], jobs["optional"])
        self.assertIn("rely on expression names: publish", output)

    def test_workflow_job_parser_reads_only_what_it_can_prove(self):
        self.assertIsNone(coordinator.workflow_jobs("name: x\n"))
        self.assertIsNone(coordinator.workflow_jobs("jobs:\n"))
        self.assertIsNone(coordinator.workflow_jobs("jobs:\n\tbuild:\n"))
        self.assertIsNone(coordinator.workflow_jobs("jobs:\n  build: &base\n    x: 1\n"))
        self.assertIsNone(coordinator.workflow_jobs("jobs:\n  build:\n   x: 1\n  <<: *base\n"))
        self.assertIsNone(coordinator.workflow_jobs("jobs:\n  build:\n    x: 1\n  build:\n"))
        self.assertEqual(
            {"build": "Build  it", "test": None, "docs": "docs", "it": "it's"},
            coordinator.workflow_jobs(
                "jobs: # comment\n"
                "  # leading comment\n"
                "  build:  # trailing\n"
                '    name: "Build  it" # comment\n'
                "  test:\n"
                "    name: |\n"
                "      multi\n"
                "  docs:\n"
                "    steps:\n"
                "      - name: step\n"
                "  it:\n"
                "    name: 'it''s'\n"
                "env: {}\n"
            ),
        )

    def test_busy_lock_reports_its_recorded_owner_and_liveness(self):
        finished = subprocess.Popen([sys.executable, "-c", "pass"])
        finished.wait()
        lock = storage.service_root(self.root) / "release.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"pid": finished.pid, "tag": "v0.9.0", "root": "elsewhere"}))
        code, output = self.invoke()
        self.assertEqual(2, code, output)
        self.assertIn(
            f"pid {finished.pid} (no process with this PID is running), tag v0.9.0, root elsewhere",
            output,
        )
        lock.write_text(json.dumps({"pid": os.getpid(), "tag": "v1.0.0", "root": str(self.root)}))
        self.assertIn(f"pid {os.getpid()} (a process with this PID is running)", self.invoke()[1])
        self.assertTrue(lock.exists())
        self.assertEqual(0, self.runner.pushes)

    def test_abandon_records_the_outcome_and_frees_the_version_once_the_remote_tag_is_gone(self):
        # Field case: CI rejected the pushed tag, no release was created, the owner
        # later deleted the remote tag; the receipt must not trap the version forever.
        self.github.ci_result = "failure"
        self.github.no_release = True
        code, output = self.invoke()
        self.assertEqual(1, code, output)
        self.assertTrue(self.receipt()["pushed"])
        self.assertEqual("not-pushed", self.receipt()["publication"])
        self.assertIn("still exists on origin", self.invoke("abandon", reason="CI cancelled")[1])
        subprocess.run(
            ["git", "tag", "-d", "v1.0.0"], cwd=self.server, check=True, capture_output=True
        )
        self.assertIn("previously pushed tag disappeared", self.invoke("resume")[1])
        self.assertIn("non-empty --reason", self.invoke("abandon")[1])
        self.assertIn(
            "publication flags are not accepted",
            self.invoke("abandon", reason="x", publish=True)[1],
        )
        self.assertIn("use release resume, or release abandon", self.invoke()[1])
        code, output = self.invoke("abandon", reason="CI cancelled; remote tag removed by owner")
        self.assertEqual(0, code, output)
        self.assertIn("local tag v1.0.0 still exists", output)
        receipt = self.receipt()
        self.assertEqual("abandoned", receipt["outcome"]["status"])
        self.assertEqual("CI cancelled; remote tag removed by owner", receipt["outcome"]["reason"])
        self.assertEqual("recorded", receipt["stages"]["abandoned"])
        self.assertIn("disappeared", receipt["error"])
        code, output = self.invoke("resume")
        self.assertEqual(2, code, output)
        self.assertIn("recorded as abandoned", output)
        self.assertIn("already recorded as abandoned", self.invoke("abandon", reason="again")[1])
        self.runner.git("tag", "-d", "v1.0.0")
        self.github.ci_result = "success"
        self.github.no_release = False
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertIn("archived the abandoned attempt receipt", output)
        self.assertEqual("passed", self.receipt()["verification"])
        self.assertNotIn("outcome", self.receipt())
        releases = coordinator._state_path(self.root, "v1.0.0").parent.parent
        archived = [p for p in releases.iterdir() if p.name.startswith("v1.0.0.abandoned-")]
        self.assertEqual(1, len(archived))
        old = json.loads((archived[0] / "state.json").read_text())
        self.assertEqual("abandoned", old["outcome"]["status"])
        self.assertEqual(2, self.runner.pushes)

    def test_abandon_refuses_a_published_release_and_misplaced_reasons(self):
        self.assertIn("accepted only by release abandon", self.invoke("plan", reason="x")[1])
        self.assertEqual(0, self.invoke()[0])
        code, output = self.invoke("abandon", reason="mistake")
        self.assertEqual(2, code, output)
        self.assertIn("cannot be abandoned", output)
        self.assertNotIn("outcome", self.receipt())

    def test_abandon_refuses_while_a_release_or_draft_exists_without_the_tag(self):
        self.github.draft = True
        self.assertEqual(3, self.invoke()[0])
        subprocess.run(
            ["git", "tag", "-d", "v1.0.0"], cwd=self.server, check=True, capture_output=True
        )
        self.github.orphan_release = True
        code, output = self.invoke("abandon", reason="x")
        self.assertEqual(2, code, output)
        self.assertIn("a release or draft exists for v1.0.0", output)
        self.assertEqual("draft", self.receipt()["publication"])
        self.assertNotIn("outcome", self.receipt())


class BackendTests(unittest.TestCase):
    def test_verified_signature_from_other_ci_attempt_is_rejected(self):
        runner = Runner(Path("."))
        proof = json.dumps(
            [
                {
                    "verificationResult": {
                        "signature": {
                            "certificate": {
                                "runInvocationURI": "https://github.com/example/project/actions/runs/15/attempts/2",
                                "sourceRepositoryIdentifier": "123",
                                "buildTrigger": "push",
                            }
                        }
                    }
                }
            ]
        )
        with patch.object(runner, "call", return_value=proof), self.assertRaises(ReleaseError):
            GitHub(runner, "example/project").signatures(
                "v1.0.0",
                "a" * 40,
                ".github/workflows/release.yml",
                [Path("application.bin")],
                ci={"id": 15, "attempt": 1},
                repository_id=123,
            )

    def test_native_signature_policy_pins_full_ref_sha_and_workflow(self):
        runner = Runner(Path("."))
        proof = json.dumps(
            [
                {
                    "verificationResult": {
                        "signature": {
                            "certificate": {
                                "runInvocationURI": "https://github.com/example/project/actions/runs/15/attempts/1",
                                "sourceRepositoryIdentifier": "123",
                                "buildTrigger": "push",
                            }
                        }
                    }
                }
            ]
        )
        with patch.object(runner, "call", return_value=proof) as call:
            GitHub(runner, "example/project").signatures(
                "v1.0.0",
                "a" * 40,
                ".github/workflows/release.yml",
                [Path("application.bin")],
                ci={"id": 15, "attempt": 1},
                repository_id=123,
            )
        args = call.call_args.args[0]
        self.assertIn("refs/tags/v1.0.0", args)
        self.assertEqual("a" * 40, args[args.index("--source-digest") + 1])
        self.assertEqual("a" * 40, args[args.index("--signer-digest") + 1])
        self.assertIn("example/project/.github/workflows/release.yml", args)

    def test_remote_url_cannot_redirect_publication(self):
        runner = Runner(Path("."))
        for url in (
            "https://example.invalid/example/project",
            "https://github.com/other/project.git",
            "https://token@github.com/example/project.git",
        ):
            with patch.object(runner, "git", return_value=url), self.assertRaises(ReleaseError):
                remote_identity(runner, "origin", "example/project")

    def test_unknown_release_settings_fail_existing_config_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "relkit.toml").write_text("[release]\nunknown = true\n")
            with self.assertRaises(config.ConfigError):
                config.load(path)

    def test_path_and_filename_aliases_are_rejected(self):
        for name in ("../escape", "C:/escape", ".git/config", "NUL", "name.", "a\\b"):
            with self.subTest(name=name), self.assertRaises((ValueError, config.ConfigError)):
                settings.relative(name)
        for name in ("payload.", "../outside", "NUL.zip", "a/b"):
            with self.subTest(asset=name), self.assertRaises(ValueError):
                settings.filename(name)


class SelfHostingTests(unittest.TestCase):
    """This repository publishes itself, so its own contract has to stay consistent.

    Every one of these disagreements would otherwise surface for the first time
    during a release, after a tag exists and the version number is already spent.
    """

    def setUp(self) -> None:
        policy = config.load(ROOT)
        self.assertIsNotNone(policy.release, "this repository declares its own [release]")
        self.policy = policy
        self.release = policy.release
        self.workflow = (ROOT / self.release.workflow).read_text(encoding="utf-8")

    def test_required_jobs_are_declared_by_its_own_publishing_workflow(self) -> None:
        coverage = coordinator.job_coverage(
            self.release.required_jobs, coordinator.workflow_jobs(self.workflow)
        )
        self.assertEqual([], coverage["missing"])
        self.assertEqual([], coverage["unverified"], "a static answer beats a tag-time surprise")

    def test_the_workflow_publishes_exactly_the_declared_asset_set(self) -> None:
        # The coordinator compares the published set against the signed release
        # attestation only after the immutable release exists.
        self.assertEqual(
            sorted(self.release.assets),
            sorted(set(re.findall(r"dist/([A-Za-z0-9._-]+)", self.workflow))),
        )

    def test_the_declared_asset_set_is_exactly_what_the_builder_produces(self) -> None:
        # The one disagreement the contract cannot answer statically. The published set
        # is compared to the signed attestation only after the immutable release
        # exists, so a forgotten asset is found by spending the version number.
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "dist"
            completed = subprocess.run(
                [sys.executable, "tools/build_release.py", str(output)],
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            produced = sorted(path.name for path in output.iterdir())

        self.assertEqual(sorted(self.release.assets), produced)

    def test_the_version_file_and_changelog_describe_exactly_this_version(self) -> None:
        observed = re.findall(
            self.release.version_pattern,
            (ROOT / self.release.version_file).read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        self.assertEqual([__version__], observed)
        self.assertIsNotNone(
            changelog.entry_for(
                (ROOT / self.release.changelog).read_text(encoding="utf-8"),
                __version__,
                profile=self.policy.changelog.profile,
            ),
            "the version being built needs its own validated changelog entry",
        )

    def test_declared_project_commands_exist_in_this_checkout(self) -> None:
        for command in self.release.checks + self.release.smoke:
            script = next((arg for arg in command if arg.endswith(".py")), None)
            self.assertIsNotNone(script, command)
            self.assertTrue((ROOT / script).is_file(), script)
