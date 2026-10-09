"""A committed release must include only its committed source and document bytes."""

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_plugin
import build_release
import build_wheel
import build_zipapp


class CommittedInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="committed inputs ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "src/releasekit"
        self.source.mkdir(parents=True)
        (self.source / "__init__.py").write_text('__version__ = "1.2.3"\n')
        (self.source / "cli.py").write_text("def main():\n    return 0\n")
        (self.source / "optional.py").write_text("VALUE = 1\n")
        self.write_project("README.md")
        plugin_root = build_plugin.TEMPLATE.relative_to(build_plugin.ROOT)
        for relative in (
            *build_plugin.DOCUMENTS,
            *(plugin_root / name for name in build_plugin.FILES),
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Fixture document\n")
        (self.root / "LICENSE").write_text("Fixture license\n")
        (self.root / "CHANGELOG.md").write_text("## [1.2.3] - 2026-01-01\n")
        (self.root / ".gitignore").write_text(".cache/\n*.local.py\n")
        for args in (
            ("init", "-q"),
            ("config", "user.name", "Example Maintainer"),
            ("config", "user.email", "maintainer@example.invalid"),
            ("config", "core.hooksPath", ".git/hooks"),
            ("config", "commit.gpgsign", "false"),
            ("add", "."),
            ("commit", "-qm", "test: source fixture"),
        ):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def write_project(self, readme):
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "release-input-fixture"\ndynamic = ["version"]\n'
            'authors = [{name = "Example", email = "maintainer@example.invalid"}]\n'
            'license = "MIT"\ndescription = "Release input fixture"\n'
            'requires-python = ">=3.11"\nscripts = {}\n'
            f"readme = {json.dumps(readme)}\n"
        )

    def wheel(self):
        with (
            patch.object(build_wheel, "ROOT", self.root),
            patch.object(build_wheel, "SOURCE", self.source),
        ):
            return build_wheel.build(self.root / ".cache/wheels")

    def packaged_names(self):
        output = self.root / ".cache/candidate.pyz"
        with (
            patch.object(build_zipapp, "ROOT", self.root),
            patch.object(build_zipapp, "SOURCE", self.source),
        ):
            build_zipapp.build(output)
        with zipfile.ZipFile(output) as archive:
            return archive.namelist()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout.strip()

    def assert_build_refuses(self, relative):
        output = self.root / ".cache/rejected-release"
        with (
            patch.object(build_release, "ROOT", self.root),
            self.assertRaisesRegex(ValueError, relative),
        ):
            build_release.build_release(output)
        self.assertFalse(output.exists(), "unsafe inputs must be rejected before creating output")

    def test_committed_metadata_link_cannot_follow_a_clean_ignored_target(self):
        metadata = self.root / "pyproject.toml"
        target = self.root / ".cache/project.toml"
        target.parent.mkdir()
        metadata.replace(target)
        try:
            metadata.symlink_to(".cache/project.toml")
        except OSError:
            self.skipTest("symlinks unavailable")
        self.git("add", "pyproject.toml")
        self.git("commit", "-qm", "test: linked release input")
        self.assertTrue(self.git("ls-files", "--stage", "pyproject.toml").startswith("120000 "))
        original_head = self.git("rev-parse", "HEAD")
        target.write_text('[project]\ndescription = "changed ignored metadata"\n')
        self.assertEqual("", self.git("status", "--porcelain"))
        self.assertEqual(original_head, self.git("rev-parse", "HEAD"))

        self.assertEqual(["pyproject.toml"], build_release.diverged(self.root))
        self.assert_build_refuses("pyproject.toml")

    def test_an_uninitialized_gitlink_cannot_qualify_as_committed_release_source(self):
        relative = "src/releasekit_mcp"
        self.git(
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.git('rev-parse', 'HEAD')},{relative}",
        )
        self.git("commit", "-qm", "test: gitlink release input")
        external_source = self.root / relative
        external_source.mkdir()

        self.assertEqual([relative], build_release.diverged(self.root))
        self.assert_build_refuses(relative)

    def test_untracked_and_ignored_modules_cannot_join_a_committed_release(self):
        self.assertEqual([], build_release.diverged(self.root))
        for name in ("untracked.py", "ignored.local.py"):
            with self.subTest(name=name):
                path = self.source / name
                path.write_text("LOCAL_ONLY = True\n")
                self.assertIn("releasekit/" + name, self.packaged_names())
                self.assertEqual(["src/releasekit/" + name], build_release.diverged(self.root))
                path.unlink()

    def test_missing_tracked_module_cannot_silently_disappear_from_release(self):
        (self.source / "optional.py").unlink()
        self.assertNotIn("releasekit/optional.py", self.packaged_names())
        self.assertEqual(["src/releasekit/optional.py"], build_release.diverged(self.root))

    def test_declared_ignored_readme_cannot_change_a_clean_commit_build(self):
        relative = ".cache/description.md"
        readme = self.root / relative
        readme.parent.mkdir()
        readme.write_text("First ignored description\n")
        self.write_project(relative)
        self.git("add", "pyproject.toml")
        self.git("commit", "-qm", "test: ignored readme input")
        head = self.git("rev-parse", "HEAD")
        first = self.wheel().read_bytes()

        readme.write_text("Second ignored description\n")
        self.assertNotEqual(first, self.wheel().read_bytes())
        self.assertEqual(head, self.git("rev-parse", "HEAD"))
        self.assertEqual("", self.git("status", "--porcelain"))
        self.assertEqual([relative], build_release.diverged(self.root))
        self.assert_build_refuses(relative)

    def test_ignored_fixed_license_cannot_change_a_clean_commit_build(self):
        self.git("rm", "--cached", "LICENSE")
        ignored = self.root / ".gitignore"
        ignored.write_text(ignored.read_text() + "LICENSE\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "test: ignored license input")
        head = self.git("rev-parse", "HEAD")
        first = self.wheel().read_bytes()

        (self.root / "LICENSE").write_text("Changed ignored license\n")
        self.assertNotEqual(first, self.wheel().read_bytes())
        self.assertEqual(head, self.git("rev-parse", "HEAD"))
        self.assertEqual("", self.git("status", "--porcelain"))
        self.assertEqual(["LICENSE"], build_release.diverged(self.root))
        self.assert_build_refuses("LICENSE")

    def test_alternate_tracked_readme_remains_a_valid_wheel_input(self):
        relative = "docs/packaging/description.md"
        readme = self.root / relative
        readme.parent.mkdir()
        readme.write_text("Description from the committed alternate readme\n")
        self.write_project(relative)
        self.git("add", "pyproject.toml", relative)
        self.git("commit", "-qm", "test: alternate tracked readme")

        self.assertEqual([], build_release.diverged(self.root))
        with zipfile.ZipFile(self.wheel()) as archive:
            metadata = archive.read("release_input_fixture-1.2.3.dist-info/METADATA")
        self.assertTrue(metadata.endswith(readme.read_bytes()))

    def test_readme_path_cannot_escape_or_alias_a_committed_file(self):
        outside = self.root.parent / (self.root.name + "-README.md")
        outside.write_text("Owned fixture outside the source checkout\n")
        self.addCleanup(outside.unlink)
        for relative in ("../" + outside.name, "docs/../README.md"):
            with self.subTest(relative=relative):
                self.write_project(relative)
                self.git("add", "pyproject.toml")
                self.git("commit", "-qm", "test: noncanonical readme input")
                self.assertIn(relative, build_release.diverged(self.root))
                self.assert_build_refuses("release inputs")

    def test_broken_checkout_cannot_claim_to_be_a_git_free_snapshot(self):
        (self.root / ".git/HEAD").write_text("broken metadata\n")
        with self.assertRaisesRegex(ValueError, "committed release inputs"):
            build_release.diverged(self.root)

    def test_git_free_snapshot_never_discovers_a_parent_checkout(self):
        snapshot = self.root / ".cache/snapshot"
        snapshot.mkdir(parents=True)
        (snapshot / "local.py").write_text("standalone source\n")
        self.assertEqual([], build_release.diverged(snapshot))


if __name__ == "__main__":
    unittest.main()
