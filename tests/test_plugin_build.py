"""Distribution checks without importing the optional MCP SDK."""

import hashlib
import io
import json
import os
import posixpath
import runpy
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path, PureWindowsPath
from threading import Event
from unittest.mock import Mock, patch

from releasekit import __version__, distribution, storage
from releasekit.plugin import Bundle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
BUILDER = runpy.run_path(str(ROOT / "tools/build_plugin.py"))


class PluginBuildTests(unittest.TestCase):
    def test_packaged_notes_include_the_changelog_starter_and_its_relative_links(self):
        with tempfile.TemporaryDirectory(prefix="packaged notes ") as temporary:
            output = Path(temporary) / "plugin.zip"
            BUILDER["build_plugin"](output)
            with zipfile.ZipFile(output) as archive:
                inventory = json.loads(archive.read("release-kit/package.json"))["files"]
                for name in ("examples/changelog/README.md", "examples/changelog/cliff.toml"):
                    self.assertIn(name, inventory)
                    payload = archive.read("release-kit/" + name)
                    self.assertEqual(hashlib.sha256(payload).hexdigest(), inventory[name])
                    self.assertEqual((ROOT / name).read_bytes(), payload)
                for document, label in (
                    ("docs/notes.md", "examples/changelog"),
                    ("examples/changelog/README.md", "heading aliases"),
                    ("examples/changelog/README.md", "custom generators"),
                ):
                    text = archive.read("release-kit/" + document).decode("utf-8")
                    _, marker, tail = text.partition(f"[{label}](")
                    self.assertTrue(marker, f"{document} must link to {label}")
                    target = tail.partition(")")[0].split("#", 1)[0]
                    self.assertFalse(target.startswith(("https://", "http://", "/")))
                    resolved = posixpath.normpath(
                        posixpath.join(posixpath.dirname(document), target)
                    )
                    self.assertIn(
                        resolved, inventory, f"{document} has an unresolved link: {target}"
                    )
                    self.assertIn("release-kit/" + resolved, archive.namelist())

    def test_windows_runtime_paths_support_long_drive_and_unc_locations(self):
        launcher = runpy.run_path(str(ROOT / "plugins/release-kit/scripts/launch.py"))
        with patch.object(sys, "platform", "win32"):
            for value, expected in (
                ("C:\\example project\\cache", "\\\\?\\C:\\example project\\cache"),
                ("\\\\server\\share\\cache", "\\\\?\\UNC\\server\\share\\cache"),
                ("\\\\?\\C:\\example\\cache", "\\\\?\\C:\\example\\cache"),
            ):
                self.assertEqual(expected, launcher["process_path"](value))
        with patch.object(sys, "platform", "linux"):
            self.assertEqual("/example/cache", launcher["process_path"]("/example/cache"))

    def test_windows_plugin_smoke_stays_within_loader_budget_without_an_alias(self):
        smoke = runpy.run_path(str(ROOT / "tools/smoke_plugin.py"))
        launcher = runpy.run_path(str(ROOT / "plugins/release-kit/scripts/launch.py"))
        namespace = launcher["loadable_base"].__globals__
        directory = PureWindowsPath(r"C:\owned work\runs\run-abcdefgh\plugin space")
        with patch.object(sys, "platform", "win32"):
            for parent in (directory, directory / ("review " + "\U0001f680" * 12)):
                with self.subTest(parent=str(parent)):
                    destination = smoke["extraction_destination"](parent)
                    package = destination / "release-kit"
                    virtualenv = package / ".runtime" / "venv"
                    self.assertEqual(parent, destination.parent)
                    self.assertIn(" ", str(package))
                    self.assertIn("\U0001f680", destination.name)
                    self.assertGreater(len(str(package).encode("utf-16-le")) // 2, 150)
                    self.assertLessEqual(
                        len(str(virtualenv).encode("utf-16-le")) // 2 + launcher["RUNTIME_LEAF"],
                        launcher["LOADER_LIMIT"],
                    )
                    unavailable = Mock(
                        side_effect=AssertionError("ordinary smoke must not need an alias")
                    )
                    with patch.dict(namespace, {"alias_path": unavailable}):
                        self.assertEqual(package, launcher["loadable_base"](package, virtualenv))
                    unavailable.assert_not_called()

            # An already long caller-owned parent stays owned and may still refuse.
            long_parent = directory / ("already long " * 15).strip()
            destination = smoke["extraction_destination"](long_parent)
            self.assertEqual(long_parent, destination.parent)
            package = destination / "release-kit"
            with (
                patch.dict(namespace, {"alias_path": lambda path: None}),
                self.assertRaisesRegex(ValueError, "too long for the Windows DLL loader"),
            ):
                launcher["loadable_base"](package, package / ".runtime" / "venv")
        with patch.object(sys, "platform", "linux"):
            self.assertEqual(directory, smoke["extraction_destination"](directory))

    def test_windows_loader_counts_utf16_units_for_original_and_alias_paths(self):
        launcher = runpy.run_path(str(ROOT / "plugins/release-kit/scripts/launch.py"))
        namespace = launcher["loadable_base"].__globals__
        parent = PureWindowsPath(r"C:\owned work")
        astral_parent = parent / ("p" + "\U0001f642" * 90)
        astral = astral_parent / "release-kit"
        ascii_same_units = parent / ("p" + "x" * 180) / "release-kit"
        bmp = parent / ("p" + "é" * 90) / "release-kit"
        shorter_unicode = parent / ("p" + "\U0001f642" * 20) / "release-kit"
        opaque_surrogate = parent / "opaque-\ud800" / "release-kit"
        deep = astral_parent / ("long" * 40) / "release-kit"
        unsafe_alias = astral_parent / "RUNTIM~1" / "RELEAS~1"
        short_alias = PureWindowsPath(r"C:\OWNED~1\RELEAS~1")
        virtualenv = astral / ".runtime" / "venv"
        self.assertLess(len(str(virtualenv)) + launcher["RUNTIME_LEAF"], launcher["LOADER_LIMIT"])
        self.assertGreater(
            len(str(virtualenv).encode("utf-16-le")) // 2 + launcher["RUNTIME_LEAF"],
            launcher["LOADER_LIMIT"],
        )
        cases = (
            ("astral path without alias", astral, None, None, 1),
            ("astral path with short alias", astral, short_alias, short_alias, 1),
            ("overlong astral alias", deep, unsafe_alias, None, 1),
            ("deep path with short alias", deep, short_alias, short_alias, 1),
            ("ASCII of the same UTF-16 length", ascii_same_units, None, None, 1),
            ("BMP within the budget", bmp, None, bmp, 0),
            ("non-BMP within the budget", shorter_unicode, None, shorter_unicode, 0),
            ("opaque WCHAR within the budget", opaque_surrogate, None, opaque_surrogate, 0),
        )
        with patch.object(sys, "platform", "win32"):
            for label, package, alias, expected, calls in cases:
                lookup = Mock(return_value=alias)
                with self.subTest(label=label), patch.dict(namespace, {"alias_path": lookup}):
                    if expected is None:
                        with self.assertRaisesRegex(
                            ValueError, "too long for the Windows DLL loader"
                        ):
                            launcher["loadable_base"](package, package / ".runtime" / "venv")
                    else:
                        self.assertEqual(
                            expected,
                            launcher["loadable_base"](package, package / ".runtime" / "venv"),
                        )
                    self.assertEqual(calls, lookup.call_count)
        forbidden = Mock(side_effect=AssertionError("POSIX must not request Windows aliases"))
        with (
            patch.object(sys, "platform", "linux"),
            patch.dict(namespace, {"alias_path": forbidden}),
        ):
            self.assertEqual(deep, launcher["loadable_base"](deep, deep / ".runtime" / "venv"))
        forbidden.assert_not_called()

    def test_deep_windows_runtime_uses_a_short_alias_for_compiled_imports(self):
        # A real installation hit this: uv installed the packages and the server
        # then failed with "DLL load failed while importing _cffi_backend".
        launcher = runpy.run_path(str(ROOT / "plugins/release-kit/scripts/launch.py"))
        namespace = launcher["loadable_base"].__globals__
        root = Path(tempfile.gettempdir()) / ("deep plugin " * 12).strip() / "release-kit"
        virtualenv = root / ".runtime" / "venv"
        alias = Path(tempfile.gettempdir()) / "DEEPPL~1" / "RELEAS~1"
        limit = launcher["LOADER_LIMIT"] - launcher["RUNTIME_LEAF"]
        self.assertGreater(launcher["utf16_units"](virtualenv), limit)
        with patch.object(sys, "platform", "win32"):
            with patch.dict(namespace, {"alias_path": lambda path: alias}):
                base = launcher["loadable_base"](root, virtualenv)
                self.assertEqual(alias, base)
                relocated = launcher["relocate"](base, root, virtualenv)
                self.assertLessEqual(launcher["utf16_units"](relocated), limit)
                self.assertEqual(virtualenv.relative_to(root), relocated.relative_to(base))
            # An alias the volume cannot supply must refuse before installing anything.
            for unavailable in (lambda path: None, lambda path: root):
                with (
                    patch.dict(namespace, {"alias_path": unavailable}),
                    self.assertRaisesRegex(ValueError, "too long for the Windows DLL loader"),
                ):
                    launcher["loadable_base"](root, virtualenv)
            # A path the loader already accepts keeps the installed location verbatim.
            shallow = Path(tempfile.gettempdir()) / "release-kit"
            self.assertEqual(shallow, launcher["loadable_base"](shallow, shallow / ".runtime/venv"))

    @unittest.skipUnless(sys.platform == "win32", "8.3 aliases are a Windows volume feature")
    def test_windows_alias_resolves_to_the_same_installed_directory(self):
        launcher = runpy.run_path(str(ROOT / "plugins/release-kit/scripts/launch.py"))
        with tempfile.TemporaryDirectory(prefix="alias probe ") as temporary:
            nested = Path(temporary) / "plugin space" / "release-kit"
            # Exercise the actual loader boundary even when this volume supplies
            # no short names and GetShortPathNameW returns the original spelling.
            while (
                launcher["utf16_units"](nested / ".runtime" / "venv") + launcher["RUNTIME_LEAF"]
                <= launcher["LOADER_LIMIT"]
            ):
                nested /= "nested plugin directory"
            nested.mkdir(parents=True)
            virtualenv = nested / ".runtime" / "venv"
            alias = launcher["alias_path"](virtualenv)
            if alias is not None:
                self.assertEqual(nested.resolve(), alias.parent.parent.resolve())
            if (
                alias is not None
                and launcher["utf16_units"](alias) + launcher["RUNTIME_LEAF"]
                <= launcher["LOADER_LIMIT"]
            ):
                base = launcher["loadable_base"](nested, virtualenv)
                self.assertEqual(nested.resolve(), base.resolve())
                relocated = launcher["relocate"](base, nested, virtualenv)
                self.assertLessEqual(
                    launcher["utf16_units"](relocated) + launcher["RUNTIME_LEAF"],
                    launcher["LOADER_LIMIT"],
                )
                print("Windows runtime path: native short alias fits the DLL loader budget")
            else:
                with self.assertRaisesRegex(ValueError, "too long for the Windows DLL loader"):
                    launcher["loadable_base"](nested, virtualenv)
                print("Windows runtime path: no usable native short alias; deep runtime refused")

    def test_concurrent_cold_launch_waits_for_runtime_receipt(self):
        with tempfile.TemporaryDirectory(prefix="cold plugin ") as temporary:
            folder = Path(temporary)
            output = folder / "plugin.zip"
            BUILDER["build_plugin"](output)
            with zipfile.ZipFile(output) as archive:
                archive.extractall(folder)
            package = folder / "release-kit"
            launcher = runpy.run_path(str(package / "scripts/launch.py"))
            writing, release, entered = Event(), Event(), Event()
            atomic_json = storage.atomic_json

            def paused_receipt(path, value):
                writing.set()
                if not release.wait(10):
                    raise RuntimeError("test did not release receipt writer")
                atomic_json(path, value)

            def second_launch():
                entered.set()
                return launcher["main"]([])

            with (
                patch.object(sys, "path", list(sys.path)),
                patch("shutil.which", return_value="uv"),
                patch("subprocess.call", return_value=0) as invoke,
                patch.object(storage, "atomic_json", side_effect=paused_receipt),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                first = pool.submit(launcher["main"], [])
                try:
                    self.assertTrue(writing.wait(10))
                    second = pool.submit(second_launch)
                    self.assertTrue(entered.wait(10))
                    # A contender must wait while ownership is being published.
                    with self.assertRaises(TimeoutError):
                        second.result(timeout=0.5)
                finally:
                    release.set()
                self.assertEqual(0, first.result(timeout=10))
                self.assertEqual(0, second.result(timeout=10))
                self.assertEqual(4, invoke.call_count)
            receipt = json.loads((package / ".runtime/owner.json").read_text())
            self.assertEqual(storage.digest(package / "uv.lock"), receipt["lock_sha256"])

    def test_distributions_carry_the_license_and_human_package_metadata(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertTrue(project.get("authors"), "the declared human maintainer must be identified")
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "plugin.zip"
            BUILDER["build_plugin"](output)
            with zipfile.ZipFile(output) as plugin:
                self.assertEqual(license_text, plugin.read("release-kit/LICENSE").decode())
                payload = plugin.read("release-kit/tools/relkit.pyz")
            with zipfile.ZipFile(io.BytesIO(payload)) as cli:
                metadata = json.loads(cli.read(distribution.BUILD_INFO))
                self.assertEqual(license_text, metadata["license"]["text"])
                self.assertEqual("MIT", metadata["license"]["expression"])
                self.assertEqual(project["authors"], metadata["authors"])
            self.assertEqual(__version__, distribution.inspect(payload).version)

    def test_joint_release_exports_exact_bundled_cli_and_refuses_overwrite(self):
        builder = runpy.run_path(str(ROOT / "tools/build_release.py"))["build_release"]
        with tempfile.TemporaryDirectory(prefix="release set ") as temporary:
            folder = Path(temporary)
            one, two = folder / "one", folder / "two"
            # Deliberately from the worktree: this checks the builder, not the tree.
            self.assertEqual(builder(one, allow_divergent=True), builder(two, allow_divergent=True))
            self.assertEqual(__version__, json.loads((one / "release.json").read_text())["version"])
            with zipfile.ZipFile(one / "release-kit-plugin.zip") as archive:
                self.assertEqual(
                    (one / "relkit.pyz").read_bytes(), archive.read("release-kit/tools/relkit.pyz")
                )
                archive.extractall(folder)
            bundle = Bundle(folder / "release-kit")
            self.assertEqual(
                "aligned",
                bundle.alignment(distribution.inspect((one / "relkit.pyz").read_bytes()))["state"],
            )
            with self.assertRaisesRegex(ValueError, "new or empty"):
                builder(one, allow_divergent=True)
            self.assertEqual((one / "relkit.pyz").read_bytes(), (two / "relkit.pyz").read_bytes())
            bundle.path.write_bytes(bundle.path.read_bytes() + b"drift")
            with self.assertRaisesRegex(ValueError, "source changed"):
                bundle.check()

    def test_runtime_is_owned_local_and_checks_cache_paths_before_invocation(self):
        with tempfile.TemporaryDirectory(prefix="plugin space ") as temporary:
            folder = Path(temporary)
            output = folder / "plugin.zip"
            BUILDER["build_plugin"](output)
            with zipfile.ZipFile(output) as archive:
                archive.extractall(folder)
            package = folder / "release-kit"
            launcher = runpy.run_path(str(package / "scripts/launch.py"))
            runtime = package / ".runtime"
            with (
                patch.object(sys, "path", list(sys.path)),
                patch("shutil.which", return_value="uv"),
                patch("subprocess.call", return_value=0) as invoke,
                patch.dict(os.environ, {"UV_PROJECT_ENVIRONMENT": "unowned", "UV_OFFLINE": "1"}),
                patch.object(storage, "inside", wraps=storage.inside) as checked,
            ):
                self.assertEqual(0, launcher["main"]([]))
                environment = invoke.call_args.kwargs["env"]
                prefix = "\\\\?\\" if sys.platform == "win32" else ""
                self.assertEqual(
                    prefix + str(runtime / "venv"), environment["UV_PROJECT_ENVIRONMENT"]
                )
                self.assertEqual(prefix + str(runtime / "cache"), environment["UV_CACHE_DIR"])
                self.assertEqual(prefix + str(runtime / "tmp"), environment["TEMP"])
                self.assertNotIn("UV_OFFLINE", environment)
                for leaf in ("tmp", "cache", "venv"):
                    self.assertIn(((package, runtime / leaf),), checked.call_args_list)
                self.assertEqual(0, launcher["main"]([]))
                owner = (runtime / "owner.json").read_bytes()
                (runtime / "owner.json").write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "ownership/version"):
                    launcher["main"]([])
                self.assertEqual(4, invoke.call_count)
                (runtime / "owner.json").unlink()
                with self.assertRaisesRegex(ValueError, "ownership/version"):
                    launcher["main"]([])
                self.assertFalse((runtime / "owner.json").exists())
                self.assertEqual(4, invoke.call_count)
                (runtime / "owner.json").write_bytes(owner)
                invoke.reset_mock()
                invoke.side_effect = [7, 0, 0]
                self.assertEqual(7, launcher["main"]([]))
                self.assertEqual(1, invoke.call_count)
                self.assertEqual("sync", invoke.call_args.args[0][1])
                self.assertEqual(0, launcher["main"]([]))
                self.assertEqual(3, invoke.call_count)
                self.assertIn("--no-sync", invoke.call_args.args[0])

    def test_deterministic_complete_package_and_read_only_check(self):
        with tempfile.TemporaryDirectory(prefix="plugin space ") as temporary:
            folder = Path(temporary)
            one, two = folder / "one.zip", folder / "two.zip"
            self.assertEqual(BUILDER["build_plugin"](one), BUILDER["build_plugin"](two))
            self.assertEqual(one.read_bytes(), two.read_bytes())
            with zipfile.ZipFile(one) as archive:
                inventory = json.loads(archive.read("release-kit/package.json"))
                self.assertIn("lib/releasekit_mcp/projects.py", inventory["files"])
                self.assertIn("tools/relkit.pyz", inventory["files"])
                self.assertEqual(len(inventory["files"]) + 1, len(archive.namelist()))
                for name, digest in inventory["files"].items():
                    self.assertEqual(
                        digest, hashlib.sha256(archive.read("release-kit/" + name)).hexdigest()
                    )
                    self.assertNotIn(".runtime", name)
                    self.assertNotIn("__pycache__", name)
                archive.extractall(folder)
            package = folder / "release-kit"
            environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            mcp = json.loads((package / ".mcp.json").read_text())["mcpServers"]["releasekit"]
            self.assertEqual(".", mcp["cwd"])
            self.assertEqual("python", mcp["command"])
            self.assertTrue(all("${" not in argument for argument in mcp["args"]))
            check = [sys.executable, *mcp["args"], "--check"]
            result = subprocess.run(
                check,
                cwd=package / mcp["cwd"],
                capture_output=True,
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            checked = json.loads(result.stdout)
            self.assertTrue(checked["valid"])
            self.assertEqual(
                {name: __version__ for name in ("plugin", "runtime", "lock", "cli", "inventory")},
                checked["components"],
            )
            self.assertFalse((package / ".runtime").exists())
            (package / "skills/release-kit/SKILL.md").write_text("modified", encoding="utf-8")
            result = subprocess.run(
                check,
                cwd=package / mcp["cwd"],
                capture_output=True,
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn(b"payload changed", result.stderr)

    def test_version_mismatch_refuses_packaging(self):
        with (
            patch("json.loads", return_value={"name": "release-kit", "version": "0.0.0"}),
            self.assertRaisesRegex(ValueError, "versions must agree"),
        ):
            BUILDER["payloads"]()

    def test_stale_runtime_lock_refuses_packaging(self):
        original = tomllib.loads

        def stale_lock(text):
            parsed = original(text)
            for package in parsed.get("package", []):
                if package["name"] == "release-kit-plugin-runtime":
                    package["version"] = "0.0.0"
            return parsed

        with (
            patch("tomllib.loads", side_effect=stale_lock),
            self.assertRaisesRegex(ValueError, "versions must agree"),
        ):
            BUILDER["payloads"]()


if __name__ == "__main__":
    unittest.main()
