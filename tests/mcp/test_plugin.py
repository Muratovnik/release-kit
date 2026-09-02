"""Plugin protocol acceptance: explicit targets, real confirmations, no publications."""

import io
import os
import sys
import zipfile

import anyio
from mcp import Client, StdioServerParameters
from mcp.types import ElicitResult
from test_stdio import SOURCE, Fixture

from releasekit_mcp.projects import Projects


class PluginTests(Fixture):
    def test_inspection_never_executes_untrusted_projection_code(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(self.projection) as original, zipfile.ZipFile(buffer, "w") as changed:
            for entry in original.namelist():
                payload = original.read(entry)
                if entry == "releasekit/cli.py":
                    payload += b'\n__import__("pathlib").Path("untrusted-executed.txt").write_text("ran")\n'
                changed.writestr(entry, payload)
        self.projection.write_bytes(buffer.getvalue())
        inspected = Projects().inspect(str(self.root))
        self.assertEqual(str(self.root), inspected.review["project"])
        self.assertFalse((self.root / "untrusted-executed.txt").exists())

    def test_policy_drift_during_legacy_trust_is_refused(self):
        async def mutate(ctx, params):
            (self.root / "relkit.toml").write_text("# changed during trust\n")
            return ElicitResult(action="accept", content={"approve": True})

        async def scenario():
            async with self.plugin_client(mode="legacy", callback=mutate) as client:
                result = await client.call_tool(
                    "relkit_project", {"request": {"action": "bind", "root": str(self.root)}}
                )
                self.assertTrue(result.is_error, result)

        self.run_async(scenario)

    def plugin_client(self, mode="auto", callback="default"):
        return Client(
            StdioServerParameters(
                command=sys.executable,
                args=["-m", "releasekit_mcp.server", "--plugin"],
                env={**os.environ, "PYTHONPATH": str(SOURCE), "PYTHONDONTWRITEBYTECODE": "1"},
            ),
            mode=mode,
            elicitation_callback=self.accept if callback == "default" else callback,
        )

    async def bind(self, client, root=None):
        result = await client.call_tool(
            "relkit_project", {"request": {"action": "bind", "root": str(root or self.root)}}
        )
        self.assertFalse(result.is_error, result)
        return result.structured_content["binding"]

    def test_both_eras_inspect_bind_read_write_unbind_and_no_implicit_target(self):
        async def scenario():
            for mode in ("auto", "legacy"):
                async with self.plugin_client(mode) as client:
                    tools = (await client.list_tools()).tools
                    self.assertEqual(9, len(tools))
                    for tool in tools:
                        self.assertNotIn("approval", tool.input_schema.get("properties", {}))
                        if tool.name != "relkit_project":
                            self.assertIn("binding", tool.input_schema["required"])
                    before = len(self.prompts)
                    inspected = await client.call_tool(
                        "relkit_project", {"request": {"action": "inspect", "root": str(self.root)}}
                    )
                    self.assertFalse(inspected.is_error, inspected)
                    self.assertEqual(
                        self.digest, inspected.structured_content["review"]["projection_sha256"]
                    )
                    self.assertIsNone(inspected.structured_content["binding"])
                    self.assertEqual(before, len(self.prompts))
                    binding = await self.bind(client)
                    self.assertEqual(before + 1, len(self.prompts))
                    result = await client.call_tool(
                        "relkit_version", {"binding": binding, "request": {}}
                    )
                    self.assertFalse(result.is_error, result)
                    result = await client.call_tool(
                        "relkit_notes",
                        {
                            "binding": binding,
                            "request": {"version": "1.0.0", "output": "approved-notes.txt"},
                        },
                    )
                    self.assertFalse(result.is_error, result)
                    self.assertEqual(before + 2, len(self.prompts))
                    result = await client.call_tool(
                        "relkit_project", {"request": {"action": "unbind", "binding": binding}}
                    )
                    self.assertFalse(result.is_error, result)
                    result = await client.call_tool(
                        "relkit_version", {"binding": binding, "request": {}}
                    )
                    self.assertTrue(result.is_error)

        self.run_async(scenario)

    def test_bind_decline_does_not_authorize_a_workflow(self):
        async def decline(ctx, params):
            return ElicitResult(action="decline")

        async def scenario():
            async with self.plugin_client(callback=decline) as client:
                result = await client.call_tool(
                    "relkit_project", {"request": {"action": "bind", "root": str(self.root)}}
                )
                self.assertTrue(result.is_error, result)
                result = await client.call_tool(
                    "relkit_version", {"binding": "invented", "request": {}}
                )
                self.assertTrue(result.is_error)
            async with self.plugin_client() as client:
                binding = await self.bind(client)
            async with self.plugin_client() as client:
                result = await client.call_tool(
                    "relkit_version", {"binding": binding, "request": {}}
                )
                self.assertTrue(result.is_error)

        self.run_async(scenario)

    def test_interleaved_projects_never_share_default_state(self):
        second = Fixture()
        second.setUp()
        self.addCleanup(second.doCleanups)

        async def scenario():
            async with self.plugin_client() as client:
                first_binding = await self.bind(client)
                second_binding = await self.bind(client, second.root)
                results = {}

                async def read(key, binding):
                    results[key] = await client.call_tool(
                        "relkit_version", {"binding": binding, "request": {}}
                    )

                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(read, "first", first_binding)
                    tasks.start_soon(read, "second", second_binding)
                self.assertEqual(str(self.root), results["first"].structured_content["project"])
                self.assertEqual(str(second.root), results["second"].structured_content["project"])
                (self.root / "relkit.toml").write_text("# changed policy\n")
                result = await client.call_tool(
                    "relkit_version", {"binding": first_binding, "request": {}}
                )
                self.assertTrue(result.is_error)
                result = await client.call_tool(
                    "relkit_version", {"binding": second_binding, "request": {}}
                )
                self.assertFalse(result.is_error, result)

        self.run_async(scenario)

    def test_bind_detects_changed_inputs_and_rejects_relative_roots(self):
        registry = Projects()
        with self.assertRaisesRegex(ValueError, "absolute"):
            registry.inspect(".")
        prepared = registry.inspect(str(self.root))
        (self.root / "relkit.toml").write_text("# drift\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            registry.bind(prepared)
        self.assertEqual({}, registry.bindings)

    def test_repeated_bindings_share_one_project_operation_lock(self):
        registry = Projects()
        first = registry.bind(registry.inspect(str(self.root)))
        second = registry.bind(registry.inspect(str(self.root)))
        self.assertNotEqual(first, second)
        self.assertIs(registry.get(first), registry.get(second))
        registry.unbind(first)
        self.assertIsNotNone(registry.get(second))
