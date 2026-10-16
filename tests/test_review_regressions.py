"""Controls for exact candidate membership and byte binding."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_distribution
import smoke
from smoke_onboarding import fixture_environment

VERSION = "1.2.3"


class ReleaseSetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="candidate bytes ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.assets = self.root / "assets"
        self.assets.mkdir()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as cli:
            cli.writestr(
                "__main__.py",
                "import sys\n"
                f"print('release-kit {VERSION} (synthetic)' if '--version' in sys.argv "
                f"else '## [{VERSION}]')\n",
            )
        payload = stream.getvalue()
        (self.assets / smoke.CLI).write_bytes(payload)
        # The wheel is part of the published set; its contents are irrelevant here,
        # because these regressions are about the set and its declared digests.
        (self.assets / self.wheel).write_bytes(b"synthetic wheel bytes")
        self.reseal()
        self.original = {path.name: path.read_bytes() for path in self.assets.iterdir()}

    @property
    def wheel(self) -> str:
        return smoke.WHEEL.format(version=VERSION)

    def reseal(self):
        for name in (smoke.CLI, self.wheel):
            digest = smoke.digest((self.assets / name).read_bytes())
            (self.assets / (name + ".sha256")).write_text(f"{digest}  {name}\n", encoding="utf-8")
        self.receipt = {
            "schema": 1,
            "version": VERSION,
            "files": {
                name: smoke.digest((self.assets / name).read_bytes())
                for name in smoke.expected_assets(VERSION) - {smoke.RECEIPT}
            },
        }
        self.write_receipt()

    def write_receipt(self):
        (self.assets / smoke.RECEIPT).write_text(json.dumps(self.receipt), encoding="utf-8")

    def assert_refused_before_execution(self):
        with patch.object(smoke, "_run") as run, self.assertRaises((smoke.SmokeError, ValueError)):
            smoke.smoke(self.assets, self.root, VERSION)
        run.assert_not_called()

    def test_complete_set_runs_real_synthetic_zipapp(self):
        self.assertEqual([], smoke.smoke(self.assets, self.root, VERSION))
        hashes = smoke.inventory(self.assets, VERSION)
        self.assertEqual(smoke.expected_assets(VERSION), set(hashes))

    def test_empty_or_partial_receipt_never_selects_its_own_coverage(self):
        for files in ({}, {smoke.CLI: self.receipt["files"][smoke.CLI]}):
            with self.subTest(files=list(files)):
                self.receipt["files"] = files
                self.write_receipt()
                self.assert_refused_before_execution()

    def test_missing_wheel_sidecar_and_its_record_are_refused(self):
        name = self.wheel + ".sha256"
        (self.assets / name).unlink()
        self.receipt["files"].pop(name)
        self.write_receipt()
        self.assert_refused_before_execution()

    def test_extra_file_or_directory_is_refused(self):
        extra = self.assets / "unexpected"
        extra.write_text("extra", encoding="utf-8")
        self.assert_refused_before_execution()
        extra.unlink()
        extra.mkdir()
        self.assert_refused_before_execution()

    def test_receipt_rejects_extra_names_and_incorrect_hashes(self):
        for name, digest in (("unlisted", "0" * 64), (smoke.CLI, "0" * 64), (self.wheel, 42)):
            with self.subTest(name=name, digest=digest):
                self.receipt = json.loads(self.original[smoke.RECEIPT])
                self.receipt["files"][name] = digest
                self.write_receipt()
                self.assert_refused_before_execution()

    def test_both_sidecars_are_verified_even_when_manifest_is_rehashed(self):
        for name in (smoke.CLI, self.wheel):
            with self.subTest(name=name):
                for filename, data in self.original.items():
                    (self.assets / filename).write_bytes(data)
                sidecar = name + ".sha256"
                (self.assets / sidecar).write_text("0" * 64 + "  " + name, encoding="utf-8")
                self.receipt = json.loads(self.original[smoke.RECEIPT])
                self.receipt["files"][sidecar] = smoke.digest((self.assets / sidecar).read_bytes())
                self.write_receipt()
                self.assert_refused_before_execution()

    def test_duplicate_manifest_keys_are_refused(self):
        text = json.dumps(self.receipt).replace('"schema": 1', '"schema": 2, "schema": 1')
        (self.assets / smoke.RECEIPT).write_text(text, encoding="utf-8")
        self.assert_refused_before_execution()

    def test_invalid_manifest_identity_is_refused(self):
        for key, value in (("schema", True), ("version", "9.9.9"), ("files", None)):
            with self.subTest(key=key):
                self.receipt = json.loads(self.original[smoke.RECEIPT])
                self.receipt[key] = value
                self.write_receipt()
                self.assert_refused_before_execution()

    def test_expected_asset_cannot_be_a_directory(self):
        (self.assets / smoke.CLI).unlink()
        (self.assets / smoke.CLI).mkdir()
        self.assert_refused_before_execution()

    def test_package_result_binds_unchanged_bytes(self):
        report = {"checks": []}
        check_distribution.check_packages(
            [("controlled-read", [sys.executable, "-c", "pass"])],
            assets=self.assets,
            version=VERSION,
            root=self.root,
            environment=fixture_environment(),
            report=report,
        )
        self.assertTrue(report["artifacts_unchanged"])
        self.assertEqual(smoke.inventory(self.assets, VERSION), report["artifacts"])

    def test_rehashed_candidate_drift_is_rejected_after_checks(self):
        def change(*args, **kwargs):
            (self.assets / self.wheel).write_bytes(b"different but consistently rehashed bytes")
            self.reseal()

        with (
            patch.object(check_distribution, "run_stages", side_effect=change),
            self.assertRaisesRegex(check_distribution.CheckFailure, "changed"),
        ):
            check_distribution.check_packages(
                [],
                assets=self.assets,
                version=VERSION,
                root=self.root,
                environment=fixture_environment(),
                report={"checks": []},
            )

    def test_invalid_set_never_reaches_a_package_process(self):
        self.receipt["files"] = {}
        self.write_receipt()
        with (
            patch.object(check_distribution, "run_stages") as run,
            self.assertRaises(smoke.SmokeError),
        ):
            check_distribution.check_packages(
                [],
                assets=self.assets,
                version=VERSION,
                root=self.root,
                environment=fixture_environment(),
                report={"checks": []},
            )
        run.assert_not_called()

    def test_package_runner_accepts_git_free_source_with_separate_assets_and_scratch(self):
        snapshot = self.root / "snapshot"
        snapshot.mkdir()
        declaration = snapshot / "src/releasekit/__init__.py"
        declaration.parent.mkdir(parents=True)
        declaration.write_text(f'__version__ = "{VERSION}"\n', encoding="utf-8")
        scratch = self.root / "scratch"
        scratch.mkdir()
        # Exercise orchestration/storage with a real child process.
        commands = [("controlled-package-check", [sys.executable, "-c", "pass"])]
        with (
            patch.object(check_distribution, "ROOT", snapshot),
            patch.object(check_distribution, "package_checks", return_value=commands),
            patch.object(check_distribution.shutil, "which", return_value="uv"),
            patch.object(sys, "path", list(sys.path)),
            patch.dict(os.environ, fixture_environment(), clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = check_distribution.main(
                ["--assets", str(self.assets), "--version", VERSION, "--work-dir", str(scratch)]
            )
        self.assertEqual(0, code)
        results = list(scratch.glob("release-kit-checks/reports/run-*.json"))
        self.assertEqual(1, len(results))
        report = json.loads(results[0].read_text(encoding="utf-8"))
        self.assertEqual("git-free-snapshot", report["source_kind"])
        self.assertIsNone(report["source_head"])
        self.assertTrue(report["artifacts_unchanged"])
        self.assertEqual(smoke.inventory(self.assets, VERSION), report["artifacts"])
        self.assertFalse((snapshot / ".git").exists())
        self.assertFalse(Path(report["workspace"]).exists())

    def test_snapshot_runner_requires_explicit_scratch_without_creating_it(self):
        snapshot = self.root / "snapshot"
        snapshot.mkdir()
        with (
            patch.object(check_distribution, "ROOT", snapshot),
            patch.object(check_distribution.shutil, "which", return_value="uv"),
            patch.object(sys, "path", list(sys.path)),
            patch.dict(os.environ, fixture_environment(), clear=True),
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            code = check_distribution.main(["--assets", str(self.assets), "--version", VERSION])
        self.assertEqual(1, code)
        self.assertIn("explicit --work-dir", output.getvalue())
        self.assertEqual([], list(snapshot.iterdir()))


class CandidateWiringTests(unittest.TestCase):
    def test_coordinator_checks_source_then_smokes_the_actual_assets(self):
        settings = tomllib.loads((ROOT / "relkit.toml").read_text(encoding="utf-8"))["release"]
        self.assertIn(
            ["{python}", "tools/check_distribution.py", "--source-only"], settings["checks"]
        )
        self.assertIn(
            [
                "{python}",
                "tools/check_distribution.py",
                "--assets",
                "{assets}",
                "--version",
                "{version}",
                "--work-dir",
                "{temp}",
            ],
            settings["smoke"],
        )

    def test_source_only_does_not_build_a_substitute_candidate(self):
        commands = check_distribution.source_checks(ROOT)
        self.assertEqual(["base"], [name for name, _ in commands])

    def test_candidate_mode_neither_builds_nor_runs_source_gates(self):
        assets = Path("separate candidate/assets")
        commands = check_distribution.package_checks(ROOT, Path("scratch"), VERSION, assets)
        self.assertEqual(
            ["cli-smoke", "onboarding", "wheel-install", "changelog"],
            [name for name, _ in commands],
        )
        self.assertIn(str(assets / smoke.CLI), dict(commands)["onboarding"])

    def test_git_free_snapshot_does_not_discover_ancestor_git(self):
        with tempfile.TemporaryDirectory(prefix="source snapshot ") as directory:
            root = Path(directory).resolve()
            subprocess.run(
                ["git", "init", "--template=", "-q", str(root)],
                env=fixture_environment(),
                check=True,
                capture_output=True,
            )
            snapshot = root / "snapshot"
            snapshot.mkdir()
            with patch.object(check_distribution.subprocess, "run") as run:
                identity = check_distribution.source_identity(snapshot)
            run.assert_not_called()
            self.assertEqual("git-free-snapshot", identity["source_kind"])
            self.assertIsNone(identity["source_head"])


if __name__ == "__main__":
    unittest.main()
