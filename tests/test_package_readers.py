"""The built plugin must carry the reader journeys and exact starter files."""

import runpy
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


class PackageReaderTests(unittest.TestCase):
    def test_installed_package_preserves_source_docs_and_examples(self):
        build = runpy.run_path(str(ROOT / "tools/build_plugin.py"))["build_plugin"]
        required = (
            "README.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "docs/audit.md",
            "docs/updates.md",
            "docs/notes.md",
            "docs/distribution.md",
            "docs/publication-review.md",
            "docs/mcp.md",
            "docs/cli-json.md",
            "docs/plugin.md",
            "examples/audit/README.md",
            "examples/audit/relkit.toml",
            "examples/audit/.betterleaks.toml",
            "examples/audit/.gitignore",
            "examples/plugin-marketplace.json",
        )
        with tempfile.TemporaryDirectory(prefix="package readers ") as directory:
            output = Path(directory) / "plugin.zip"
            build(output)
            with zipfile.ZipFile(output) as archive:
                for name in required:
                    with self.subTest(name=name):
                        self.assertEqual(
                            (ROOT / name).read_bytes(), archive.read("release-kit/" + name)
                        )
                self.assertFalse(any("/.runtime/" in name for name in archive.namelist()))
