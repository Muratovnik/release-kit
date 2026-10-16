"""One declared version, mechanically propagated to the files that must carry it."""

import contextlib
import hashlib
import io
import json
import re
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

SOURCE = b'"""Example."""\n\n__version__ = "0.1.0"\n'
UNREADABLE = b"this page forgot to say what to install\n"


def build(root: Path) -> Path:
    declaration = set_version.declaration(root)
    declaration.parent.mkdir(parents=True)
    declaration.write_bytes(SOURCE)
    return root


class VersionSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = build(Path(temporary.name).resolve())

    def test_the_declaration_takes_the_declared_version(self):
        for path, pattern in set_version.carriers(self.root):
            self.assertTrue(set_version.rewrite(path, pattern, "9.9.9"), path)
        # Only the number moved: the surrounding document is untouched.
        self.assertEqual(
            SOURCE.replace(b"0.1.0", b"9.9.9"), set_version.declaration(self.root).read_bytes()
        )

    def test_an_unchanged_version_is_reported_as_not_written(self):
        for path, pattern in set_version.carriers(self.root):
            self.assertFalse(set_version.rewrite(path, pattern, "0.1.0"), path)

    def test_a_second_version_in_a_carrier_refuses_the_rewrite(self):
        declaration = set_version.declaration(self.root)
        declaration.write_bytes(SOURCE + b'__version__ = "0.1.0"\n')
        with self.assertRaisesRegex(SystemExit, "exactly one version"):
            set_version.rewrite(declaration, set_version.DECLARED, "9.9.9")

    def test_every_readme_installs_the_current_version(self):
        """A quick start that pins an old release installs an old tool.

        The README stated v0.21.1 while v0.23.2 was published, so anyone following it
        got a CLI three releases behind. Nothing noticed, because nothing compared the
        two. A translated quick start installs the same way, so this holds every README
        to the declared version rather than only the English page.
        """
        declared = distribution.source_version(DECLARATION)
        for path in set_version.readmes(ROOT):
            with self.subTest(readme=path.name):
                text = path.read_text(encoding="utf-8")
                found = set(re.findall(r"(?:\bv|release_kit-)([0-9]+\.[0-9]+\.[0-9]+)", text))
                self.assertTrue(found, "this README names no release to install")
                self.assertEqual({declared}, found)

    def test_a_translation_is_retargeted_with_the_page_it_translates(self):
        # A translation that keeps the previous release installs an old tool just as
        # effectively as the English page did, and nobody reads it in review.
        pinned = b"uv tool install .../v0.1.0/release_kit-0.1.0-py3-none-any.whl\n"
        for name in ("README.md", "README.ru.md", "README.zh-CN.md"):
            (self.root / name).write_bytes(pinned)
        written = [
            path.name
            for path in set_version.readmes(self.root)
            if set_version.retarget_readme(path, "9.9.9")
        ]
        self.assertEqual(["README.md", "README.ru.md", "README.zh-CN.md"], sorted(written))
        for name in written:
            retargeted = (self.root / name).read_bytes()
            self.assertIn(b"/v9.9.9/release_kit-9.9.9-py3-none-any.whl", retargeted)

    def test_a_canonical_readme_naming_no_release_refuses(self):
        path = self.root / "README.md"
        path.write_bytes(UNREADABLE)
        with self.assertRaisesRegex(SystemExit, "names no release"):
            set_version.retarget_readme(path, "9.9.9")

    def test_package_metadata_declares_no_second_version(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertNotIn("version", project)
        self.assertIn("version", project.get("dynamic", []))

    def test_the_package_declares_no_mcp_extra_or_command(self):
        # The adapter was removed in 0.33.0; a leftover entry point resolves to nothing.
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual({"relkit"}, set(project["scripts"]))
        self.assertNotIn("mcp", project.get("optional-dependencies", {}))

    def transaction_files(self):
        return [
            *(path for path, _ in set_version.carriers(self.root)),
            *set_version.readmes(self.root),
        ]

    def invoke_main(self):
        with (
            patch.object(set_version, "ROOT", self.root),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return set_version.main(["9.9.9"])

    def recoveries(self):
        return list((self.root / ".cache").glob("version-recovery-*"))

    def test_refused_version_bump_restores_every_owned_file_exactly(self):
        # The declaration is written first; a README that names no release then refuses
        # the transaction, which must give the declaration back byte for byte.
        (self.root / "README.md").write_bytes(UNREADABLE.replace(b"\n", b"\r\n"))
        (self.root / "README.ru.md").write_bytes(b"/v0.1.0/\r\n")
        before = {path: path.read_bytes() for path in self.transaction_files()}
        with self.assertRaisesRegex(SystemExit, "names no release"):
            self.invoke_main()
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual([], self.recoveries())

    def test_a_second_version_in_the_declaration_changes_nothing(self):
        declaration = set_version.declaration(self.root)
        declaration.write_bytes(SOURCE + b'__version__ = "0.1.0"\n')
        (self.root / "README.md").write_bytes(b"/v0.1.0/\n")
        before = {path: path.read_bytes() for path in self.transaction_files()}
        with self.assertRaisesRegex(SystemExit, "exactly one version"):
            self.invoke_main()
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_successful_version_bump_commits_the_validated_carriers(self):
        (self.root / "README.md").write_bytes(b"Install /v0.1.0/release_kit-0.1.0.whl\r\n")
        self.assertEqual(0, self.invoke_main())
        self.assertEqual(
            "9.9.9", distribution.source_version(set_version.declaration(self.root).read_text())
        )
        self.assertEqual(
            b"Install /v9.9.9/release_kit-9.9.9.whl\r\n", (self.root / "README.md").read_bytes()
        )
        self.assertEqual([], self.recoveries())

    def assert_recovery(self, before):
        saved = self.recoveries()
        self.assertEqual(1, len(saved), "one invocation must retain one recoverable snapshot")
        receipt = json.loads((saved[0] / "recovery.json").read_text(encoding="utf-8"))
        self.assertEqual("set_version", receipt["tool"])
        self.assertEqual(str(self.root), receipt["root"])
        expected = {
            path.relative_to(self.root).as_posix(): payload for path, payload in before.items()
        }
        self.assertEqual(set(expected), set(receipt["files"]))
        for name, payload in expected.items():
            entry = receipt["files"][name]
            self.assertEqual(hashlib.sha256(payload).hexdigest(), entry["sha256"])
            self.assertEqual(payload, (saved[0] / entry["backup"]).read_bytes())
        return saved[0]

    def test_failed_restore_retains_original_bytes_for_every_owned_file(self):
        (self.root / "README.md").write_bytes(UNREADABLE)
        before = {path: path.read_bytes() for path in self.transaction_files()}
        blocked = set_version.declaration(self.root)
        write = Path.write_bytes

        def refuse_restore(path, payload):
            if path == blocked and payload == before[blocked]:
                raise PermissionError("controlled restore refusal")
            return write(path, payload)

        with (
            patch.object(Path, "write_bytes", refuse_restore),
            self.assertRaisesRegex(SystemExit, "could not be restored") as refused,
        ):
            self.invoke_main()
        saved = self.assert_recovery(before)
        self.assertIn(str(saved), str(refused.exception))
        self.assertIn("src/releasekit/__init__.py", str(refused.exception))
        for path, payload in before.items():
            if path != blocked:
                self.assertEqual(payload, path.read_bytes())

    def test_recovery_cache_alias_is_refused_before_any_version_changes(self):
        other = self.root / "other-data"
        other.mkdir()
        (other / "keep").write_bytes(b"keep")
        try:
            (self.root / ".cache").symlink_to(other, target_is_directory=True)
        except OSError:
            self.skipTest("creating directory symlinks is not permitted on this host")
        before = {path: path.read_bytes() for path in self.transaction_files()}
        with self.assertRaisesRegex(SystemExit, "before changing versions"):
            self.invoke_main()
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual([other / "keep"], list(other.iterdir()))

    def test_unknown_recovery_data_is_retained_after_a_successful_bump(self):
        before = {path: path.read_bytes() for path in self.transaction_files()}
        original = set_version.preserve

        def preserved(root, files, version):
            recovery = original(root, files, version)
            (recovery.path / "operator-note").write_bytes(b"preserve unknown data")
            return recovery

        with (
            patch.object(set_version, "preserve", preserved),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(0, self.invoke_main())
        saved = self.assert_recovery(before)
        self.assertEqual(b"preserve unknown data", (saved / "operator-note").read_bytes())
        self.assertIn(str(saved), errors.getvalue())


if __name__ == "__main__":
    unittest.main()
