"""Validate the real-engine smoke's fixture and oracle, not scanner acceptance."""

import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import smoke_onboarding


class SecretSmokeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic credential ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "synthetic-credential.txt"

    def test_never_issued_fixture_is_checked_then_removed_and_rechecked(self):
        observed = []

        def invoke(root, environment, expected, *arguments):
            self.assertEqual(self.root, root)
            self.assertEqual(("audit",), arguments)
            observed.append(expected)
            if expected == 1:
                self.assertRegex(self.path.read_text(), r"github_token = ghp_[0-9a-f]{36}\n")
                return {"data": {"engines": {"betterleaks": 1, "lychee": 0}}}
            self.assertFalse(self.path.exists())
            return {"data": {"engines": {"betterleaks": 0, "lychee": 0}}}

        with patch.object(smoke_onboarding, "invoke", side_effect=invoke):
            smoke_onboarding.check_secret_detection(self.root, {})
        self.assertEqual([1, 0], observed)

    def test_zero_exit_or_operational_failure_is_not_secret_detection(self):
        for engine in (0, 2, None):
            with (
                self.subTest(engine=engine),
                patch.object(
                    smoke_onboarding,
                    "invoke",
                    return_value={"data": {"engines": {"betterleaks": engine, "lychee": 0}}},
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "secret scanner"):
                    smoke_onboarding.check_secret_detection(self.root, {})
                self.assertFalse(self.path.exists())

    def test_failed_scan_still_cleans_only_its_own_synthetic_file(self):
        with (
            patch.object(smoke_onboarding, "invoke", side_effect=RuntimeError("scan failed")),
            self.assertRaisesRegex(RuntimeError, "scan failed"),
        ):
            smoke_onboarding.check_secret_detection(self.root, {})
        self.assertFalse(self.path.exists())

    def test_existing_file_is_never_replaced_or_deleted(self):
        self.path.write_text("existing data", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            smoke_onboarding.check_secret_detection(self.root, {})
        self.assertEqual("existing data", self.path.read_text())

    def test_clean_recovery_is_required(self):
        responses = [
            {"data": {"engines": {"betterleaks": 1, "lychee": 0}}},
            {"data": {"engines": {"betterleaks": 1, "lychee": 0}}},
        ]
        with (
            patch.object(smoke_onboarding, "invoke", side_effect=responses),
            self.assertRaisesRegex(RuntimeError, "recover"),
        ):
            smoke_onboarding.check_secret_detection(self.root, {})
        self.assertFalse(self.path.exists())

    def test_source_contains_no_complete_token(self):
        source = (ROOT / "tools/smoke_onboarding.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"ghp_[A-Za-z0-9]{36}", source))


if __name__ == "__main__":
    unittest.main()
