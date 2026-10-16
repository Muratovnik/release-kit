"""`run --publish --prepare --ci-run ID` gates once and downloads the candidate once."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

from test_release_candidate import CandidateFixture
from test_release_stages import stage_events

from releasekit.release import candidate, coordinator
from releasekit.result import Result


class OneInvocationCandidateTests(CandidateFixture):
    def count_calls(self):
        calls = {"gate": 0, "candidate_downloads": 0, "asset_downloads": 0}
        previous_call = self.runner.call

        def call(args, **kwargs):
            if args[:3] == ["gh", "run", "download"]:
                calls["candidate_downloads"] += 1
            if args[-1:] == ["check.py"]:
                calls["gate"] += 1
            return previous_call(args, **kwargs)

        self.runner.call = call
        previous_download = self.github.download

        def download(asset, destination):
            calls["asset_downloads"] += 1
            return previous_download(asset, destination)

        self.github.download = download
        return calls

    def base_hash(self):
        return coordinator.fingerprint(
            coordinator.plan(self.runner, "1.0.0", github=self.github, candidate_receipt=False)
        )

    def one_invocation(self, **kwargs):
        with patch.object(coordinator, "_audit"):
            return self.invoke(prepare_here=True, ci_run=35, **kwargs)

    def held_file(self, result):
        return Path(result.data["candidate"]["temporary"]) / "assets" / "application.bin"

    def test_the_gate_runs_once_and_the_candidate_is_downloaded_once(self):
        calls = self.count_calls()
        result = Result()
        code, output = self.one_invocation(plan_hash=self.base_hash(), result=result)
        self.assertEqual(0, code, output)
        self.assertEqual(1, calls["gate"], "the local gate runs once in the one invocation")
        self.assertEqual(1, calls["candidate_downloads"])
        self.assertEqual(0, calls["asset_downloads"], "published files come from the candidate")
        self.assertEqual("tag", self.runner.git("cat-file", "-t", "v1.0.0"))
        self.assertEqual(1, self.runner.pushes)
        self.assertEqual(1, self.github.signatures_checked)
        receipt = self.receipt()
        self.assertEqual("passed", receipt["verification"])
        self.assertEqual("same", receipt["preparation"]["invocation"])
        self.assertEqual(result.data["candidate"]["attempt"], receipt["preparation"]["attempt"])
        self.assertEqual("passed", receipt["stages"]["local-checks"])
        self.assertTrue(candidate.receipt_path(self.root, "v1.0.0").exists())
        self.assertIn("acceptance=accepted", output)
        self.assertEqual([], list(self.root.glob(".git/relkit/tmp/*")), "nothing is left behind")

    def test_separate_invocations_still_run_the_gate_again(self):
        # The no-reuse rule: a commit does not identify ignored dependencies, tools or
        # hooks, so a later process never trusts an earlier process's check result.
        calls = self.count_calls()
        self.assertEqual(0, self.prepare()[0])
        self.assertEqual(1, calls["gate"])
        with patch.object(coordinator, "_audit"):
            code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertEqual(2, calls["gate"])
        self.assertEqual(2, calls["candidate_downloads"], "prepare and run each download once")
        self.assertEqual(0, calls["asset_downloads"], "run verifies against its own download")

    def test_resume_downloads_the_published_files_because_nothing_was_verified_here(self):
        self.assertEqual(0, self.one_invocation()[0])
        calls = self.count_calls()
        code, output = self.invoke("resume")
        self.assertEqual(0, code, output)
        self.assertEqual(len(self.payloads), calls["asset_downloads"])
        self.assertEqual(0, calls["gate"])

    def test_a_candidate_file_changed_since_it_was_verified_is_downloaded_not_trusted(self):
        calls = self.count_calls()
        result = Result()
        self.github.on_wait = lambda: self.held_file(result).write_bytes(b"changed on disk")
        code, output = self.one_invocation(result=result)
        self.assertEqual(0, code, output)
        self.assertEqual(1, calls["asset_downloads"], "only the changed file is downloaded")
        self.assertEqual("passed", self.receipt()["verification"])
        self.assertEqual(
            b"changed on disk", self.held_file(result).read_bytes(), "a changed file is not swept"
        )
        self.assertIn("retained unowned or changed temporary files", output)

    def test_a_corrupt_download_after_a_changed_candidate_file_still_fails_verification(self):
        result = Result()
        self.github.download_valid = False
        self.github.on_wait = lambda: self.held_file(result).write_bytes(b"changed on disk")
        code, output = self.one_invocation(result=result)
        self.assertEqual(1, code, output)
        self.assertIn("downloaded checksum/size mismatch: application.bin", output)

    def test_published_files_that_differ_from_the_candidate_fail_verification(self):
        def republish_other_bytes():
            self.payloads["application.bin"] = b"other bytes"
            self.payloads["SHA256SUMS"] = (
                hashlib.sha256(b"other bytes").hexdigest() + "  application.bin\n"
            ).encode()

        self.github.on_wait = republish_other_bytes
        code, output = self.one_invocation()
        self.assertEqual(1, code, output)
        self.assertIn("published files differ from the prepared candidate", output)
        self.assertEqual(0, self.github.signatures_checked)

    def test_a_signed_attestation_that_disagrees_with_the_published_files_still_fails(self):
        self.github.attestation_digests_differ = True
        code, output = self.one_invocation()
        self.assertEqual(1, code, output)
        self.assertIn("signed release attestation does not match", output)

    def test_the_candidate_is_revalidated_from_disk_before_the_tag(self):
        original = candidate.revalidate

        def corrupt_then_revalidate(github, value, release, run_id, directory):
            (Path(directory) / "application.bin").write_bytes(b"flipped before tagging")
            return original(github, value, release, run_id, directory)

        with patch.object(candidate, "revalidate", corrupt_then_revalidate):
            code, output = self.one_invocation()
        self.assertEqual(1, code, output)
        self.assertEqual("", self.runner.git("tag", "--list"))
        self.assertEqual(0, self.runner.pushes)

    def test_a_new_ci_attempt_between_preparation_and_the_tag_is_caught(self):
        original = candidate.revalidate

        def new_attempt(github, value, release, run_id, directory):
            self.run_data["run_attempt"] = 2
            return original(github, value, release, run_id, directory)

        with patch.object(candidate, "revalidate", new_attempt):
            code, output = self.one_invocation()
        self.assertEqual(1, code, output)
        self.assertEqual("", self.runner.git("tag", "--list"))
        self.assertEqual(0, self.runner.pushes)

    def test_prepare_in_run_needs_the_ci_run_and_creates_nothing_without_it(self):
        with patch.object(coordinator, "_audit"):
            code, output = self.invoke(prepare_here=True)
        self.assertEqual(2, code, output)
        self.assertIn("--ci-run ID", output)
        self.assertFalse((self.root / ".git/relkit/candidates").exists())
        self.assertFalse((self.root / ".git/relkit/releases").exists())
        self.assertEqual("", self.runner.git("tag", "--list"))

    def test_a_stale_hash_stops_before_the_candidate_is_downloaded(self):
        calls = self.count_calls()
        code, output = self.one_invocation(plan_hash="0" * 64)
        self.assertEqual(2, code, output)
        self.assertIn("stale", output)
        self.assertEqual(0, calls["candidate_downloads"] + calls["gate"])
        self.assertFalse((self.root / ".git/relkit/candidates").exists())

    def test_ci_run_without_prepare_is_refused_for_run(self):
        code, output = self.invoke(ci_run=35)
        self.assertEqual(2, code, output)
        self.assertIn("--ci-run is only for prepare and run --prepare", output)
        self.assertEqual("", self.runner.git("tag", "--list"))

    def test_run_log_carries_the_preparation_stages_of_the_same_invocation(self):
        code, output = self.one_invocation()
        self.assertEqual(0, code, output)
        log = coordinator._state_path(self.root, "v1.0.0").parent / "run.log"
        starts = [name for name, event in stage_events(log) if event == "start"]
        for name in ("local-checks", "candidate-download", "candidate-smoke"):
            self.assertEqual(1, starts.count(name), (name, starts))
        self.assertLess(starts.index("candidate-download"), starts.index("annotated-tag"))

    def test_a_failed_preparation_leaves_its_stage_lines_and_allows_a_retry(self):
        self.run_data["conclusion"] = "failure"
        code, output = self.one_invocation()
        self.assertNotEqual(0, code)
        run_log = coordinator._state_path(self.root, "v1.0.0").parent / "run.log"
        self.assertIn(("preflight", "failed"), stage_events(run_log))
        self.assertFalse(coordinator._state_path(self.root, "v1.0.0").exists())
        self.assertFalse((self.root / ".git/relkit/release.lock").exists())
        self.run_data["conclusion"] = "success"
        code, output = self.one_invocation()
        self.assertEqual(0, code, output)

    def test_plan_names_the_one_invocation_command_for_the_actions_adapter(self):
        code, output = self.invoke("plan", publish=False, human=True)
        self.assertEqual(0, code, output)
        self.assertIn("relkit release prepare v1.0.0 --ci-run ID, then plan again", output)
        self.assertIn("run v1.0.0 --publish --prepare --ci-run ID --plan-hash", output)
