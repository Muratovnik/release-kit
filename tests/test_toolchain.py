from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import zipfile
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from releasekit import toolchain

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "tools" / "build_zipapp.py"


class PinTests(unittest.TestCase):
    def test_every_supported_asset_has_a_release_sha256(self) -> None:
        for tool in toolchain.TOOLS.values():
            with self.subTest(tool=tool.name):
                self.assertRegex(tool.version, r"^\d+\.\d+\.\d+$")
                self.assertTrue(tool.assets)
                for asset in tool.assets.values():
                    self.assertRegex(asset.sha256, r"^[0-9a-f]{64}$")

    def test_executable_version_must_match_a_complete_version_token(self) -> None:
        tool = toolchain.BETTERLEAKS
        lookalike = subprocess.CompletedProcess(
            ["betterleaks", "--version"], 0, stdout="betterleaks 11.8.10\n", stderr=""
        )
        with (
            patch.object(toolchain.subprocess, "run", return_value=lookalike),
            self.assertRaises(toolchain.ToolchainError),
        ):
            toolchain._verify(tool, Path("betterleaks"))

        exact = subprocess.CompletedProcess(
            ["betterleaks", "--version"], 0, stdout="betterleaks 1.8.1\n", stderr=""
        )
        with patch.object(toolchain.subprocess, "run", return_value=exact):
            toolchain._verify(tool, Path("betterleaks"))

    def test_executable_version_timeout_is_a_stable_toolchain_error(self) -> None:
        with (
            patch.object(
                toolchain.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["betterleaks", "--version"], 30),
            ),
            self.assertRaises(toolchain.ToolchainError),
        ):
            toolchain._verify(toolchain.BETTERLEAKS, Path("betterleaks"))

    def test_cached_executable_is_verified_against_the_pinned_archive_before_execution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            executable = b"official executable"
            archive_payload = Path(name) / "fixture.zip"
            with zipfile.ZipFile(archive_payload, "w") as archive:
                archive.writestr("nested/tool.exe", executable)
            asset = toolchain.Asset("fixture.zip", sha256(archive_payload.read_bytes()).hexdigest())
            tool = toolchain.Tool(
                name="fixture",
                version="1.2.3",
                repository="example/fixture",
                release="v1.2.3",
                executable="tool.exe",
                assets={("test", "test"): asset},
            )
            cache = root / ".cache" / "release-kit" / "fixture" / "1.2.3"
            cache.mkdir(parents=True)
            cached_archive = cache / asset.filename
            cached_archive.write_bytes(archive_payload.read_bytes())
            cached_executable = cache / tool.executable
            cached_executable.write_bytes(b"tampered executable")

            with (
                patch.dict(toolchain.TOOLS, {"fixture": tool}),
                patch.object(toolchain, "_platform_key", return_value=("test", "test")),
                patch.object(toolchain, "_verify") as verify,
                self.assertRaisesRegex(toolchain.ToolchainError, "cached executable"),
            ):
                toolchain.resolve("fixture", root=root, allow_download=False)

        verify.assert_not_called()

    def test_offline_cache_requires_the_verified_release_archive(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            asset = toolchain.Asset("fixture.zip", "a" * 64)
            tool = toolchain.Tool(
                name="fixture",
                version="1.2.3",
                repository="example/fixture",
                release="v1.2.3",
                executable="tool.exe",
                assets={("test", "test"): asset},
            )
            executable = root / ".cache" / "release-kit" / "fixture" / "1.2.3" / "tool.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"claims version 1.2.3")

            with (
                patch.dict(toolchain.TOOLS, {"fixture": tool}),
                patch.object(toolchain, "_platform_key", return_value=("test", "test")),
                patch.object(toolchain, "_verify") as verify,
                self.assertRaisesRegex(toolchain.ToolchainError, "verified archive"),
            ):
                toolchain.resolve("fixture", root=root, allow_download=False)

        verify.assert_not_called()

    def test_offline_cache_miss_does_not_create_cache_directories(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            asset = toolchain.Asset("fixture.zip", "a" * 64)
            tool = toolchain.Tool(
                name="fixture",
                version="1.2.3",
                repository="example/fixture",
                release="v1.2.3",
                executable="tool.exe",
                assets={("test", "test"): asset},
            )

            with (
                patch.dict(toolchain.TOOLS, {"fixture": tool}),
                patch.object(toolchain, "_platform_key", return_value=("test", "test")),
                self.assertRaisesRegex(toolchain.ToolchainError, "verified archive"),
            ):
                toolchain.resolve("fixture", root=root, allow_download=False)

            self.assertFalse((root / ".cache").exists())


class ProjectionTests(unittest.TestCase):
    def test_the_zipapp_runs_without_installing_the_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "relkit.pyz"
            built = subprocess.run(
                [sys.executable, str(BUILD), str(output)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            invoked = subprocess.run(
                [sys.executable, str(output), "--version"],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(0, built.returncode, built.stdout + built.stderr)
        self.assertEqual(0, invoked.returncode, invoked.stdout + invoked.stderr)
        self.assertIn("release-kit 0.5.0", invoked.stdout)
        self.assertIn("Betterleaks 1.8.1", invoked.stdout)
        self.assertIn("Lychee 0.24.2", invoked.stdout)
