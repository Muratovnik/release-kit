from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock, patch

from releasekit import __version__, toolchain

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "tools" / "build_zipapp.py"


class PlatformTests(unittest.TestCase):
    def test_empty_windows_machine_uses_os_query_not_a_default_architecture(self):
        for native, expected in (("amd64", "x64"), ("arm64", "arm64"), ("x86", "x86")):
            with (
                patch.object(toolchain.sys, "platform", "win32"),
                patch.object(toolchain.platform, "machine", return_value=""),
                patch.object(
                    toolchain, "_windows_machine", create=True, return_value=native
                ) as query,
            ):
                self.assertEqual(("windows", expected), toolchain._platform_key())
                query.assert_called_once_with()

    def test_known_and_non_windows_architectures_do_not_query_windows(self):
        for operating_system, machine, expected in (
            ("win32", "AMD64", ("windows", "x64")),
            ("win32", "ARM64", ("windows", "arm64")),
            ("win32", "x86", ("windows", "x86")),
            ("linux", "aarch64", ("linux", "arm64")),
            ("darwin", "x86_64", ("darwin", "x64")),
            ("linux", "", ("linux", "")),
        ):
            with (
                patch.object(toolchain.sys, "platform", operating_system),
                patch.object(toolchain.platform, "machine", return_value=machine),
                patch.object(toolchain, "_windows_machine", create=True) as query,
            ):
                self.assertEqual(expected, toolchain._platform_key())
                query.assert_not_called()

    def test_windows_api_maps_supported_and_unsupported_processor_types(self):
        import ctypes

        for code, expected in ((9, "amd64"), (12, "arm64"), (0, "x86"), (65535, "unknown-65535")):

            def fill(pointer, code=code):
                pointer._obj.wProcessorArchitecture = code

            library = Mock()
            library.GetNativeSystemInfo.side_effect = fill
            with patch.object(ctypes, "WinDLL", create=True, return_value=library):
                self.assertEqual(expected, toolchain._windows_machine())
            self.assertIsNone(library.GetNativeSystemInfo.restype)
            self.assertEqual(1, len(library.GetNativeSystemInfo.argtypes))

    def test_windows_query_failure_does_not_guess_x64(self):
        import ctypes

        with (
            patch.object(toolchain.sys, "platform", "win32"),
            patch.object(toolchain.platform, "machine", return_value=""),
            patch.object(ctypes, "WinDLL", create=True, side_effect=OSError("query unavailable")),
            self.assertRaisesRegex(toolchain.ToolchainError, "Windows architecture"),
        ):
            toolchain._platform_key()

    def test_unsupported_windows_fallback_never_downloads_another_architecture(self):
        with tempfile.TemporaryDirectory() as temporary:
            for machine in ("x86", "unknown-65535"):
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch.object(toolchain.sys, "platform", "win32"),
                    patch.object(toolchain.platform, "machine", return_value=""),
                    patch.object(toolchain, "_windows_machine", return_value=machine),
                    patch.object(toolchain, "_download") as download,
                    self.assertRaisesRegex(toolchain.ToolchainError, "no pinned asset"),
                ):
                    toolchain.resolve("betterleaks", root=Path(temporary))
                download.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows architecture regression")
    def test_real_windows_query_without_processor_environment_in_zipapp(self):
        with tempfile.TemporaryDirectory() as temporary:
            projection = Path(temporary) / "relkit.pyz"
            subprocess.run(
                [sys.executable, str(BUILD), str(projection)],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.upper().startswith("PROCESSOR_")
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import json, sys; sys.path.insert(0, sys.argv[1]); from releasekit.toolchain import _platform_key; print(json.dumps(_platform_key()))",
                    str(projection),
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn(json.loads(result.stdout), (["windows", "x64"], ["windows", "arm64"]))


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
        self.assertIn(f"release-kit {__version__}", invoked.stdout)
        self.assertIn("Betterleaks 1.8.1", invoked.stdout)
        self.assertIn("Lychee 0.24.2", invoked.stdout)
