"""The installable form of the published artifact, checked without installing it."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import runpy
import sys
import tempfile
import unittest
import zipfile
from email.parser import Parser
from pathlib import Path

from releasekit import __version__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import smoke

BUILDER = runpy.run_path(str(ROOT / "tools/build_wheel.py"))
NAME = f"release_kit-{__version__}-py3-none-any.whl"


class WheelBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wheel build ")
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name).resolve()
        self.wheel = BUILDER["build"](self.output)

    def entries(self) -> dict[str, bytes]:
        with zipfile.ZipFile(self.wheel) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def test_the_wheel_is_a_function_of_the_commit_not_of_the_host(self):
        # Same reason the zipapp fixes its timestamps: a release artifact that differs
        # per build cannot be compared against the one that was published.
        first = self.wheel.read_bytes()
        with tempfile.TemporaryDirectory(prefix="wheel rebuild ") as second:
            again = BUILDER["build"](Path(second).resolve())
            self.assertEqual(first, again.read_bytes())
            self.assertEqual(NAME, again.name)

    def test_the_record_covers_every_member_with_its_own_digest(self):
        members = self.entries()
        record = members.pop(f"release_kit-{__version__}.dist-info/RECORD").decode("utf-8")
        rows = {row[0]: row for row in csv.reader(io.StringIO(record)) if row}
        self.assertEqual(set(members) | {f"release_kit-{__version__}.dist-info/RECORD"}, set(rows))
        for name, payload in members.items():
            expected = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
            self.assertEqual(f"sha256={expected.decode('ascii')}", rows[name][1], name)
            self.assertEqual(str(len(payload)), rows[name][2], name)

    def test_metadata_declares_the_runtime_version_licence_and_commands(self):
        members = self.entries()
        metadata = Parser().parsestr(
            members[f"release_kit-{__version__}.dist-info/METADATA"].decode("utf-8")
        )
        self.assertEqual(__version__, metadata["Version"])
        self.assertEqual("release-kit", metadata["Name"])
        self.assertEqual("MIT", metadata["License-Expression"])
        commands = members[f"release_kit-{__version__}.dist-info/entry_points.txt"].decode("utf-8")
        self.assertIn("relkit = releasekit.cli:main", commands)
        self.assertIn("relkit-mcp = releasekit_mcp.server:main", commands)
        # Both importable packages ship, or the MCP entry point resolves to nothing.
        self.assertIn("releasekit/cli.py", members)
        self.assertIn("releasekit_mcp/server.py", members)

    def test_the_sidecar_names_the_wheel_and_its_digest(self):
        sidecar = self.wheel.with_name(self.wheel.name + ".sha256").read_bytes()
        digest = hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        self.assertEqual(f"{digest}  {NAME}\n".encode(), sidecar)

    def test_the_release_asset_set_expects_the_wheel_of_that_version(self):
        wanted = smoke.expected_assets(__version__)
        self.assertIn(NAME, wanted)
        self.assertIn(NAME + ".sha256", wanted)
        self.assertNotIn(NAME, smoke.expected_assets("0.0.1"))


if __name__ == "__main__":
    unittest.main()
