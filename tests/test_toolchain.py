from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
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
        self.assertIn("release-kit 0.2.0", invoked.stdout)
        self.assertIn("Betterleaks 1.8.1", invoked.stdout)
        self.assertIn("Lychee 0.24.2", invoked.stdout)
