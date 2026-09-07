"""Distribution checks without importing the optional MCP SDK."""

import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from releasekit import __version__, distribution, storage
from releasekit.plugin import Bundle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
BUILDER = runpy.run_path(str(ROOT / "tools/build_plugin.py"))


class PluginBuildTests(unittest.TestCase):
    def test_distributions_carry_the_license_and_human_package_metadata(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertTrue(project.get("authors"), "the declared human maintainer must be identified")
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "plugin.zip"
            BUILDER["build_plugin"](output)
            with zipfile.ZipFile(output) as plugin:
                self.assertEqual(license_text, plugin.read("release-kit/LICENSE").decode())
                payload = plugin.read("release-kit/tools/relkit.pyz")
            with zipfile.ZipFile(io.BytesIO(payload)) as cli:
                metadata = json.loads(cli.read(distribution.BUILD_INFO))
                self.assertEqual(license_text, metadata["license"]["text"])
                self.assertEqual("MIT", metadata["license"]["expression"])
                self.assertEqual(project["authors"], metadata["authors"])
            self.assertEqual(__version__, distribution.inspect(payload).version)

    def test_joint_release_exports_exact_bundled_cli_and_refuses_overwrite(self):
        builder = runpy.run_path(str(ROOT / "tools/build_release.py"))["build_release"]
        with tempfile.TemporaryDirectory(prefix="release set ") as temporary:
            folder = Path(temporary)
            one, two = folder / "one", folder / "two"
            # Deliberately from the worktree: this checks the builder, not the tree.
            self.assertEqual(builder(one, allow_divergent=True), builder(two, allow_divergent=True))
            self.assertEqual(__version__, json.loads((one / "release.json").read_text())["version"])
            with zipfile.ZipFile(one / "release-kit-plugin.zip") as archive:
                self.assertEqual(
                    (one / "relkit.pyz").read_bytes(), archive.read("release-kit/tools/relkit.pyz")
                )
                archive.extractall(folder)
            bundle = Bundle(folder / "release-kit")
            self.assertEqual(
                "aligned",
                bundle.alignment(distribution.inspect((one / "relkit.pyz").read_bytes()))["state"],
            )
            with self.assertRaisesRegex(ValueError, "new or empty"):
                builder(one, allow_divergent=True)
            self.assertEqual((one / "relkit.pyz").read_bytes(), (two / "relkit.pyz").read_bytes())
            bundle.path.write_bytes(bundle.path.read_bytes() + b"drift")
            with self.assertRaisesRegex(ValueError, "source changed"):
                bundle.check()

    def test_runtime_is_owned_local_and_checks_cache_paths_before_invocation(self):
        with tempfile.TemporaryDirectory(prefix="plugin space ") as temporary:
            folder = Path(temporary)
            output = folder / "plugin.zip"
            BUILDER["build_plugin"](output)
            with zipfile.ZipFile(output) as archive:
                archive.extractall(folder)
            package = folder / "release-kit"
            launcher = runpy.run_path(str(package / "scripts/launch.py"))
            runtime = package / ".runtime"
            with (
                patch.object(sys, "path", list(sys.path)),
                patch("shutil.which", return_value="uv"),
                patch("subprocess.call", return_value=0) as invoke,
                patch.dict(os.environ, {"UV_PROJECT_ENVIRONMENT": "unowned", "UV_OFFLINE": "1"}),
                patch.object(storage, "inside", wraps=storage.inside) as checked,
            ):
                self.assertEqual(0, launcher["main"]([]))
                environment = invoke.call_args.kwargs["env"]
                self.assertEqual(str(runtime / "venv"), environment["UV_PROJECT_ENVIRONMENT"])
                self.assertEqual(str(runtime / "cache"), environment["UV_CACHE_DIR"])
                self.assertEqual(str(runtime / "tmp"), environment["TEMP"])
                self.assertNotIn("UV_OFFLINE", environment)
                for leaf in ("tmp", "cache", "venv"):
                    self.assertIn(((package, runtime / leaf),), checked.call_args_list)
                self.assertEqual(0, launcher["main"]([]))
                (runtime / "owner.json").write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "ownership/version"):
                    launcher["main"]([])
                self.assertEqual(2, invoke.call_count)

    def test_deterministic_complete_package_and_read_only_check(self):
        with tempfile.TemporaryDirectory(prefix="plugin space ") as temporary:
            folder = Path(temporary)
            one, two = folder / "one.zip", folder / "two.zip"
            self.assertEqual(BUILDER["build_plugin"](one), BUILDER["build_plugin"](two))
            self.assertEqual(one.read_bytes(), two.read_bytes())
            with zipfile.ZipFile(one) as archive:
                inventory = json.loads(archive.read("release-kit/package.json"))
                self.assertIn("lib/releasekit_mcp/projects.py", inventory["files"])
                self.assertIn("tools/relkit.pyz", inventory["files"])
                self.assertEqual(len(inventory["files"]) + 1, len(archive.namelist()))
                for name, digest in inventory["files"].items():
                    self.assertEqual(
                        digest, hashlib.sha256(archive.read("release-kit/" + name)).hexdigest()
                    )
                    self.assertNotIn(".runtime", name)
                    self.assertNotIn("__pycache__", name)
                archive.extractall(folder)
            package = folder / "release-kit"
            environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            mcp = json.loads((package / ".mcp.json").read_text())["mcpServers"]["releasekit"]
            self.assertEqual(".", mcp["cwd"])
            self.assertEqual("python", mcp["command"])
            self.assertTrue(all("${" not in argument for argument in mcp["args"]))
            check = [sys.executable, *mcp["args"], "--check"]
            result = subprocess.run(
                check,
                cwd=package / mcp["cwd"],
                capture_output=True,
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(json.loads(result.stdout)["valid"])
            self.assertFalse((package / ".runtime").exists())
            (package / "skills/release-kit/SKILL.md").write_text("modified", encoding="utf-8")
            result = subprocess.run(
                check,
                cwd=package / mcp["cwd"],
                capture_output=True,
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn(b"payload changed", result.stderr)

    def test_version_mismatch_refuses_packaging(self):
        with (
            patch("json.loads", return_value={"name": "release-kit", "version": "0.0.0"}),
            self.assertRaisesRegex(ValueError, "versions must agree"),
        ):
            BUILDER["payloads"]()


if __name__ == "__main__":
    unittest.main()
