from __future__ import annotations

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import smoke_changelog
from smoke_onboarding import git


class ChangelogSmokeFixtureTests(unittest.TestCase):
    def test_existing_workspace_is_refused_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            source = root / "CHANGELOG.md"
            source.write_bytes(b"Historical content.\r\n")
            with self.assertRaises(FileExistsError):
                smoke_changelog.create_project(root, root / "missing.pyz")
            self.assertEqual([source], list(root.iterdir()))
            self.assertEqual(b"Historical content.\r\n", source.read_bytes())

    def test_fixture_contains_real_output_safety_controls_without_a_hosted_remote(self):
        with tempfile.TemporaryDirectory() as name:
            workspace = Path(name).resolve()
            artifact = workspace / "setup-only.pyz"
            artifact.write_bytes(b"fixture construction only; never execute")
            root = workspace / "project space"
            environment = smoke_changelog.create_project(root, artifact)
            template = tomllib.loads((root / "cliff.toml").read_text(encoding="utf-8"))
            self.assertEqual("CHANGELOG.md", template["changelog"]["output"])
            self.assertIn(
                "processor.py", template["changelog"]["postprocessors"][0]["replace_command"]
            )
            self.assertEqual("CHANGELOG.md", environment["GIT_CLIFF_PREPEND"])
            self.assertEqual("true", environment["GIT_CLIFF_OFFLINE"])
            self.assertFalse((root / "unexpected-processor-write").exists())
            self.assertEqual("", git(root, environment, "remote"))
            self.assertEqual(artifact.read_bytes(), (root / ".github/relkit.pyz").read_bytes())


if __name__ == "__main__":
    unittest.main()
