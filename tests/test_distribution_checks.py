"""Regression protection for full-gate discovery, failure handling and starter setup."""

from __future__ import annotations

import importlib.metadata
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_distribution
import check_mcp
import smoke_onboarding
import smoke_plugin


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gate discovery ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.name = "test_probe_" + uuid.uuid4().hex
        self.addCleanup(sys.modules.pop, self.name, None)
        self.path_patch = patch.object(sys, "path", list(sys.path))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def write_test(self, folder, body):
        (folder / (self.name + ".py")).write_text(body, encoding="utf-8")

    def test_base_discovery_can_pass_while_explicit_mcp_discovery_fails(self):
        # Reproduce the original omission, not just an assertion on a script name.
        nested = self.root / "mcp"
        nested.mkdir()
        self.write_test(
            nested,
            "import unittest\nclass Probe(unittest.TestCase):\n"
            "    def test_failure(self):\n        self.fail('MCP regression')\n",
        )
        name = "test_outer_" + uuid.uuid4().hex
        self.addCleanup(sys.modules.pop, name, None)
        (self.root / (name + ".py")).write_text(
            "import unittest\nclass Outer(unittest.TestCase):\n"
            "    def test_valid(self):\n        self.assertEqual(2, 1 + 1)\n",
            encoding="utf-8",
        )
        outer = unittest.TestLoader().discover(str(self.root), pattern="test_*.py")
        result = unittest.TextTestRunner(stream=io.StringIO()).run(outer)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(1, result.testsRun)
        self.assertEqual(1, check_mcp.run_suite(nested, stream=io.StringIO()))

    def test_valid_explicit_suite_passes(self):
        self.write_test(
            self.root,
            "import unittest\nclass Probe(unittest.TestCase):\n"
            "    def test_valid(self):\n        self.assertEqual(2, 1 + 1)\n",
        )
        self.assertEqual(0, check_mcp.run_suite(self.root, stream=io.StringIO()))

    def test_empty_discovery_refuses(self):
        with self.assertRaisesRegex(ValueError, "no MCP tests"):
            check_mcp.run_suite(self.root, stream=io.StringIO())

    def test_all_skipped_suite_refuses(self):
        self.write_test(
            self.root,
            "import unittest\nclass Probe(unittest.TestCase):\n"
            "    @unittest.skip('unavailable')\n    def test_skipped(self):\n        pass\n",
        )
        with self.assertRaisesRegex(ValueError, "all discovered"):
            check_mcp.run_suite(self.root, stream=io.StringIO())

    def test_import_failure_is_not_reported_as_empty_or_success(self):
        self.write_test(self.root, "raise ImportError('missing integration dependency')\n")
        self.assertEqual(1, check_mcp.run_suite(self.root, stream=io.StringIO()))

    def test_sdk_pin_is_read_from_plugin_not_duplicated(self):
        folder = self.root / "plugins/release-kit"
        folder.mkdir(parents=True)
        (folder / "pyproject.toml").write_text(
            '[project]\ndependencies = ["mcp==9.8.7"]\n', encoding="utf-8"
        )
        with patch("importlib.metadata.version", return_value="9.8.7"):
            self.assertEqual("9.8.7", check_mcp.require_sdk(self.root))
        with (
            patch("importlib.metadata.version", return_value="9.8.6"),
            self.assertRaisesRegex(ValueError, "mismatch"),
        ):
            check_mcp.require_sdk(self.root)
        with (
            patch(
                "importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError
            ),
            self.assertRaises(importlib.metadata.PackageNotFoundError),
        ):
            check_mcp.require_sdk(self.root)


class GateTests(unittest.TestCase):
    def test_joint_distribution_explicitly_requires_all_components(self):
        root, scratch = Path("source"), Path("scratch")
        source = dict(check_distribution.source_checks(root, "uv"))
        packages = dict(
            check_distribution.package_checks(root, scratch, "uv", "1.2.3", scratch / "assets")
        )
        self.assertEqual({"base", "mcp"}, set(source))
        self.assertEqual({"cli-smoke", "onboarding", "plugin-stdio"}, set(packages))
        mcp = source["mcp"]
        self.assertIn("--locked", mcp)
        self.assertIn("--no-python-downloads", mcp)
        self.assertIn(str(root / "tools/check_mcp.py"), mcp)
        self.assertIn(str(scratch / "assets/release-kit-plugin.zip"), packages["plugin-stdio"])

    def test_provided_candidate_bytes_are_checked_without_a_rebuild(self):
        assets = Path("source/downloaded")
        commands = check_distribution.package_checks(
            Path("source"), Path("scratch"), "uv", "1.2.3", assets
        )
        self.assertEqual(["cli-smoke", "onboarding", "plugin-stdio"], [n for n, _ in commands])
        self.assertIn(str(assets / "relkit.pyz"), dict(commands)["onboarding"])
        self.assertIn(str(assets / "release-kit-plugin.zip"), dict(commands)["plugin-stdio"])

    def test_mcp_failure_stops_the_gate_after_a_passing_base(self):
        report = {"checks": []}
        outcomes = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 7)]
        with (
            patch("releasekit.processes.run", side_effect=outcomes) as run,
            self.assertRaisesRegex(check_distribution.CheckFailure, "mcp failed"),
        ):
            check_distribution.run_stages(
                [("base", ["base"]), ("mcp", ["mcp"]), ("build", ["build"])],
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
                [("mcp", ["missing"])], root=ROOT, environment={}, report=report
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

    def test_marketplace_example_uses_the_extracted_plugin_location(self):
        document = json.loads((ROOT / "examples/plugin-marketplace.json").read_text())
        self.assertEqual("local-release-kit", document["name"])
        source = document["plugins"][0]["source"]
        self.assertEqual({"source": "local", "path": "./plugins/release-kit"}, source)


class ArchiveTests(unittest.TestCase):
    def test_valid_package_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="archive space ") as directory:
            root = Path(directory).resolve()
            archive = root / "plugin.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("release-kit/README.md", "readme")
            package = smoke_plugin.extract(archive, root / "extracted space")
            self.assertEqual("readme", (package / "README.md").read_text())
            with self.assertRaises(FileExistsError):
                smoke_plugin.extract(archive, root / "extracted space")

    def test_unsafe_archive_does_not_write_outside_its_new_directory(self):
        for name in (
            "../escape",
            "/escape",
            "other/file",
            "release-kit/../escape",
            "release-kit/back\\slash",
            "release-kit/C:escape",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                archive = root / "plugin.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    entry = zipfile.ZipInfo(name)
                    # The constructor normalizes backslashes on Windows. Write
                    # the intended raw entry so the rejection control is real.
                    entry.filename = name
                    output.writestr(entry, "unexpected")
                with zipfile.ZipFile(archive) as recorded:
                    self.assertEqual(name, recorded.infolist()[0].orig_filename)
                with self.assertRaises(ValueError):
                    smoke_plugin.extract(archive, root / "out")
                self.assertFalse((root / "escape").exists())
                self.assertEqual([], list((root / "out").iterdir()))


if __name__ == "__main__":
    unittest.main()
