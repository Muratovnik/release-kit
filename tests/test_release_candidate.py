from __future__ import annotations

import json
import os
from unittest.mock import patch

from test_release import ReleaseFixture

from releasekit import canonical
from releasekit.release import candidate, coordinator
from releasekit.release.backend import ReleaseError


class CandidateTests(ReleaseFixture):
    def setUp(self):
        super().setUp()
        path = self.root / "relkit.toml"
        path.write_text(path.read_text() + '\ncandidate_jobs = ["publish"]\n')
        self.commit()
        self.output = self.root / ".cache/bundle"
        self.output.mkdir(parents=True)
        for name, data in self.payloads.items():
            (self.output / name).write_bytes(data)
        env = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": self.sha,
            "GITHUB_REPOSITORY": "example/project",
            "GITHUB_RUN_ID": "35",
            "GITHUB_RUN_ATTEMPT": "1",
        }
        with patch.dict(os.environ, env):
            self.record = candidate.bundle(self.runner, "v1.0.0", self.output)
        self.run_data = {
            "id": 35,
            "event": "workflow_dispatch",
            "head_sha": self.sha,
            "path": ".github/workflows/release.yml",
            "repository": {"id": 123},
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
        }
        old_api = self.github.api
        self.github.api = lambda path="", **kwargs: (
            self.run_data if path == "/actions/runs/35" else old_api(path, **kwargs)
        )
        old_call = self.runner.call

        def call(args, **kwargs):
            if args[:3] == ["gh", "run", "download"]:
                from pathlib import Path

                target = Path(args[-1])
                for name, data in self.payloads.items():
                    (target / name).write_bytes(data)
                (target / candidate.MANIFEST).write_text(json.dumps(self.record))
                return ""
            return old_call(args, **kwargs)

        self.runner.call = call

    def prepare(self):
        with patch.object(coordinator, "_audit"):
            return self.invoke("prepare", publish=False, ci_run=35)

    def test_prepare_promote_and_draft_keep_exact_bytes_without_preparation_tag(self):
        code, output = self.prepare()
        self.assertEqual(0, code, output)
        self.assertEqual("", self.runner.git("tag", "--list"))
        self.assertEqual(0, self.runner.pushes)
        value = coordinator.plan(self.runner, "1.0.0", github=self.github)
        self.assertEqual(canonical.fingerprint(self.record), value["candidate"]["sha256"])
        self.runner.git("tag", "--annotate", "v1.0.0", "--message", coordinator._tag_message(value))
        target = self.root / ".cache/promoted"
        self.assertEqual(self.record, candidate.promote(self.runner, self.github, "1.0.0", target))
        self.assertFalse((target / candidate.MANIFEST).exists())
        self.github.orphan_release = self.github.draft = True
        self.assertEqual(
            "passed", candidate.draft(self.runner, self.github, "1.0.0", target)["verification"]
        )
        self.github.extra_asset = True
        with self.assertRaisesRegex(ReleaseError, "draft assets differ"):
            candidate.draft(self.runner, self.github, "1.0.0", target)

    def test_failure_can_retry_same_number_and_keeps_attempts(self):
        self.run_data["conclusion"] = "failure"
        self.assertNotEqual(0, self.prepare()[0])
        self.assertFalse(candidate.receipt_path(self.root, "v1.0.0").exists())
        self.run_data["conclusion"] = "success"
        code, output = self.prepare()
        self.assertEqual(0, code, output)
        receipts = list(candidate.receipt_path(self.root, "v1.0.0").parent.glob("*.json"))
        self.assertEqual(3, len(receipts))
        self.assertEqual("", self.runner.git("tag", "--list"))

    def test_run_without_preparation_refuses_before_any_tag(self):
        code, output = self.invoke()
        self.assertNotEqual(0, code)
        self.assertIn("no matching prepared candidate", output)
        self.assertEqual("", self.runner.git("tag", "--list"))

    def test_wrong_sha_event_attempt_or_manifest_cannot_prepare(self):
        for key, wrong in [("head_sha", "0" * 40), ("event", "push"), ("run_attempt", 2)]:
            original = self.run_data[key]
            self.run_data[key] = wrong
            with self.subTest(key=key):
                self.assertNotEqual(0, self.prepare()[0])
                self.assertEqual("", self.runner.git("tag", "--list"))
            self.run_data[key] = original
        self.record["version"] = "2.0.0"
        self.assertNotEqual(0, self.prepare()[0])

    def test_changed_source_invalidates_successful_preparation(self):
        self.assertEqual(0, self.prepare()[0])
        (self.root / "extra.txt").write_text("fix")
        self.commit()
        self.assertNotIn("candidate", coordinator.plan(self.runner, "1.0.0", github=self.github))

    def test_checksum_manifest_extra_file_and_failed_matrix_leg_block_preparation(self):
        self.github.failed_jobs = ("publish",)
        self.assertNotEqual(0, self.prepare()[0])
        self.github.failed_jobs = ()
        self.payloads["unexpected.bin"] = b"extra"
        self.assertNotEqual(0, self.prepare()[0])
        self.payloads.pop("unexpected.bin")
        self.payloads["SHA256SUMS"] = b"0" * 64 + b"  application.bin\n"
        self.assertNotEqual(0, self.prepare()[0])
