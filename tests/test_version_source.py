"""One declared version, mechanically propagated to the files that must carry it."""

import contextlib
import hashlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import set_version

from releasekit import distribution, processes

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
            return subprocess.CompletedProcess(arguments, 0, b"", b"")

        return patch.object(set_version.processes, "run", run)

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
        path.write_bytes(b"this page forgot to say what to install\n")
        with self.assertRaisesRegex(SystemExit, "names no release"):
            set_version.retarget_readme(path, "9.9.9")

    def test_package_metadata_declares_no_second_version(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertNotIn("version", project)
        self.assertIn("version", project.get("dynamic", []))

    def transaction_files(self):
        return [
            *(path for path, _ in set_version.carriers(self.root)),
            *set_version.readmes(self.root),
            set_version.plugin_root(self.root) / "uv.lock",
        ]

    def invoke_main(self, *, uv="uv"):
        with (
            patch.object(set_version, "ROOT", self.root),
            patch.object(set_version.shutil, "which", return_value=uv),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return set_version.main(["9.9.9"])

    def test_refused_version_bump_restores_every_owned_file_exactly(self):
        (self.root / "README.md").write_bytes(b"Install /v0.1.0/release_kit-0.1.0.whl\r\n")
        (self.root / "README.ru.md").write_bytes(b"/v0.1.0/\r\n")
        before = {path: path.read_bytes() for path in self.transaction_files()}
        drifted = LOCK.replace(b'"0.1.0"\nsource', b'"9.9.9"\nsource').replace(b"2.1.1", b"2.2.0")
        with self._locked(drifted), self.assertRaisesRegex(SystemExit, "mcp 2.1.1 -> 2.2.0"):
            self.invoke_main()
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual([], self.recoveries())

    def test_invalid_late_carrier_does_not_leave_earlier_carriers_bumped(self):
        manifest = set_version.plugin_root(self.root) / ".codex-plugin/plugin.json"
        manifest.write_bytes(MANIFEST.replace(b'"skills"', b'"version": "0.1.0",\n  "skills"'))
        before = {path: path.read_bytes() for path in self.transaction_files()}
        with self.assertRaisesRegex(SystemExit, "exactly one version"):
            self.invoke_main()
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_interrupted_lock_rewrite_restores_the_previous_versions(self):
        before = {path: path.read_bytes() for path in self.transaction_files()}

        def interrupted(*args, **kwargs):
            (set_version.plugin_root(self.root) / "uv.lock").write_bytes(b"incomplete lock")
            raise subprocess.TimeoutExpired(args[0], set_version.LOCK_TIMEOUT)

        with (
            patch.object(set_version.processes, "run", side_effect=interrupted),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            self.invoke_main()
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual([], self.recoveries())

    def test_successful_version_bump_commits_the_validated_carriers(self):
        (self.root / "README.md").write_bytes(b"Install /v0.1.0/release_kit-0.1.0.whl\r\n")
        with self._locked(LOCK.replace(b'"0.1.0"\nsource', b'"9.9.9"\nsource')):
            self.assertEqual(0, self.invoke_main())
        self.assertEqual(
            "9.9.9", distribution.source_version(set_version.declaration(self.root).read_text())
        )
        self.assertEqual(
            b"Install /v9.9.9/release_kit-9.9.9.whl\r\n", (self.root / "README.md").read_bytes()
        )
        self.assertEqual([], self.recoveries())

    def recoveries(self):
        return list((self.root / ".cache").glob("version-recovery-*"))

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

    def test_unconfirmed_resolver_cleanup_preserves_before_bytes_without_rollback(self):
        before = {path: path.read_bytes() for path in self.transaction_files()}
        lock = set_version.plugin_root(self.root) / "uv.lock"

        def unconfirmed(*args, **kwargs):
            lock.write_bytes(b"resolver may still be writing")
            raise processes.CleanupError("worker teardown unconfirmed")

        with (
            patch.object(set_version.processes, "run", side_effect=unconfirmed),
            self.assertRaisesRegex(SystemExit, "process cleanup is unconfirmed") as refused,
        ):
            self.invoke_main()
        saved = self.assert_recovery(before)
        self.assertIn(str(saved), str(refused.exception))
        self.assertEqual(b"resolver may still be writing", lock.read_bytes())
        self.assertIn(b'__version__ = "9.9.9"', set_version.declaration(self.root).read_bytes())

    def test_failed_restore_retains_original_bytes_for_every_owned_file(self):
        before = {path: path.read_bytes() for path in self.transaction_files()}
        blocked = set_version.declaration(self.root)
        write = Path.write_bytes

        def refuse_restore(path, payload):
            if path == blocked and payload == before[blocked]:
                raise PermissionError("controlled restore refusal")
            return write(path, payload)

        with (
            patch.object(Path, "write_bytes", refuse_restore),
            patch.object(set_version, "relock", side_effect=SystemExit("controlled lock failure")),
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
        with (
            patch.object(set_version.processes, "run") as resolver,
            self.assertRaisesRegex(SystemExit, "before changing versions"),
        ):
            self.invoke_main()
        resolver.assert_not_called()
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual([other / "keep"], list(other.iterdir()))

    def test_unknown_recovery_data_is_retained_after_a_successful_bump(self):
        before = {path: path.read_bytes() for path in self.transaction_files()}

        def rewritten(arguments, **kwargs):
            saved = self.recoveries()[0]
            (saved / "operator-note").write_bytes(b"preserve unknown data")
            (set_version.plugin_root(self.root) / "uv.lock").write_bytes(
                LOCK.replace(b'"0.1.0"\nsource', b'"9.9.9"\nsource')
            )
            return subprocess.CompletedProcess(arguments, 0, b"", b"")

        with (
            patch.object(set_version.processes, "run", side_effect=rewritten),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(0, self.invoke_main())
        saved = self.assert_recovery(before)
        self.assertEqual(b"preserve unknown data", (saved / "operator-note").read_bytes())
        self.assertIn(str(saved), errors.getvalue())

    def test_timed_out_resolver_worker_cannot_overwrite_a_confirmed_rollback(self):
        before = {path: path.read_bytes() for path in self.transaction_files()}
        ready, trigger, late = (self.root / name for name in ("ready", "trigger", "late"))
        lock = set_version.plugin_root(self.root) / "uv.lock"
        child = (
            "from pathlib import Path\nimport os,time\n"
            f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
            "deadline = time.monotonic() + 20\n"
            f"while not Path({str(trigger)!r}).exists() and time.monotonic() < deadline:\n"
            " time.sleep(0.01)\n"
            f"if Path({str(trigger)!r}).exists():\n"
            f" Path({str(lock)!r}).write_text('late resolver write')\n"
            f" Path({str(late)!r}).touch()\n"
        )
        # Resolving the executable to Python makes its `lock` argument name this
        # controlled script on every platform, without a shell or installed fake uv.
        script = set_version.plugin_root(self.root) / "lock"
        script.write_text(
            "import subprocess,sys\nfrom pathlib import Path\n"
            "Path('uv.lock').write_text('incomplete resolver output')\n"
            f"child = subprocess.Popen([sys.executable,'-B','-c',{child!r}],"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nchild.wait()\n",
            encoding="utf-8",
        )
        try:
            with (
                patch.object(set_version, "LOCK_TIMEOUT", 2),
                self.assertRaises(subprocess.TimeoutExpired),
            ):
                self.invoke_main(uv=sys.executable)
            self.assertTrue(ready.is_file(), "the resolver worker must start for this probe")
            self.assertEqual(before, {path: path.read_bytes() for path in before})
            trigger.touch()
            time.sleep(0.3)
            self.assertFalse(late.exists(), "a resolver worker survived confirmed rollback")
            self.assertEqual(before, {path: path.read_bytes() for path in before})
            self.assertEqual([], self.recoveries())
        finally:
            if ready.is_file():
                try:
                    os.kill(int(ready.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def test_failed_resolver_keeps_text_diagnostics_and_restores_the_previous_bytes(self):
        before = {path: path.read_bytes() for path in self.transaction_files()}
        detail = "Ошибка проверки зависимостей."
        script = set_version.plugin_root(self.root) / "lock"
        script.write_text(
            "import sys\n"
            f"sys.stderr.buffer.write({(detail + chr(10)).encode('utf-8')!r})\n"
            "raise SystemExit(7)\n",
            encoding="utf-8",
        )
        with self.assertRaises(SystemExit) as refused:
            self.invoke_main(uv=sys.executable)
        self.assertEqual("set-version: uv lock failed\n" + detail, str(refused.exception))
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual([], self.recoveries())


if __name__ == "__main__":
    unittest.main()
