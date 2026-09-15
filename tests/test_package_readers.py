"""The installed package keeps user journeys; maintainer pages stay in source."""

import runpy
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from releasekit import __version__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


class PackageReaderTests(unittest.TestCase):
    def test_installed_package_keeps_user_docs_and_exact_starter_files(self):
        build = runpy.run_path(str(ROOT / "tools/build_plugin.py"))["build_plugin"]
        with tempfile.TemporaryDirectory(prefix="package readers ") as directory:
            output = Path(directory) / "plugin.zip"
            build(output)
            with zipfile.ZipFile(output) as archive:
                for name in (
                    "README.md",
                    "LICENSE",
                    "SECURITY.md",
                    "docs/audit.md",
                    "docs/updates.md",
                    "docs/notes.md",
                    "docs/mcp.md",
                    "docs/cli-json.md",
                    "docs/plugin.md",
                ):
                    with self.subTest(name=name):
                        self.assertIn("release-kit/" + name, archive.namelist())
                for name in (
                    "examples/audit/README.md",
                    "examples/audit/relkit.toml",
                    "examples/audit/.betterleaks.toml",
                    "examples/audit/.gitignore",
                    "examples/plugin-marketplace.json",
                ):
                    self.assertEqual(
                        (ROOT / name).read_bytes(), archive.read("release-kit/" + name)
                    )
                for name in (
                    "CONTRIBUTING.md",
                    "docs/distribution.md",
                    "docs/publication-review.md",
                ):
                    self.assertNotIn("release-kit/" + name, archive.namelist())
                    self.assertTrue((ROOT / name).is_file(), "do not remove source documentation")
                prefix = f"https://github.com/Muratovnik/release-kit/blob/v{__version__}/"
                readme = archive.read("release-kit/README.md").decode()
                self.assertIn(prefix + "CONTRIBUTING.md", readme)
                for name in (
                    "README.ru.md",
                    "README.zh-CN.md",
                    "docs/ru/README.md",
                    "docs/ru/audit.md",
                    "docs/ru/updates.md",
                    "docs/zh-CN/README.md",
                    "docs/zh-CN/audit.md",
                    "docs/zh-CN/updates.md",
                ):
                    # The packaged README offers these languages without packaging them,
                    # so each offer has to point at the release rather than at a
                    # neighbouring file the archive does not contain.
                    with self.subTest(translation=name):
                        self.assertNotIn("release-kit/" + name, archive.namelist())
                        self.assertIn(prefix + name, readme)
                plugin = archive.read("release-kit/docs/plugin.md").decode()
                self.assertIn(prefix + "docs/distribution.md#integrity-and-trust", plugin)
                self.assertIn(prefix + "CONTRIBUTING.md#checks", plugin)
                self.assertFalse(any("/.runtime/" in name for name in archive.namelist()))

    def test_document_projection_preserves_text_user_links_and_non_markdown(self):
        project = runpy.run_path(str(ROOT / "tools/build_plugin.py"))["document_bytes"]
        source = b"Text [user](audit.md) [dev](../CONTRIBUTING.md#checks)\n"
        expected = (
            b"Text [user](audit.md) [dev](https://github.com/Muratovnik/release-kit/"
            b"blob/v1.2.3/CONTRIBUTING.md#checks)\n"
        )
        self.assertEqual(expected, project("docs/plugin.md", source, "1.2.3"))
        self.assertEqual(source, project("examples/config.json", source, "1.2.3"))
