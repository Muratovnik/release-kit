from __future__ import annotations

import json
from unittest.mock import patch

from test_release import FakeGitHub, ReleaseFixture

from releasekit import config
from releasekit.release import coordinator
from releasekit.release.backend import Pending


class LocalGitHub(FakeGitHub):
    """Release storage independent of Actions, with observable remote mutations."""

    def __init__(self, fixture):
        super().__init__(fixture)
        self.remote_release = None
        self.uploaded = set()
        self.create_calls = self.publish_calls = 0
        self.upload_calls = []
        self.lose = ""

    def api(self, path="", **kwargs):
        if path.startswith("/actions"):
            raise AssertionError("local publication contacted GitHub Actions")
        if path == "/immutable-releases":
            return {"enabled": self.immutable}
        return super().api(path, **kwargs)

    def release(self, tag):
        return self.remote_release

    def create_draft(self, tag, sha, title, notes):
        self.create_calls += 1
        self.remote_release = {
            "id": 21,
            "tag_name": tag,
            "target_commitish": sha,
            "name": title,
            "draft": True,
            "prerelease": False,
            "immutable": False,
            "body": notes.read_text(encoding="utf-8"),
        }
        if self.lose == "create":
            self.lose = ""
            raise Pending("lost create response")

    def upload(self, tag, path):
        self.fixture.assertEqual(self.fixture.payloads[path.name], path.read_bytes())
        self.upload_calls.append(path.name)
        self.uploaded.add(path.name)
        if self.lose == "upload":
            self.lose = ""
            raise Pending("lost upload response")

    def publish(self, tag):
        self.publish_calls += 1
        self.fixture.assertEqual(set(self.fixture.payloads), self.uploaded)
        self.remote_release.update(draft=False, immutable=True, name=tag)
        if self.lose == "publish":
            self.lose = ""
            raise Pending("lost publish response")

    def assets(self, release_id):
        return [asset for asset in super().assets(release_id) if asset["name"] in self.uploaded]

    def signatures(self, tag, sha, workflow, paths, *, ci, repository_id, provenance=True):
        self.fixture.assertFalse(provenance)
        self.fixture.assertEqual("", workflow)
        self.fixture.assertEqual({}, ci)
        self.signatures_checked += 1

    def runs(self, *_):
        raise AssertionError("local publication waited for Actions")


class LocalFixture(ReleaseFixture):
    def setUp(self):
        super().setUp()
        policy = self.root / "relkit.toml"
        text = policy.read_text(encoding="utf-8")
        text = text.replace('workflow = ".github/workflows/release.yml"\n', "")
        text = text.replace('required_jobs = ["publish"]\n', "")
        text += 'publisher = "github"\nbuild = [["{python}", "build.py", "{assets}"]]\n'
        policy.write_text(text, encoding="utf-8")
        (self.root / ".github/workflows/release.yml").unlink()
        (self.root / "build.py").write_text(
            "import sys,hashlib\nfrom pathlib import Path\n"
            "p=Path(sys.argv[1]);p.mkdir()\n"
            "(p/'application.bin').write_bytes(b'application')\n"
            "(p/'SHA256SUMS').write_text(hashlib.sha256(b'application').hexdigest()"
            "+'  application.bin\\n',encoding='utf-8',newline='\\n')\n",
            encoding="utf-8",
        )
        self.commit()
        self.github = LocalGitHub(self)

    def prepare(self):
        with patch.object(coordinator, "_audit"):
            code, output = self.invoke("prepare", publish=False)
        self.assertEqual(0, code, output)
        return coordinator.plan(self.runner, "1.0.0", github=self.github)

    def publish(self, **kwargs):
        with patch.object(coordinator, "_audit"):
            return self.invoke(**kwargs)


class LocalReleaseTests(LocalFixture):
    def test_prepare_publish_verify_without_actions_or_payment_capabilities(self):
        value = self.prepare()
        self.assertEqual("github", value["settings"]["publisher"])
        self.assertFalse(value["settings"]["require_provenance"])
        self.assertEqual("", self.runner.git("tag", "--list"))
        self.assertEqual(0, self.github.create_calls)
        code, output = self.publish(plan_hash=coordinator.fingerprint(value))
        self.assertEqual(0, code, output)
        self.assertIn("CI=not-required", output)
        self.assertIn("acceptance=accepted", output)
        self.assertEqual(1, self.github.publish_calls)
        self.assertEqual(1, self.runner.pushes)
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(0, code, output)
        self.assertEqual(1, self.github.create_calls)
        self.assertEqual(1, self.github.publish_calls)

    def test_lost_remote_responses_resume_without_duplicate_writes(self):
        self.prepare()
        self.github.lose = "create"
        code, _ = self.publish()
        self.assertNotEqual(0, code)
        self.github.lose = "upload"
        code, _ = self.publish(action="resume")
        self.assertNotEqual(0, code)
        self.github.lose = "publish"
        code, _ = self.publish(action="resume")
        self.assertNotEqual(0, code)
        code, output = self.publish(action="resume")
        self.assertEqual(0, code, output)
        self.assertEqual(1, self.github.create_calls)
        self.assertEqual(1, self.github.publish_calls)
        self.assertEqual(2, len(self.github.upload_calls))
        self.assertEqual(1, self.runner.pushes)

    def test_modified_candidate_is_refused_before_tag(self):
        value = self.prepare()
        directory = self.root / ".git/relkit/candidates/v1.0.0" / value["candidate"]["attempt"]
        (directory / "assets/application.bin").write_bytes(b"modified")
        code, output = self.publish()
        self.assertNotEqual(0, code)
        self.assertIn("checksum", output)
        self.assertEqual(0, self.runner.pushes)
        self.assertEqual("", self.runner.git("tag", "--list"))

    def test_foreign_or_corrupted_draft_is_never_published(self):
        self.prepare()
        self.github.lose = "upload"
        self.assertNotEqual(0, self.publish()[0])
        self.github.remote_release["name"] = "unrelated draft"
        code, output = self.publish(action="resume")
        self.assertNotEqual(0, code)
        self.assertIn("draft", output)
        self.assertEqual(0, self.github.publish_calls)

    def test_changed_uploaded_file_is_not_overwritten_on_resume(self):
        self.prepare()
        self.github.lose = "upload"
        self.assertNotEqual(0, self.publish()[0])
        original = self.github.assets

        def changed(release_id):
            assets = original(release_id)
            assets[0]["digest"] = "sha256:" + "0" * 64
            return assets

        self.github.assets = changed
        code, output = self.publish(action="resume")
        self.assertNotEqual(0, code)
        self.assertIn("changed files", output)
        self.assertEqual(1, len(self.github.upload_calls))
        self.assertEqual(0, self.github.publish_calls)

    def test_failed_build_retries_same_number_without_tag(self):
        path = self.root / "build.py"
        original = path.read_bytes()
        path.write_text("raise RuntimeError('build failed')\n", encoding="utf-8")
        self.commit()
        with patch.object(coordinator, "_audit"):
            self.assertNotEqual(0, self.invoke("prepare", publish=False)[0])
        self.assertEqual("", self.runner.git("tag", "--list"))
        path.write_bytes(original)
        self.commit()
        self.prepare()
        attempts = list((self.root / ".git/relkit/candidates/v1.0.0").glob("*.json"))
        states = [json.loads(p.read_text(encoding="utf-8"))["status"] for p in attempts]
        self.assertIn("failed", states)
        self.assertIn("passed", states)

    def test_disabled_immutability_refuses_plan_before_any_tag(self):
        self.github.immutable = False
        code, output = self.invoke("plan", publish=False)
        self.assertNotEqual(0, code)
        self.assertIn("immutable", output)
        self.assertEqual(0, self.runner.pushes)

    def test_local_mode_rejects_ci_and_paid_provenance_requirements(self):
        path = self.root / "relkit.toml"
        text = path.read_text(encoding="utf-8")
        for extra in ["require_provenance = true", 'candidate_jobs = ["build"]']:
            with self.subTest(extra=extra):
                path.write_text(text + extra + "\n", encoding="utf-8")
                with self.assertRaises(config.ConfigError):
                    config.load(self.root)


class DirectoryReleaseTests(LocalFixture):
    def setUp(self):
        super().setUp()
        path = self.root / "relkit.toml"
        text = path.read_text(encoding="utf-8").replace('repository = "example/project"\n', "")
        # With neither publisher nor workflow, the portable directory is default.
        text = text.replace('publisher = "github"\n', "")
        path.write_text(text, encoding="utf-8")
        self.runner.git("remote", "remove", "origin")
        self.commit()
        previous = self.runner.call

        def offline(args, **kwargs):
            if args[0] == "gh" or args[:2] in (["git", "push"], ["git", "ls-remote"]):
                raise AssertionError("portable release attempted a hosting/network command")
            return previous(args, **kwargs)

        self.runner.call = offline

    def test_offline_prepare_export_verify_and_next_without_hosting_or_remote(self):
        from releasekit.result import Result

        with patch.object(coordinator, "_audit"):
            code, output = self.invoke("prepare", publish=False)
            self.assertEqual(0, code, output)
            code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertIn("tag=local", output)
        self.assertIn("acceptance=accepted", output)
        folder = self.root / ".cache/releases/v1.0.0"
        self.assertEqual(b"application", (folder / "assets/application.bin").read_bytes())
        manifest = json.loads((folder / "relkit-release.json").read_text(encoding="utf-8"))
        self.assertEqual(self.sha, manifest["sha"])
        self.assertNotIn(str(self.root), json.dumps(manifest))
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(0, code, output)
        result = Result()
        code = coordinator.run(
            self.root, "next", "", bump="patch", runner=self.runner, result=result
        )
        self.assertEqual(0, code)
        self.assertEqual("v1.0.1", result.data["next"]["tag"])
        (folder / "extra.txt").write_bytes(b"unexpected")
        code, output = self.invoke("verify", publish=False)
        self.assertNotEqual(0, code)
        self.assertIn("unexpected files", output)
        (folder / "extra.txt").unlink()
        (folder / "assets/application.bin").write_bytes(b"changed")
        code, output = self.invoke("verify", publish=False)
        self.assertNotEqual(0, code)
        self.assertIn("checksum", output)

    def test_interrupted_export_can_be_abandoned_without_hosting(self):
        from releasekit.release.directory import Directory

        with patch.object(coordinator, "_audit"):
            self.assertEqual(0, self.invoke("prepare", publish=False)[0])
            with patch.object(Directory, "export", side_effect=OSError("disk unavailable")):
                self.assertNotEqual(0, self.invoke()[0])
        code, output = self.invoke("abandon", publish=False, reason="cancelled export")
        self.assertEqual(0, code, output)
        self.assertIn("abandoned", output)
        self.assertEqual("v1.0.0", self.runner.git("tag", "--list"))

    def test_gitlab_commit_links_are_checked_against_local_git(self):
        sha = self.sha
        (self.root / "CHANGELOG.md").write_text(
            "## [1.0.0] (2026-09-12)\n\n### Features\n\n"
            f"- Prepared [{sha[:7]}](https://gitlab.example/team/project/-/commit/{sha}).\n",
            encoding="utf-8",
        )
        self.commit()
        code, output = self.invoke("plan", publish=False)
        self.assertEqual(0, code, output)
