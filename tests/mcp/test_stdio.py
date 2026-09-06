"""Optional integration suite: real SDK clients and subprocess stdio servers."""

import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import anyio
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import ElicitResult

from releasekit import __version__, distribution
from releasekit_mcp.bridge import Bridge
from releasekit_mcp.projects import Projects

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src"
BUILD = runpy.run_path(str(ROOT / "tools/build_zipapp.py"))["build"]
POLICY = "[exposure]\ncheck_secrets = false\ncheck_links = false\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mcp space ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "Example Maintainer")
        self.git("config", "user.email", "maintainer@example.invalid")
        self.git("config", "core.hooksPath", ".git/hooks")
        self.git("config", "commit.gpgsign", "false")
        self.projection = self.root / ".github/relkit.pyz"
        self.projection.parent.mkdir()
        BUILD(self.projection)
        (self.root / "relkit.toml").write_text(POLICY)
        (self.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [1.0.0]\n\n### Added\n\n- Example feature.\n"
        )
        (self.root / ".gitignore").write_text(".cache/\n")
        self.git("add", ".")
        self.git("commit", "-qm", "feat: initial example")
        self.digest = hashlib.sha256(self.projection.read_bytes()).hexdigest()
        self.prompts = []

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True).stdout

    async def accept(self, ctx, params):
        self.prompts.append(params.message)
        return ElicitResult(action="accept", content={"approve": True})

    def client(self, *, mode="auto", callback="default", timeout=30):
        return Client(
            StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "releasekit_mcp.server",
                    "--root",
                    str(self.root),
                    "--sha256",
                    self.digest,
                    "--timeout",
                    str(timeout),
                ],
                env={**os.environ, "PYTHONPATH": str(SOURCE), "PYTHONDONTWRITEBYTECODE": "1"},
            ),
            mode=mode,
            elicitation_callback=self.accept if callback == "default" else callback,
        )

    def run_async(self, fn):
        anyio.run(fn)


class StdioTests(Fixture):
    def test_notes_preserves_prerelease_and_unreleased_cli_support(self):
        (self.root / "CHANGELOG.md").write_text(
            "## [Unreleased]\n\nUpcoming notes.\n\n## [1.1.0-rc.1+build]\n\nCandidate notes.\n"
        )

        async def scenario():
            async with self.client() as client:
                for version in ("Unreleased", "v1.1.0-rc.1+build"):
                    result = await client.call_tool(
                        "relkit_notes", {"request": {"version": version}}
                    )
                    self.assertFalse(result.is_error, result)

        self.run_async(scenario)

    def test_modern_confirmation_is_reasked_after_input_drift(self):
        prompts = []

        async def accept_and_mutate_once(ctx, params):
            prompts.append(params.message)
            if len(prompts) == 1:
                (self.root / "CHANGELOG.md").write_text("## [1.0.0]\n\nNew reviewed notes.\n")
            return ElicitResult(action="accept", content={"approve": True})

        async def scenario():
            async with self.client(callback=accept_and_mutate_once) as client:
                result = await client.call_tool(
                    "relkit_notes", {"request": {"version": "1.0.0", "output": "notes.txt"}}
                )
                self.assertFalse(result.is_error, result)
            self.assertEqual(2, len(prompts))
            self.assertNotEqual(prompts[0], prompts[1])
            self.assertIn("New reviewed notes", (self.root / "notes.txt").read_text())

        self.run_async(scenario)

    def test_large_confirmation_and_nested_checkout_are_refused(self):
        (self.root / "CHANGELOG.md").write_text("## [1.0.0]\n\n" + "x" * 70000 + "\n")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / ".git").mkdir()

        async def scenario():
            async with self.client() as client:
                for output in ("notes.txt", "nested/notes.txt"):
                    result = await client.call_tool(
                        "relkit_notes", {"request": {"version": "1.0.0", "output": output}}
                    )
                    self.assertTrue(result.is_error)
            self.assertEqual([], self.prompts)
            self.assertFalse((self.root / "notes.txt").exists())

        self.run_async(scenario)

    def test_real_update_and_rollback_require_restart_with_new_pin(self):
        stream = io.BytesIO()
        with (
            zipfile.ZipFile(self.projection) as original,
            zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as candidate,
        ):
            for name in original.namelist():
                payload = original.read(name)
                if name in ("releasekit/__init__.py", distribution.BUILD_INFO):
                    future = distribution.version_tuple(__version__)
                    next_version = f"{future[0]}.{future[1]}.{future[2] + 1}".encode()
                    payload = payload.replace(__version__.encode(), next_version)
                candidate.writestr(name, payload)
        folder = self.root / ".cache"
        folder.mkdir()
        (folder / "candidate.pyz").write_bytes(stream.getvalue())
        expected = hashlib.sha256(stream.getvalue()).hexdigest()
        old_digest = self.digest

        async def scenario():
            async with self.client() as client:
                source = {
                    "artifact": ".cache/candidate.pyz",
                    "sha256": expected,
                    "no_download": True,
                }
                result = await client.call_tool(
                    "relkit_update", {"request": {"action": "plan", **source}}
                )
                self.assertFalse(result.is_error, result)
                plan_hash = result.structured_content["result"]["data"]["plan_sha256"]
                result = await client.call_tool(
                    "relkit_update",
                    {"request": {"action": "apply", "plan_hash": plan_hash, **source}},
                )
                self.assertFalse(result.is_error, result)
                self.assertTrue(result.structured_content["restart_required"])
                result = await client.call_tool("relkit_version", {"request": {}})
                self.assertTrue(result.is_error)
            self.digest = expected
            async with self.client() as client:
                result = await client.call_tool(
                    "relkit_update", {"request": {"action": "rollback_plan"}}
                )
                self.assertFalse(result.is_error, result)
                plan_hash = result.structured_content["result"]["data"]["plan_sha256"]
                result = await client.call_tool(
                    "relkit_update", {"request": {"action": "rollback", "plan_hash": plan_hash}}
                )
                self.assertFalse(result.is_error, result)
                self.assertTrue(result.structured_content["restart_required"])
            self.assertEqual(old_digest, hashlib.sha256(self.projection.read_bytes()).hexdigest())
            self.assertEqual(2, len(self.prompts))

        self.run_async(scenario)

    def test_discovery_and_read_tools_in_both_protocol_eras(self):
        async def scenario():
            for mode in ("auto", "legacy"):
                async with self.client(mode=mode) as client:
                    tools = await client.list_tools()
                    self.assertEqual(
                        {t.name for t in tools.tools},
                        {
                            "relkit_version",
                            "relkit_audit",
                            "relkit_exposure",
                            "relkit_overlay",
                            "relkit_notes",
                            "relkit_protect",
                            "relkit_release",
                            "relkit_update",
                        },
                    )
                    for tool in tools.tools:
                        schema = json.dumps(tool.input_schema)
                        self.assertNotIn('"approval"', schema)
                        self.assertNotIn('"prepared"', schema)
                        self.assertIsNotNone(tool.output_schema)
                    for name, request in (
                        ("relkit_version", {}),
                        ("relkit_notes", {"version": "1.0.0"}),
                        ("relkit_exposure", {}),
                        ("relkit_audit", {"no_download": True}),
                    ):
                        result = await client.call_tool(name, {"request": request})
                        self.assertFalse(result.is_error, result)
                        self.assertEqual(result.structured_content["result"]["exit_code"], 0)
                    self.assertEqual(self.prompts, [])

        self.run_async(scenario)

    def test_notes_export_confirms_in_both_protocol_eras(self):
        async def scenario():
            for mode in ("auto", "legacy"):
                async with self.client(mode=mode) as client:
                    result = await client.call_tool(
                        "relkit_notes", {"request": {"version": "1.0.0", "output": "notes.txt"}}
                    )
                    self.assertFalse(result.is_error, result)
                    self.assertIn("Example feature", (self.root / "notes.txt").read_text())
            self.assertEqual(len(self.prompts), 2)
            self.assertEqual(
                str(self.root), json.loads(self.prompts[0].split("\n", 1)[1])["project"]
            )
            self.assertIn("after_sha256", self.prompts[0])

        self.run_async(scenario)

    def test_decline_cancel_false_and_missing_capability_do_not_write(self):
        async def scenario():
            for action in ("decline", "cancel", "false", "missing"):

                async def reject(ctx, params, action=action):
                    return (
                        ElicitResult(action="accept", content={"approve": False})
                        if action == "false"
                        else ElicitResult(action=action)
                    )

                async with self.client(callback=None if action == "missing" else reject) as client:
                    try:
                        result = await client.call_tool(
                            "relkit_notes", {"request": {"version": "1.0.0", "output": "notes.txt"}}
                        )
                    except MCPError as error:
                        self.assertIn("elicitation", str(error).lower())
                    else:
                        self.assertTrue(result.is_error, result)
                self.assertFalse((self.root / "notes.txt").exists())

        self.run_async(scenario)

    def test_inputs_changed_during_legacy_confirmation_are_refused(self):
        async def mutate(ctx, params):
            (self.root / "CHANGELOG.md").write_text("## [1.0.0]\n\nChanged after review.\n")
            return ElicitResult(action="accept", content={"approve": True})

        async def scenario():
            async with self.client(mode="legacy", callback=mutate) as client:
                result = await client.call_tool(
                    "relkit_notes", {"request": {"version": "1.0.0", "output": "notes.txt"}}
                )
                self.assertTrue(result.is_error)
                self.assertFalse((self.root / "notes.txt").exists())

        self.run_async(scenario)

    def test_untrusted_paths_and_arguments_are_refused(self):
        async def scenario():
            async with self.client() as client:
                for output in (
                    "../escape",
                    "C:/escape",
                    ".git/config",
                    ".GIT/config",
                    "NUL.txt",
                    "relkit.toml",
                    ".github/relkit.pyz",
                    "notes.txt:stream",
                    "sub/../notes.txt",
                ):
                    result = await client.call_tool(
                        "relkit_notes", {"request": {"version": "1.0.0", "output": output}}
                    )
                    self.assertTrue(result.is_error, output)
                for extra in ({"root": "../other"}, {"approve": True}, {"argv": ["--publish"]}):
                    result = await client.call_tool(
                        "relkit_notes", {"request": {"version": "1.0.0", **extra}}
                    )
                    self.assertTrue(result.is_error)
            self.assertEqual(self.prompts, [])

        self.run_async(scenario)

    def test_guard_plan_install_and_refresh(self):
        async def scenario():
            async with self.client() as client:
                result = await client.call_tool("relkit_protect", {"request": {"action": "plan"}})
                self.assertFalse(result.is_error, result)
                plan = result.structured_content["result"]["data"]["plan"]
                result = await client.call_tool(
                    "relkit_protect",
                    {"request": {"action": "install", "plan_hash": plan["plan_sha256"]}},
                )
                self.assertFalse(result.is_error, result)
                self.assertTrue((self.root / ".git/hooks/pre-push").exists())
                (self.root / "relkit.toml").write_text(POLICY + "\n# reviewed policy change\n")
                result = await client.call_tool(
                    "relkit_update", {"request": {"action": "plan", "refresh_guard": True}}
                )
                self.assertFalse(result.is_error, result)
                digest = result.structured_content["result"]["data"]["plan_sha256"]
                result = await client.call_tool(
                    "relkit_update",
                    {
                        "request": {
                            "action": "apply",
                            "refresh_guard": True,
                            "plan_hash": digest,
                            "no_download": True,
                        }
                    },
                )
                self.assertFalse(result.is_error, result)
                result = await client.call_tool("relkit_protect", {"request": {"action": "check"}})
                self.assertFalse(result.is_error, result)
            self.assertEqual(len(self.prompts), 2)

        self.run_async(scenario)

    def test_stale_hash_and_projection_fail_closed(self):
        async def scenario():
            async with self.client() as client:
                result = await client.call_tool(
                    "relkit_protect", {"request": {"action": "install", "plan_hash": "0" * 64}}
                )
                self.assertTrue(result.is_error)
                self.assertEqual(self.prompts, [])
                self.projection.write_bytes(b"changed")
                result = await client.call_tool("relkit_version", {"request": {}})
                self.assertTrue(result.is_error)

        self.run_async(scenario)


class BridgeTests(Fixture):
    def test_missing_optional_sdk_has_actionable_startup_error(self):
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-m",
                "releasekit_mcp.server",
                "--root",
                str(self.root),
                "--sha256",
                self.digest,
            ],
            env={**os.environ, "PYTHONPATH": str(SOURCE)},
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual(b"", result.stdout)
        self.assertIn(b"install release-kit[mcp] in an isolated environment", result.stderr)

    def test_binding_rejects_wrong_pin_and_git_overrides(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            Bridge(self.root, "0" * 64)
        with (
            patch.dict(os.environ, {"GIT_DIR": str(self.root / ".git")}),
            self.assertRaisesRegex(ValueError, "GIT_DIR"),
        ):
            Bridge(self.root, self.digest)

    def test_an_ordinary_git_variable_is_not_a_reason_to_refuse_to_start(self):
        # GIT_SSH_COMMAND and friends describe how Git talks to its operator; they
        # do not redirect the repository, and refusing them rejects normal machines.
        with patch.dict(
            os.environ,
            {"GIT_SSH_COMMAND": "ssh -i /dev/null", "GIT_PAGER": "cat", "GIT_ASKPASS": "true"},
        ):
            self.assertEqual(self.root, Bridge(self.root, self.digest).root)

    def test_a_checkout_git_refuses_is_reported_instead_of_raised(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        broken = Path(outside.name).resolve() / "broken"
        (broken / ".git").mkdir(parents=True)

        with self.assertRaisesRegex(ValueError, "git could not identify the bound checkout"):
            Bridge(broken, self.digest)

    def test_the_reviewed_binding_covers_the_publishing_workflow(self):
        workflow = self.root / ".github/workflows/release.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("on: push\njobs:\n  publish:\n    runs-on: ubuntu-latest\n")
        (self.root / "relkit.toml").write_text(
            POLICY + "\n"
            '[release]\nrepository = "example/project"\n'
            'workflow = ".github/workflows/release.yml"\nrequired_jobs = ["publish"]\n'
            'version_file = "VERSION"\nversion_pattern = "^(.+)$"\n'
            'assets = ["example-{version}.zip"]\nchecks = [["python", "check.py"]]\n'
            'smoke = [["python", "smoke.py"]]\nsmoke_platforms = ["linux", "darwin", "win32"]\n'
        )
        bridge = Bridge(self.root, self.digest)

        review = Projects.review(bridge)

        self.assertIn(".github/workflows/release.yml", review["inputs"])
        workflow.write_text(workflow.read_text() + "  upload:\n    runs-on: ubuntu-latest\n")
        self.assertNotEqual(review, Projects.review(bridge))

    def test_hardlink_output_is_not_overwritten(self):
        original = self.root / "original.txt"
        original.write_text("preserve")
        os.link(original, self.root / "linked.txt")

        async def scenario():
            async with self.client() as client:
                result = await client.call_tool(
                    "relkit_notes", {"request": {"version": "1.0.0", "output": "linked.txt"}}
                )
                self.assertTrue(result.is_error)
            self.assertEqual(original.read_text(), "preserve")

        self.run_async(scenario)

    def test_base_zipapp_excludes_optional_adapter_and_works_without_site(self):
        with zipfile.ZipFile(self.projection) as archive:
            self.assertFalse(
                any(name.startswith(("releasekit_mcp/", "mcp/")) for name in archive.namelist())
            )
        result = subprocess.run(
            [sys.executable, "-S", str(self.projection), "--json", "--version"],
            capture_output=True,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["tool_version"], __version__)

    def test_unexpected_result_identity_and_output_limit(self):
        bridge = Bridge(self.root, self.digest)

        async def scenario():
            async def fake(*args, **kwargs):
                return 0, b'{"schema_version":1,"tool_version":"wrong"}', "", False

            with patch("releasekit_mcp.process.execute", fake):
                result = await bridge.call(["--version"], ["version"])
            self.assertIn("invalid", result.error)

        self.run_async(scenario)


if __name__ == "__main__":
    unittest.main()
