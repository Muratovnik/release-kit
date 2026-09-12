"""Published artifacts remain inspectable after a failed publisher workflow."""

from unittest.mock import patch

from test_release import ReleaseFixture

from releasekit import __version__
from releasekit.release import coordinator
from releasekit.result import Result


class ReleaseFeedbackTests(ReleaseFixture):
    def test_pushed_tag_without_release_is_reported_separately(self):
        self.github.no_release = True
        result = Result()
        code, output = self.invoke(result=result)
        self.assertEqual(3, code, output)
        self.assertEqual("pushed", result.data["release"]["tag_state"])
        self.assertEqual("absent", result.data["release"]["publication_state"])
        self.assertIn("tag=pushed", output)

    def test_failed_ci_can_verify_artifacts_without_accepting_the_release(self):
        self.github.ci_result = "failure"
        self.assertEqual(1, self.invoke()[0])
        original = self.receipt()
        result = Result()
        with patch.object(coordinator, "_prepare", side_effect=AssertionError("cannot publish")):
            code, output = self.invoke("verify", publish=False, result=result)
        self.assertEqual(1, code, output)
        self.assertEqual(1, self.runner.pushes)
        self.assertEqual("passed", result.data["release"]["verification"])
        self.assertEqual("failed", result.data["release"]["ci_verdict"])
        self.assertEqual("incomplete", result.data["release"]["acceptance"])
        self.assertEqual(original["plan"], self.receipt()["plan"])
        self.assertEqual(original["plan_sha256"], self.receipt()["plan_sha256"])
        self.assertIsNone(result.next_action)

    def test_compatible_old_receipt_can_be_verified_without_rewriting_plan(self):
        self.github.signature_valid = False
        original_asdict = coordinator.asdict

        def old_settings(*args, **kwargs):
            value = original_asdict(*args, **kwargs)
            value.pop("require_provenance", None)
            return value

        with (
            patch.object(coordinator, "asdict", side_effect=old_settings),
            patch.object(coordinator, "__version__", "0.13.1"),
        ):
            code, output = self.invoke()
            self.assertEqual(1, code, output)
            self.assertIn("invalid signature", output)
        saved = self.receipt()
        self.github.signature_valid = True
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(0, code, output)
        self.assertEqual(saved["plan"], self.receipt()["plan"])
        self.assertEqual(__version__, self.receipt()["verifier_version"])
        self.assertEqual(1, self.runner.pushes)

    def test_human_plan_explains_actions_without_losing_json_contract(self):
        result = Result()
        code, output = self.invoke("plan", publish=False, human=True, result=result)
        self.assertEqual(0, code, output)
        self.assertTrue(output.startswith("relkit release: plan v1.0.0"))
        self.assertIn("no commands executed", output)
        self.assertIn(result.data["plan"]["plan_sha256"], output)
        self.assertEqual(0, self.runner.pushes)

    def test_verify_refuses_required_job_failure_even_if_workflow_is_green(self):
        self.github.failed_jobs = ("publish",)
        self.assertEqual(1, self.invoke()[0])
        result = Result()
        code, output = self.invoke("verify", publish=False, result=result)
        self.assertEqual(1, code, output)
        self.assertEqual("passed", result.data["release"]["verification"])
        self.assertEqual("incomplete", result.data["release"]["acceptance"])

    def test_verify_does_not_accept_corrupt_downloads_or_a_changed_tag(self):
        self.github.signature_valid = False
        self.assertEqual(1, self.invoke()[0])
        self.github.signature_valid = True
        self.github.download_valid = False
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(1, code, output)
        self.assertNotEqual("passed", self.receipt()["verification"])
        self.runner.git("tag", "-f", "v1.0.0")
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(1, code, output)
        self.assertIn("tag object changed", output)

    def test_verify_refuses_unpublished_tag_and_never_prepares_it(self):
        self.github.no_release = True
        self.assertEqual(3, self.invoke()[0])
        with patch.object(coordinator, "_prepare", side_effect=AssertionError("cannot publish")):
            code, output = self.invoke("verify", publish=False)
        self.assertEqual(1, code, output)
        self.assertIn("requires an existing published release", output)
        self.assertEqual(0, self.github.signatures_checked)

    def test_verify_still_refuses_invalid_signature_and_changed_ci_identity(self):
        self.github.signature_valid = False
        self.assertEqual(1, self.invoke()[0])
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(1, code, output)
        self.assertIn("invalid signature", output)
        self.github.signature_valid = True
        self.github.attempt = 2
        code, output = self.invoke("verify", publish=False)
        self.assertEqual(1, code, output)
        self.assertIn("CI run/attempt changed", output)
