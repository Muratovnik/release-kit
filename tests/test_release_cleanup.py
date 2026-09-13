"""Unconfirmed process cleanup retains release ownership and recovery evidence."""

import contextlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_release import ReleaseFixture
from test_release_local import LocalFixture

from releasekit import processes, storage
from releasekit.release import coordinator
from releasekit.release.backend import Pending
from releasekit.result import Result


class ReleaseCleanupTests(ReleaseFixture):
    def fail_command(self, runner, github, state, path, workspace, no_download):
        marker = workspace.path / "owned-evidence"
        marker.write_text("retain this exact evidence", encoding="utf-8")
        workspace.remember(marker)
        self.marker = marker
        with patch.object(processes, "run", side_effect=processes.CleanupError("cleanup probe")):
            runner.call(["controlled-cleanup-probe"])

    def test_cli_returns_a_structured_cleanup_error_without_suggesting_resume(self):
        from test_json_cli import invoke

        with (
            patch.object(coordinator, "Runner", return_value=self.runner),
            patch.object(coordinator, "GitHub", return_value=self.github),
            patch.object(coordinator, "_prepare", side_effect=self.fail_command),
        ):
            code, envelope, diagnostics = invoke(
                ["release", "run", "v1.0.0", "--publish", "--json", "--root", str(self.root)]
            )
        self.assertEqual(1, code, diagnostics)
        self.assertEqual(code, envelope["exit_code"])
        self.assertEqual("release_cleanup_unconfirmed", envelope["errors"][0]["code"])
        self.assertIsNone(envelope["next_action"])
        self.assertEqual("lock_retained", envelope["warnings"][0]["code"])
        self.assertNotIn("Traceback", diagnostics)

    def test_run_retains_receipt_scratch_and_lock_and_refuses_resume(self):
        result = Result()
        with patch.object(coordinator, "_prepare", side_effect=self.fail_command):
            code, output = self.invoke(result=result)
        self.assertEqual(1, code, output)
        self.assertEqual("release_cleanup_unconfirmed", result.errors[0]["code"])
        self.assertIsNone(result.next_action)
        self.assertEqual("retain this exact evidence", self.marker.read_text())
        lock = storage.service_root(self.root) / "release.lock"
        before = lock.read_bytes()
        receipt = Path(result.data["release"]["receipt"])
        saved = json.loads(receipt.read_text())
        self.assertEqual("unconfirmed", saved["process_cleanup"])
        self.assertEqual("retained-process-cleanup-unconfirmed", saved["cleanup"])
        with patch.object(coordinator, "_prepare") as prepare:
            retry, message = self.invoke("resume")
        self.assertEqual(2, retry, message)
        prepare.assert_not_called()
        self.assertEqual(before, lock.read_bytes())
        self.assertEqual(saved, json.loads(receipt.read_text()))

    def test_resume_retains_ownership_if_a_new_command_cannot_be_reaped(self):
        with patch.object(coordinator, "_prepare", side_effect=Pending("interrupted probe")):
            code, output = self.invoke()
        self.assertEqual(3, code, output)
        lock = storage.service_root(self.root) / "release.lock"
        self.assertFalse(lock.exists())
        result = Result()
        with patch.object(coordinator, "_prepare", side_effect=self.fail_command):
            code, output = self.invoke("resume", result=result)
        self.assertEqual(1, code, output)
        self.assertTrue(lock.is_file())
        self.assertTrue(self.marker.is_file())
        self.assertEqual("unconfirmed", result.data["release"]["process_cleanup"])

    def test_owned_workspace_skips_all_cleanup_after_unconfirmed_termination(self):
        with (
            self.assertRaises(processes.CleanupError),
            storage.temporary(self.root, "cleanup-probe-") as workspace,
        ):
            marker = workspace.path / "known"
            marker.write_bytes(b"known before the command")
            workspace.remember(marker)
            raise processes.CleanupError("cleanup probe")
        self.assertEqual(b"known before the command", marker.read_bytes())
        # No process was spawned by this injection; the normal cleanup control may run.
        self.assertTrue(workspace.cleanup())
        self.assertFalse(workspace.path.exists())


class PrepareCleanupTests(LocalFixture):
    def test_local_prepare_preserves_its_evidence_and_refuses_an_immediate_retry(self):
        def fail_checks(runner, value, workspace, no_download):
            marker = workspace.path / "known"
            marker.write_bytes(b"candidate evidence")
            workspace.remember(marker)
            self.marker = marker
            with patch.object(processes, "run", side_effect=processes.CleanupError("probe")):
                runner.call(["controlled-cleanup-probe"])

        result = Result()
        with patch.object(coordinator, "_local_checks", side_effect=fail_checks):
            code, output = self.invoke("prepare", publish=False, result=result)
        self.assertEqual(2, code, output)
        self.assertEqual("release_cleanup_unconfirmed", result.errors[0]["code"])
        self.assertTrue((storage.service_root(self.root) / "release.lock").is_file())
        self.assertEqual(b"candidate evidence", self.marker.read_bytes())
        candidate = result.data["candidate"]
        self.assertEqual("cleanup-unconfirmed", candidate["status"])
        self.assertEqual(str(self.marker.parent), candidate["temporary"])
        saved = Path(candidate["receipt"]).read_bytes()
        with patch.object(coordinator, "_local_checks") as checks:
            retry, message = self.invoke("prepare", publish=False)
        self.assertEqual(2, retry, message)
        checks.assert_not_called()
        self.assertEqual(saved, Path(candidate["receipt"]).read_bytes())


class HostedPrepareCleanupTests(unittest.TestCase):
    def test_actions_candidate_retains_attempt_after_unconfirmed_cleanup(self):
        from test_release_candidate import CandidateTests

        # Reuse the existing tagless candidate fixture without inheriting its tests.
        with contextlib.ExitStack() as cleanup:
            fixture = CandidateTests()
            cleanup.callback(fixture.doCleanups)
            fixture.setUp()
            result = Result()
            with patch.object(
                coordinator, "_local_checks", side_effect=processes.CleanupError("probe")
            ):
                code, output = fixture.invoke("prepare", publish=False, ci_run=35, result=result)
            self.assertEqual(2, code, output)
            self.assertTrue((storage.service_root(fixture.root) / "release.lock").is_file())
            record = result.data["candidate"]
            self.assertEqual("cleanup-unconfirmed", record["status"])
            self.assertEqual("unconfirmed", record["process_cleanup"])
            self.assertTrue(Path(record["temporary"]).is_dir())
            self.assertEqual(
                "cleanup-unconfirmed", json.loads(Path(record["receipt"]).read_text())["status"]
            )
