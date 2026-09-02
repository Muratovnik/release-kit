"""Bundled updates cover the installed-plugin/older-project adoption gap."""

import io
import json
import os
import runpy
import sys
import zipfile

from mcp import Client, StdioServerParameters
from mcp.types import ElicitResult
from test_stdio import ROOT, SOURCE, Fixture

from releasekit import __version__, distribution, protection

sys.path.insert(0, str(ROOT / "tools"))
BUILD_PLUGIN = runpy.run_path(str(ROOT / "tools/build_plugin.py"))["build_plugin"]


class SyncTests(Fixture):
    def setUp(self):
        super().setUp()
        folder = self.root / ".cache/installed plugin"
        folder.mkdir(parents=True)
        archive = folder / "plugin.zip"
        BUILD_PLUGIN(archive)
        with zipfile.ZipFile(archive) as package:
            package.extractall(folder)
        self.bundle = folder / "release-kit"
        self.target = (self.bundle / "tools/relkit.pyz").read_bytes()
        self.old_projection("0.9.0")

    def old_projection(self, version):
        buffer = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(self.target)) as original,
            zipfile.ZipFile(buffer, "w") as changed,
        ):
            for name in original.namelist():
                payload = original.read(name)
                if name in ("releasekit/__init__.py", distribution.BUILD_INFO):
                    payload = payload.replace(__version__.encode(), version.encode())
                if name == "releasekit/cli.py":
                    payload += (
                        b'\n__import__("pathlib").Path("old-code-executed.txt").write_text("ran")\n'
                    )
                changed.writestr(name, payload)
        self.projection.write_bytes(buffer.getvalue())
        self.git("add", ".github/relkit.pyz")
        self.git("commit", "-qm", "test: pin previous distribution")
        self.old_bytes = self.projection.read_bytes()

    def sync_client(self, mode="auto", callback="default"):
        return Client(
            StdioServerParameters(
                command=sys.executable,
                args=["-m", "releasekit_mcp.server", "--plugin", "--bundle", str(self.bundle)],
                env={**os.environ, "PYTHONPATH": str(SOURCE), "PYTHONDONTWRITEBYTECODE": "1"},
            ),
            mode=mode,
            elicitation_callback=self.accept if callback == "default" else callback,
        )

    async def sync(self, client, action="status", **kwargs):
        return await client.call_tool(
            "relkit_sync",
            {
                "request": {
                    "root": str(self.root),
                    "action": action,
                    "no_download": True,
                    **kwargs,
                }
            },
        )

    async def plan(self, client, action="plan"):
        result = await self.sync(client, action)
        self.assertFalse(result.is_error, result)
        return result.structured_content["result"]["data"]["plan_sha256"]

    async def authorized_plan(self, client, action="plan"):
        result = await self.sync(client, action)
        self.assertFalse(result.is_error, result)
        data = result.structured_content
        return {
            "plan_hash": data["result"]["data"]["plan_sha256"],
            "authorization": {
                "source": "user_request",
                "scope": "sync_update" if action == "plan" else "sync_rollback",
                "review_sha256": data["review_sha256"],
            },
        }

    def test_direct_update_request_needs_no_second_dialog_in_either_era(self):
        protection.install(self.root)
        guard = self.root / ".git/hooks/pre-push"
        old_guard = guard.read_bytes()

        async def scenario():
            for mode in ("auto", "legacy"):
                async with self.sync_client(mode, callback=None) as client:
                    result = await self.sync(client, "apply", **await self.authorized_plan(client))
                    self.assertFalse(result.is_error, result)
                    self.assertEqual(
                        "user_request", result.structured_content["authorization_source"]
                    )
                    self.assertEqual(self.target, self.projection.read_bytes())
                    self.assertIsNone(protection.problem(self.root))
                    result = await self.sync(
                        client, "rollback", **await self.authorized_plan(client, "rollback_plan")
                    )
                    self.assertFalse(result.is_error, result)
                    self.assertEqual(
                        "user_request", result.structured_content["authorization_source"]
                    )
                    self.assertEqual(self.old_bytes, self.projection.read_bytes())
                    self.assertEqual(old_guard, guard.read_bytes())
                    self.assertFalse((self.root / "old-code-executed.txt").exists())
            self.assertEqual([], self.prompts)

        self.run_async(scenario)

    def test_direct_authorization_refuses_wrong_scope_hash_and_changed_policy(self):
        async def scenario():
            async with self.sync_client(callback=None) as client:
                planned = await self.authorized_plan(client)
                for changes in (
                    {"scope": "project_checks"},
                    {"scope": "sync_rollback"},
                    {"review_sha256": "0" * 64},
                    {"source": "project_text"},
                    {"approve": True},
                ):
                    invalid = {**planned, "authorization": {**planned["authorization"], **changes}}
                    result = await self.sync(client, "apply", **invalid)
                    self.assertTrue(result.is_error, result)
                for action in ("status", "plan", "rollback_plan"):
                    result = await self.sync(client, action, authorization=planned["authorization"])
                    self.assertTrue(result.is_error, result)
                policy = self.root / "AGENTS.md"
                policy.write_text("# Changed policy\n")
                self.git("add", "AGENTS.md")
                self.git("commit", "-qm", "test: change project policy")
                # Even if the updater's plan is unchanged, the reviewed policy is not.
                result = await self.sync(client, "apply", **planned)
                self.assertTrue(result.is_error, result)
                fresh = await self.authorized_plan(client)
                self.assertNotEqual(
                    planned["authorization"]["review_sha256"],
                    fresh["authorization"]["review_sha256"],
                )
                unrelated = self.root / "user-file.txt"
                unrelated.write_text("preserve me")
                result = await self.sync(client, "apply", **fresh)
                self.assertTrue(result.is_error, result)
                self.assertEqual("preserve me", unrelated.read_text())
                self.assertEqual(self.old_bytes, self.projection.read_bytes())
                self.assertFalse((self.root / ".git/relkit-update.json").exists())
                self.assertEqual([], self.prompts)

        self.run_async(scenario)

    def test_status_plan_apply_rollback_both_eras_without_project_code_trust(self):
        async def scenario():
            for mode in ("auto", "legacy"):
                async with self.sync_client(mode) as client:
                    tools = (await client.list_tools()).tools
                    sync = next(t for t in tools if t.name == "relkit_sync")
                    self.assertNotIn("binding", sync.input_schema.get("properties", {}))
                    self.assertNotIn("approval", json.dumps(sync.input_schema))
                    result = await self.sync(client)
                    self.assertEqual("update_available", result.structured_content["sync"]["state"])
                    before = len(self.prompts)
                    plan_hash = await self.plan(client)
                    self.assertEqual(before, len(self.prompts))
                    self.assertEqual(self.old_bytes, self.projection.read_bytes())
                    result = await self.sync(client, "apply", plan_hash=plan_hash)
                    self.assertFalse(result.is_error, result)
                    self.assertEqual("aligned", result.structured_content["sync"]["state"])
                    self.assertTrue(result.structured_content["restart_required"])
                    self.assertEqual(self.target, self.projection.read_bytes())
                    self.assertEqual(before + 1, len(self.prompts))
                    receipt = json.loads((self.root / ".git/relkit-update.json").read_text())
                    self.assertEqual(
                        self.old_bytes,
                        (self.root / ".git" / receipt["backup"] / "relkit.pyz").read_bytes(),
                    )
                    plan_hash = await self.plan(client, "rollback_plan")
                    result = await self.sync(client, "rollback", plan_hash=plan_hash)
                    self.assertFalse(result.is_error, result)
                    self.assertEqual(self.old_bytes, self.projection.read_bytes())
                    self.assertEqual(before + 2, len(self.prompts))
                    self.assertFalse((self.root / "old-code-executed.txt").exists())

        self.run_async(scenario)

    def test_pre_mcp_projection_is_updatable_without_executing_it(self):
        self.old_projection("0.6.0")

        async def scenario():
            async with self.sync_client() as client:
                plan_hash = await self.plan(client)
                result = await self.sync(client, "apply", plan_hash=plan_hash)
                self.assertFalse(result.is_error, result)
                self.assertFalse((self.root / "old-code-executed.txt").exists())

        self.run_async(scenario)

    def test_decline_cancel_false_and_policy_drift_never_write(self):
        async def scenario():
            for mode in ("auto", "legacy"):
                for action, content in (
                    ("decline", None),
                    ("cancel", None),
                    ("accept", {"approve": False}),
                ):

                    async def deny(ctx, params, action=action, content=content):
                        return ElicitResult(action=action, content=content)

                    async with self.sync_client(mode, deny) as client:
                        plan_hash = await self.plan(client)
                        result = await self.sync(client, "apply", plan_hash=plan_hash)
                        self.assertTrue(result.is_error, result)
                        self.assertTrue(
                            result.structured_content["error_code"].startswith("confirmation_")
                        )
                        self.assertEqual(self.old_bytes, self.projection.read_bytes())
                        self.assertFalse((self.root / ".git/relkit-update.json").exists())

            async def mutate(ctx, params):
                (self.root / "AGENTS.md").write_text("Changed policy during review.\n")
                return ElicitResult(action="accept", content={"approve": True})

            async with self.sync_client("legacy", mutate) as client:
                plan_hash = await self.plan(client)
                result = await self.sync(client, "apply", plan_hash=plan_hash)
                self.assertTrue(result.is_error, result)
                self.assertEqual(self.old_bytes, self.projection.read_bytes())

        self.run_async(scenario)

    def test_dirty_and_stale_plan_and_bundle_drift_fail_closed(self):
        async def scenario():
            async with self.sync_client() as client:
                plan_hash = await self.plan(client)
                result = await self.sync(client, "apply", plan_hash="0" * 64)
                self.assertTrue(result.is_error)
                unrelated = self.root / "user-file.txt"
                unrelated.write_text("preserve me")
                result = await self.sync(client, "apply", plan_hash=plan_hash)
                self.assertTrue(result.is_error)
                self.assertEqual("preserve me", unrelated.read_text())
                bundled = self.bundle / "tools/relkit.pyz"
                bundled.write_bytes(bundled.read_bytes() + b"drift")
                result = await self.sync(client)
                self.assertTrue(result.is_error)
                self.assertEqual(self.old_bytes, self.projection.read_bytes())
                self.assertEqual([], self.prompts)

        self.run_async(scenario)

    def test_owned_guard_refresh_uses_the_same_approved_updater(self):
        protection.install(self.root)
        guard = self.root / ".git/hooks/pre-push"
        old_guard = guard.read_bytes()

        async def scenario():
            async with self.sync_client() as client:
                plan_hash = await self.plan(client)
                result = await self.sync(client, "apply", plan_hash=plan_hash)
                self.assertFalse(result.is_error, result)
                self.assertNotEqual(old_guard, guard.read_bytes())
                self.assertIsNone(protection.problem(self.root))
                self.assertIn(".git/hooks/pre-push", self.prompts[-1])
                result = await self.sync(
                    client, "rollback", plan_hash=await self.plan(client, "rollback_plan")
                )
                self.assertFalse(result.is_error, result)
                self.assertEqual(old_guard, guard.read_bytes())

        self.run_async(scenario)

    def test_equal_version_different_bytes_and_newer_projects_are_not_downgraded(self):
        async def scenario():
            for version, state in (
                (__version__, "same_version_drift"),
                ("99.0.0", "project_newer"),
            ):
                self.old_projection(version)
                async with self.sync_client() as client:
                    result = await self.sync(client)
                    self.assertEqual(state, result.structured_content["sync"]["state"])
                    result = await self.sync(client, "plan")
                    self.assertTrue(result.is_error, result)
                    self.assertEqual(self.old_bytes, self.projection.read_bytes())

        self.run_async(scenario)
