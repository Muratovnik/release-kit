"""Check-run ownership and cleanup; controlled child probes stand in for the real gates."""

import contextlib
import io
import json
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
from smoke_onboarding import create_project, fixture_environment, git


class CheckLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="check lifecycle ")
        self.addCleanup(temporary.cleanup)
        parent = Path(temporary.name).resolve()
        artifact = parent / "setup.pyz"
        artifact.write_bytes(b"fixture setup only")
        self.root = parent / "source"
        self.environment = create_project(self.root, artifact)
        declaration = self.root / "src/releasekit/__init__.py"
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        git(self.root, self.environment, "add", "--", "src/releasekit/__init__.py")
        git(self.root, self.environment, "commit", "-qm", "test: declare check version")
        self.state = self.root / ".cache/release-kit-checks"

    def invoke(self, body="pass"):
        # Real child process and real storage, controlled work rather than a real gate.
        commands = [("controlled-source", [sys.executable, "-c", body])]
        with (
            patch.object(check_distribution, "ROOT", self.root),
            patch.object(check_distribution, "source_checks", return_value=commands),
            patch.object(check_distribution.shutil, "which", return_value="uv"),
            patch.object(check_distribution, "package_checks") as packages,
            patch.object(sys, "path", list(sys.path)),
            patch.dict(os.environ, fixture_environment(), clear=True),
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            code = check_distribution.main(["--source-only"])
        packages.assert_not_called()
        return code, output.getvalue(), errors.getvalue()

    def reports(self):
        return [
            json.loads(p.read_text(encoding="utf-8")) for p in self.state.glob("reports/*.json")
        ]

    def test_full_source_suite_can_exceed_thirty_minutes_but_still_has_a_limit(self):
        for elapsed, expected in ((1801, 0), (3601, 1)):
            with self.subTest(elapsed=elapsed):

                def command(args, *, timeout, duration=elapsed, **kwargs):
                    # Model elapsed runtime at the runner boundary without a long sleep.
                    if duration > timeout:
                        raise subprocess.TimeoutExpired(args, timeout)
                    return subprocess.CompletedProcess(args, 0)

                with patch("releasekit.processes.run", side_effect=command):
                    code, output, _ = self.invoke()
                self.assertEqual(expected, code, output)
                self.assertFalse((self.state / "check.lock").exists())
                if expected:
                    self.assertIn('"status": "timed-out"', output)

    def test_repeated_runs_keep_their_reports_but_remove_successful_workspaces(self):
        self.assertEqual(0, self.invoke()[0])
        code, output, _ = self.invoke()
        self.assertEqual(0, code)
        reports = self.reports()
        self.assertEqual(2, len(reports))
        self.assertEqual(2, len({r["workspace"] for r in reports}))
        self.assertTrue(all(r["cleanup"] == "removed" for r in reports))
        self.assertTrue(all(not Path(r["workspace"]).exists() for r in reports))
        self.assertFalse((self.state / "check.lock").exists())
        self.assertIn('"status": "passed"', output)
        self.assertIn("distribution-check: result ", output)
        self.assertEqual("", git(self.root, self.environment, "status", "--porcelain"))

    def test_failed_child_keeps_its_diagnostics_and_releases_only_its_lock(self):
        code, output, _ = self.invoke(
            "import os\nfrom pathlib import Path\n"
            "(Path(os.environ['TMP'])/'diagnostic.txt').write_text('keep')\n"
            "raise SystemExit(21)\n"
        )
        self.assertEqual(1, code)
        report = self.reports()[0]
        self.assertEqual("failed", report["status"])
        self.assertEqual(21, report["checks"][0]["exit_code"])
        self.assertEqual("retained", report["cleanup"])
        self.assertEqual("keep", (Path(report["workspace"]) / "tmp/diagnostic.txt").read_text())
        self.assertFalse((self.state / "check.lock").exists())
        self.assertIn('"status": "failed"', output)

    def test_unknown_data_retains_successful_run_for_inspection(self):
        code, _, _ = self.invoke(
            "import os\nfrom pathlib import Path\n"
            "(Path(os.environ['TMP']).parent/'unrecognized.txt').write_text('do not sweep')\n"
        )
        self.assertEqual(0, code)
        report = self.reports()[0]
        self.assertEqual("retained", report["cleanup"])
        self.assertTrue(Path(report["workspace"]).exists())

    def test_a_successful_run_removes_the_tmp_its_child_tools_filled(self):
        # `uv` leaves the same cache lock in TMP on every run, so requiring an empty
        # `tmp` retained all seventeen workspaces this repository had accumulated.
        code, _, _ = self.invoke(
            "import os\nfrom pathlib import Path\n"
            "(Path(os.environ['TMP'])/'uv-ea7e97d36fa7f037.lock').touch()\n"
        )
        self.assertEqual(0, code)
        report = self.reports()[0]
        self.assertEqual("removed", report["cleanup"])
        self.assertFalse(Path(report["workspace"]).exists())

    def test_successful_package_fixtures_are_removed_with_their_run(self):
        code, _, _ = self.invoke(
            "import os\nfrom pathlib import Path\n"
            "root=Path(os.environ['TMP']).parent\n"
            "for name in ('wheel install', 'changelog space'):\n"
            " fixture=root/name;fixture.mkdir()\n"
            " (fixture/'owned-output.txt').write_text('temporary package check')\n"
        )
        self.assertEqual(0, code)
        report = self.reports()[0]
        self.assertEqual("removed", report["cleanup"])
        self.assertFalse(Path(report["workspace"]).exists())

    def test_busy_check_state_is_not_mutated_or_unlocked_by_another_run(self):
        self.assertEqual(0, self.invoke()[0])
        lock = self.state / "check.lock"
        lock.write_text("999999\n", encoding="utf-8")
        code, _, error = self.invoke("raise AssertionError('must not run')")
        self.assertEqual(1, code)
        self.assertIn("busy", error)
        self.assertEqual("999999\n", lock.read_text())
        self.assertEqual(1, len(self.reports()))

    def test_foreign_state_is_not_adopted_or_deleted(self):
        self.state.mkdir()
        marker = self.state / "owner.json"
        marker.write_text('{"owner":"another tool"}', encoding="utf-8")
        code, _, error = self.invoke()
        self.assertEqual(1, code)
        self.assertIn("ownership", error)
        self.assertEqual('{"owner":"another tool"}', marker.read_text())
        self.assertFalse((self.state / "check.lock").exists())

    def test_check_environment_ignores_inherited_uv_and_import_settings(self):
        from releasekit import storage

        self.state.mkdir()
        workspace = self.state / "run"
        workspace.mkdir()
        (workspace / "tmp").mkdir()
        inherited = {"UV_PROJECT_ENVIRONMENT": "/outside", "UV_OFFLINE": "1", "PYTHONPATH": "x"}
        with patch.dict(os.environ, inherited):
            env = check_distribution.check_environment(self.root, self.state, workspace)
        self.assertFalse([key for key in env if key.startswith("UV_")])
        self.assertEqual(str(self.root / "src"), env["PYTHONPATH"])
        self.assertEqual(str(workspace / "tmp"), env["TMP"])
        self.assertEqual(workspace, storage.checked(workspace))

    def test_source_checks_do_not_require_uv(self):
        # Only the wheel installation needs uv, so a source-only run must not demand it.
        with (
            patch.object(check_distribution.shutil, "which", return_value=None),
            patch.object(check_distribution, "ROOT", self.root),
            patch.object(
                check_distribution,
                "source_checks",
                return_value=[("controlled-source", [sys.executable, "-c", "pass"])],
            ),
            patch.object(sys, "path", list(sys.path)),
            patch.dict(os.environ, fixture_environment(), clear=True),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            code = check_distribution.main(["--source-only"])
            packages = check_distribution.main(["--assets", str(self.root), "--version", "1.2.3"])
        self.assertEqual(0, code, errors.getvalue())
        self.assertEqual(1, packages)
        self.assertIn("install uv", errors.getvalue())

    def test_alias_fixture_root_is_retained_without_following_it(self):
        from releasekit import storage

        workspace = self.root / ".cache/run"
        workspace.mkdir()
        (workspace / "tmp").mkdir()
        target = self.root / ".cache/other"
        target.mkdir()
        (target / "keep").write_text("keep", encoding="utf-8")
        try:
            (workspace / "assets").symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("creating directory symlinks is not permitted on this host")
        with self.assertRaises(storage.StorageError):
            check_distribution.clean_success(workspace, storage.identity(workspace))
        self.assertTrue((target / "keep").is_file())

    def test_release_workflow_runs_source_only_before_one_candidate_package_matrix(self):
        text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("python tools/check_distribution.py --source-only", text)
        self.assertNotIn("run: python tools/check_distribution.py\n", text)
        self.assertEqual(1, text.count("python tools/check_distribution.py --assets"))
        self.assertIn("needs: candidate", text)
        self.assertIn("needs: check", text)


if __name__ == "__main__":
    unittest.main()
