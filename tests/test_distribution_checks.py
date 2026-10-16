"""Regression protection for gate composition, failure handling and starter setup."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_distribution
import smoke_onboarding


class GateTests(unittest.TestCase):
    def test_distribution_check_covers_the_source_suite_and_every_package_check(self):
        root, scratch = Path("source"), Path("scratch")
        source = dict(check_distribution.source_checks(root))
        packages = dict(
            check_distribution.package_checks(root, scratch, "1.2.3", scratch / "assets")
        )
        self.assertEqual({"base"}, set(source))
        self.assertEqual({"cli-smoke", "onboarding", "wheel-install", "changelog"}, set(packages))
        # Source checks run the repository's own runner; they need neither uv nor a lock.
        self.assertEqual(str(root / "tools/check.py"), source["base"][-1])
        draft = packages["changelog"]
        self.assertEqual(str(scratch / "assets/relkit.pyz"), draft[draft.index("--artifact") + 1])
        self.assertEqual(str(scratch / "changelog space"), draft[draft.index("--work-dir") + 1])

    def test_provided_candidate_bytes_are_checked_without_a_rebuild(self):
        assets = Path("source/downloaded")
        commands = check_distribution.package_checks(
            Path("source"), Path("scratch"), "1.2.3", assets
        )
        self.assertEqual(
            ["cli-smoke", "onboarding", "wheel-install", "changelog"],
            [n for n, _ in commands],
        )
        self.assertIn(str(assets / "relkit.pyz"), dict(commands)["onboarding"])

    def test_a_failing_stage_stops_the_gate_after_a_passing_one(self):
        report = {"checks": []}
        outcomes = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 7)]
        with (
            patch("releasekit.processes.run", side_effect=outcomes) as run,
            self.assertRaisesRegex(check_distribution.CheckFailure, "second failed"),
        ):
            check_distribution.run_stages(
                [("first", ["first"]), ("second", ["second"]), ("build", ["build"])],
                root=ROOT,
                environment={},
                report=report,
            )
        self.assertEqual(2, run.call_count)
        self.assertEqual(["passed", "failed"], [item["status"] for item in report["checks"]])
        self.assertEqual(7, report["checks"][-1]["exit_code"])

    def test_unavailable_process_leaves_a_failed_stage(self):
        report = {"checks": []}
        with (
            patch("releasekit.processes.run", side_effect=FileNotFoundError),
            self.assertRaises(FileNotFoundError),
        ):
            check_distribution.run_stages(
                [("missing", ["missing"])], root=ROOT, environment={}, report=report
            )
        self.assertEqual("interrupted-or-unavailable", report["checks"][0]["status"])

    def test_real_nonzero_subprocess_cannot_become_success(self):
        report = {"checks": []}
        with self.assertRaises(check_distribution.CheckFailure):
            check_distribution.run_stages(
                [("controlled-failure", [sys.executable, "-c", "raise SystemExit(9)"])],
                root=ROOT,
                environment=smoke_onboarding.fixture_environment(),
                report=report,
            )
        self.assertEqual(9, report["checks"][0]["exit_code"])


class StarterTests(unittest.TestCase):
    def test_minimal_readme_config_is_the_shipped_config(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in ("relkit.toml", ".betterleaks.toml"):
            content = (ROOT / "examples/audit" / name).read_text(encoding="utf-8")
            self.assertIn("```toml\n" + content + "```", readme)
        policy = (ROOT / "examples/audit/relkit.toml").read_text(encoding="utf-8")
        self.assertNotIn("check_secrets = false", policy)
        self.assertNotIn("check_links = false", policy)

    def test_fixture_uses_shipped_files_and_does_not_adopt_existing_data(self):
        with tempfile.TemporaryDirectory(prefix="starter space ") as directory:
            parent = Path(directory).resolve()
            artifact = parent / "fixture.pyz"
            # Only fixture setup is tested here; this payload is never executed.
            artifact.write_bytes(b"fixture setup, not an executable CLI")
            root = parent / "adopter"
            environment = smoke_onboarding.create_project(root, artifact)
            for name in ("relkit.toml", ".betterleaks.toml", ".gitignore"):
                self.assertEqual(
                    (ROOT / "examples/audit" / name).read_bytes(), (root / name).read_bytes()
                )
            self.assertEqual("", smoke_onboarding.git(root, environment, "remote"))
            self.assertEqual("", smoke_onboarding.git(root, environment, "status", "--porcelain"))
            with self.assertRaises(FileExistsError):
                smoke_onboarding.create_project(root, artifact)
            self.assertEqual(artifact.read_bytes(), (root / ".github/relkit.pyz").read_bytes())

    def test_cold_fixture_satisfies_required_ignore_before_any_scanner_runs(self):
        with tempfile.TemporaryDirectory(prefix="cold install ") as directory:
            parent = Path(directory).resolve()
            artifact = parent / "fixture.pyz"
            artifact.write_bytes(b"setup only")
            root = parent / "adopter"
            environment = smoke_onboarding.create_project(root, artifact)
            smoke_onboarding.git(
                root, environment, "check-ignore", "--quiet", "--no-index", "--", ".cache"
            )
            self.assertEqual([], list((root / ".cache").iterdir()))
            # Negative control: reproduce the old failure, without repairing the policy.
            (root / ".cache").rmdir()
            with self.assertRaises(subprocess.CalledProcessError):
                smoke_onboarding.git(
                    root, environment, "check-ignore", "--quiet", "--no-index", "--", ".cache"
                )

    def test_fixture_does_not_inherit_credentials_or_git_location(self):
        with patch.dict(
            os.environ,
            {
                "GH_TOKEN": "synthetic",
                "GIT_DIR": "elsewhere",
                "RELKIT_LYCHEE": "unverified",
                "PYTHONPATH": "source",
            },
        ):
            environment = smoke_onboarding.fixture_environment()
        for key in ("GH_TOKEN", "GIT_DIR", "RELKIT_LYCHEE", "PYTHONPATH"):
            self.assertNotIn(key, environment)
        self.assertEqual(os.devnull, environment["GIT_CONFIG_GLOBAL"])
        self.assertEqual("1", environment["GIT_CONFIG_NOSYSTEM"])


if __name__ == "__main__":
    unittest.main()
