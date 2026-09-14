"""One declared version, mechanically propagated to the files that must carry it."""

import contextlib
import io
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import set_version

from releasekit import distribution

DECLARATION = (ROOT / "src/releasekit/__init__.py").read_text(encoding="utf-8")

MANIFEST = b'{\n  "name": "example",\n  "version": "0.1.0",\n  "skills": "./skills/"\n}\n'
PROJECT = b'[project]\nname = "runtime"\nversion = "0.1.0"\ndependencies = ["mcp"]\n'
LOCK = (
    b'[[package]]\nname = "mcp"\nversion = "2.1.1"\n\n'
    b'[[package]]\nname = "runtime"\nversion = "0.1.0"\nsource = { virtual = "." }\n'
)


def build(root: Path) -> Path:
    declaration = set_version.declaration(root)
    declaration.parent.mkdir(parents=True)
    declaration.write_bytes(b'"""Example."""\n\n__version__ = "0.1.0"\n')
    template = set_version.plugin_root(root)
    (template / ".codex-plugin").mkdir(parents=True)
    (template / ".codex-plugin" / "plugin.json").write_bytes(MANIFEST)
    (template / "pyproject.toml").write_bytes(PROJECT)
    (template / "uv.lock").write_bytes(LOCK)
    return root


class VersionSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = build(Path(temporary.name).resolve())

    def test_every_carrier_takes_the_declared_version(self):
        for path, pattern in set_version.carriers(self.root):
            self.assertTrue(set_version.rewrite(path, pattern, "9.9.9"), path)
        self.assertIn(b'__version__ = "9.9.9"', set_version.declaration(self.root).read_bytes())
        template = set_version.plugin_root(self.root)
        manifest = (template / ".codex-plugin" / "plugin.json").read_bytes()
        self.assertIn(b'"version": "9.9.9",', manifest)
        # Only the number moved: the surrounding document is untouched.
        self.assertIn(b'"skills": "./skills/"', manifest)
        self.assertIn(b'version = "9.9.9"', (template / "pyproject.toml").read_bytes())

    def test_an_unchanged_version_is_reported_as_not_written(self):
        for path, pattern in set_version.carriers(self.root):
            self.assertFalse(set_version.rewrite(path, pattern, "0.1.0"), path)

    def test_a_second_version_in_a_carrier_refuses_the_rewrite(self):
        manifest = set_version.plugin_root(self.root) / ".codex-plugin" / "plugin.json"
        manifest.write_bytes(MANIFEST.replace(b'"skills"', b'"version": "0.1.0",\n  "skills"'))
        with self.assertRaisesRegex(SystemExit, "exactly one version"):
            set_version.rewrite(manifest, set_version.MANIFEST, "9.9.9")

    def _locked(self, payload: bytes):
        """Stand in for uv, which is the only thing allowed to write the lock."""

        def run(arguments, **keywords):
            (set_version.plugin_root(self.root) / "uv.lock").write_bytes(payload)
            return subprocess.CompletedProcess(arguments, 0, "", "")

        return patch.object(set_version.subprocess, "run", run)

    def test_a_regenerated_lock_carries_the_runtime_version(self):
        with self._locked(LOCK.replace(b'"0.1.0"\nsource', b'"9.9.9"\nsource')):
            self.assertTrue(set_version.relock(self.root, "9.9.9"))
        lock = tomllib.loads((set_version.plugin_root(self.root) / "uv.lock").read_text())
        self.assertEqual(
            {"mcp": "2.1.1", "runtime": "9.9.9"},
            {item["name"]: item["version"] for item in lock["package"]},
        )

    def test_a_resolved_dependency_version_change_is_refused(self):
        # A version bump is not the place to discover that a dependency also moved.
        drifted = LOCK.replace(b'"0.1.0"\nsource', b'"9.9.9"\nsource').replace(b"2.1.1", b"2.2.0")
        with self._locked(drifted), self.assertRaises(SystemExit) as refusal:
            set_version.relock(self.root, "9.9.9")
        self.assertIn("mcp 2.1.1 -> 2.2.0", str(refusal.exception))

    def test_a_metadata_only_rewrite_is_accepted_and_reported(self):
        # A uv other than the one that wrote the lock rewrites markers from the same
        # inputs. Refusing that would teach the operator to bypass this tool, so it is
        # reported into the diff they review instead.
        rewritten = LOCK.replace(b'"0.1.0"\nsource', b'"9.9.9"\nsource').replace(
            b'name = "mcp"\nversion = "2.1.1"\n',
            b'name = "mcp"\nversion = "2.1.1"\nsource = { registry = "https://example.invalid" }\n',
        )
        with self._locked(rewritten), contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertTrue(set_version.relock(self.root, "9.9.9"))
        self.assertIn("review the lock diff", printed.getvalue())

    def test_the_readme_installs_the_current_version(self):
        """A quick start that pins an old release installs an old tool.

        The README stated v0.21.1 while v0.23.2 was published, so anyone following it
        got a CLI three releases behind. Nothing noticed, because nothing compared the
        two. Every release reference in the README is now the declared version.
        """
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        referenced = set(re.findall(r"(?:\bv|release_kit-)([0-9]+\.[0-9]+\.[0-9]+)", readme))
        self.assertTrue(referenced, "the README names no release to install")
        self.assertEqual({distribution.source_version(DECLARATION)}, referenced)

    def test_package_metadata_declares_no_second_version(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertNotIn("version", project)
        self.assertIn("version", project.get("dynamic", []))


if __name__ == "__main__":
    unittest.main()
