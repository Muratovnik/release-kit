"""Controls for exact candidate membership, byte binding and the packaged launcher."""

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
import check_distribution  # noqa: E402
import smoke  # noqa: E402
import smoke_plugin  # noqa: E402
from smoke_onboarding import fixture_environment  # noqa: E402

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
        with zipfile.ZipFile(self.assets / smoke.PLUGIN, "w") as plugin:
            plugin.writestr(smoke.ENTRY, payload)
            plugin.writestr(
                smoke.INVENTORY,
                json.dumps(
                    {"version": VERSION, "files": {"tools/relkit.pyz": smoke.digest(payload)}}
                ),
            )
        self.reseal()
        self.original = {path.name: path.read_bytes() for path in self.assets.iterdir()}

    def reseal(self):
        for name in (smoke.CLI, smoke.PLUGIN):
            digest = smoke.digest((self.assets / name).read_bytes())
            (self.assets / (name + ".sha256")).write_text(f"{digest}  {name}\n", encoding="utf-8")
        self.receipt = {
            "schema": 1,
            "version": VERSION,
            "files": {
                name: smoke.digest((self.assets / name).read_bytes())
                for name in smoke.ASSETS - {smoke.RECEIPT}
            },
        }
        self.write_receipt()

    def write_receipt(self):
        (self.assets / smoke.RECEIPT).write_text(json.dumps(self.receipt), encoding="utf-8")

    def assert_refused_before_execution(self):
        with patch.object(smoke, "_run") as run:
            with self.assertRaises((smoke.SmokeError, ValueError)):
                smoke.smoke(self.assets, self.root, VERSION)
        run.assert_not_called()

    def test_complete_set_runs_real_synthetic_zipapp(self):
        self.assertEqual([], smoke.smoke(self.assets, self.root, VERSION))
        hashes = smoke.inventory(self.assets, VERSION)
        self.assertEqual(smoke.ASSETS, set(hashes))

    def test_empty_or_partial_receipt_never_selects_its_own_coverage(self):
        for files in ({}, {smoke.CLI: self.receipt["files"][smoke.CLI]}):
            with self.subTest(files=list(files)):
                self.receipt["files"] = files
                self.write_receipt()
                self.assert_refused_before_execution()

    def test_missing_plugin_sidecar_and_its_record_are_refused(self):
        name = smoke.PLUGIN + ".sha256"
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
        for name, digest in (("unlisted", "0" * 64), (smoke.CLI, "0" * 64), (smoke.PLUGIN, 42)):
            with self.subTest(name=name, digest=digest):
                self.receipt = json.loads(self.original[smoke.RECEIPT])
                self.receipt["files"][name] = digest
                self.write_receipt()
                self.assert_refused_before_execution()

    def test_both_sidecars_are_verified_even_when_manifest_is_rehashed(self):
        for name in (smoke.CLI, smoke.PLUGIN):
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
            with zipfile.ZipFile(self.assets / smoke.PLUGIN, "a") as plugin:
                plugin.writestr("changed.txt", "different but consistently rehashed bytes")
            self.reseal()

        with patch.object(check_distribution, "run_stages", side_effect=change):
            with self.assertRaisesRegex(check_distribution.CheckFailure, "changed"):
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
        with patch.object(check_distribution, "run_stages") as run:
            with self.assertRaises(smoke.SmokeError):
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
        (snapshot / "pyproject.toml").write_text(
            f'[project]\nversion = "{VERSION}"\n', encoding="utf-8"
        )
        scratch = self.root / "scratch"
        scratch.mkdir()
        # Exercise orchestration/storage with a real child, not SDK behavior.
        commands = [("controlled-package-check", [sys.executable, "-c", "pass"])]
        with (
            patch.object(check_distribution, "ROOT", snapshot),
            patch.object(check_distribution, "stages", return_value=commands),
            patch.object(check_distribution.shutil, "which", return_value="uv"),
            patch.object(sys, "path", list(sys.path)),
            patch.dict(os.environ, fixture_environment(), clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = check_distribution.main(
                ["--assets", str(self.assets), "--version", VERSION, "--work-dir", str(scratch)]
            )
        self.assertEqual(0, code)
        results = list(scratch.glob("distribution-check-*/result.json"))
        self.assertEqual(1, len(results))
        report = json.loads(results[0].read_text(encoding="utf-8"))
        self.assertEqual("git-free-snapshot", report["source_kind"])
        self.assertIsNone(report["source_head"])
        self.assertTrue(report["artifacts_unchanged"])
        self.assertEqual(smoke.inventory(self.assets, VERSION), report["artifacts"])
        self.assertFalse((snapshot / ".git").exists())

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
        commands = check_distribution.stages(ROOT, Path("scratch"), "uv", VERSION, source_only=True)
        self.assertEqual(["base", "mcp"], [name for name, _ in commands])

    def test_candidate_mode_neither_builds_nor_runs_source_gates(self):
        assets = Path("separate candidate/assets")
        commands = check_distribution.stages(ROOT, Path("scratch"), "uv", VERSION, assets)
        self.assertEqual(
            ["cli-smoke", "onboarding", "plugin-stdio"], [name for name, _ in commands]
        )
        self.assertIn(str(assets / smoke.CLI), dict(commands)["onboarding"])
        self.assertIn(str(assets / smoke.PLUGIN), dict(commands)["plugin-stdio"])

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


class PackagedLaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="configured launch ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "scripts").mkdir()
        (self.root / "sentinel.txt").write_text("right cwd", encoding="utf-8")
        (self.root / "scripts/launch.py").write_text(
            "import json,os,sys\nfrom pathlib import Path\n"
            "assert sys.argv[1:] == ['--configured', '--check']\n"
            "assert Path('sentinel.txt').read_text() == 'right cwd'\n"
            "assert os.environ['SMOKE_ENV'] == 'from-package'\n"
            f"print(json.dumps({{'valid': True, 'components': {{'cli': '{VERSION}'}}}}))\n",
            encoding="utf-8",
        )
        self.server = {
            "command": sys.executable,
            "args": ["scripts/launch.py", "--configured"],
            "cwd": ".",
            "env": {"SMOKE_ENV": "from-package"},
            "startup_timeout_sec": 10,
            "tool_timeout_sec": 23,
        }

    def launch(self):
        (self.root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"releasekit": self.server}}), encoding="utf-8"
        )
        return smoke_plugin.load_launch(self.root, fixture_environment())

    def test_command_args_cwd_and_env_are_taken_from_package(self):
        launch = self.launch()
        self.assertEqual(self.server["command"], launch.command)
        self.assertEqual(self.server["args"], launch.args)
        self.assertEqual(self.root, launch.cwd)
        self.assertEqual(10, launch.startup_timeout)
        self.assertEqual(23, launch.tool_timeout)
        smoke_plugin.check_launch(launch, VERSION)

    def test_bad_packaged_launcher_path_has_no_fallback(self):
        self.server["args"][0] = "scripts/absent.py"
        with self.assertRaises(subprocess.CalledProcessError):
            smoke_plugin.check_launch(self.launch(), VERSION)

    def test_bad_packaged_executable_has_no_python_substitution(self):
        self.server["command"] = str(self.root / "absent-python")
        with self.assertRaises(FileNotFoundError):
            smoke_plugin.check_launch(self.launch(), VERSION)

    def test_wrong_existing_cwd_is_not_repaired_by_absolute_script_path(self):
        self.server["cwd"] = "scripts"
        with self.assertRaises(subprocess.CalledProcessError):
            smoke_plugin.check_launch(self.launch(), VERSION)

    def test_environment_override_is_not_ignored(self):
        self.server["env"]["SMOKE_ENV"] = "wrong-value"
        with self.assertRaises(subprocess.CalledProcessError):
            smoke_plugin.check_launch(self.launch(), VERSION)

    def test_startup_timeout_is_enforced(self):
        (self.root / "scripts/launch.py").write_text(
            "import time\ntime.sleep(10)\n", encoding="utf-8"
        )
        self.server["startup_timeout_sec"] = 0.1
        with self.assertRaises(subprocess.TimeoutExpired):
            smoke_plugin.check_launch(self.launch(), VERSION)

    def test_invalid_cwd_and_timeouts_are_refused(self):
        for field, value in (("cwd", ".."), ("startup_timeout_sec", 0), ("tool_timeout_sec", True)):
            with self.subTest(field=field):
                original = self.server[field]
                self.server[field] = value
                with self.assertRaises(ValueError):
                    self.launch()
                self.server[field] = original


if __name__ == "__main__":
    unittest.main()
