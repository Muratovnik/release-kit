"""Plugin protocol acceptance: explicit targets, real confirmations, no publications."""

import io
import os
import sys
import zipfile

import anyio
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import ElicitResult
from test_stdio import SOURCE, Fixture

from releasekit_mcp.projects import Projects


class PluginTests(Fixture):
    def test_configured_scanner_policy_drift_requires_a_new_binding(self):
        policy = self.root / ".gitleaks.toml"
        (self.root / "relkit.toml").write_text(
            '[exposure]\nbetterleaks_config = ".gitleaks.toml"\n'
        )
        policy.write_text("[extend]\nuseDefault = true\n")
        projects = Projects()
        reviewed = projects.inspect(str(self.root))
        binding = projects.bind(reviewed)
        (self.root / "README.md").write_text("ordinary source edits remain supported\n")
        projects.get(binding)
        policy.write_text('[extend]\nuseDefault = true\n[allowlist]\npaths = [".*"]\n')
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            projects.get(binding)
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            projects.bind(reviewed)
        refreshed = projects.bind(projects.inspect(str(self.root)))
        projects.get(refreshed)
        policy.unlink()
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            projects.get(refreshed)

    def test_direct_update_request_allows_checks_of_dirty_pin_without_second_dialog(self):
        self.projection.write_bytes(self.projection.read_bytes() + b"\n")
        dirty_pin = self.projection.read_bytes()

        async def scenario():
            for mode in ("auto", "legacy"):
                async with self.plugin_client(mode, callback=None) as client:
                    inspected = await client.call_tool(
                        "relkit_project", {"request": {"action": "inspect", "root": str(self.root)}}
                    )
                    authorization = {
                        "source": "user_request",
                        "scope": "project_checks",
                        "review_sha256": inspected.structured_content["review_sha256"],
                    }
                    bound = await client.call_tool(
                        "relkit_project",
                        {
                            "request": {
                                "action": "bind",
                                "root": str(self.root),
                                "authorization": authorization,
                            }
                        },
                    )
                    self.assertFalse(bound.is_error, bound)
                    self.assertEqual(
                        "user_request", bound.structured_content["authorization_source"]
                    )
                    binding = bound.structured_content["binding"]
                    result = await client.call_tool(
                        "relkit_version", {"binding": binding, "request": {}}
                    )
                    self.assertFalse(result.is_error, result)
                    result = await client.call_tool(
                        "relkit_audit", {"binding": binding, "request": {"no_download": True}}
                    )
                    self.assertFalse(result.is_error, result)
                    self.assertEqual(dirty_pin, self.projection.read_bytes())
                    # Check permission never becomes permission for unrelated writes.
                    try:
                        result = await client.call_tool(
                            "relkit_notes",
                            {
                                "binding": binding,
                                "request": {"version": "1.0.0", "output": "notes.txt"},
                            },
                        )
                    except MCPError as error:
                        self.assertIn("elicitation", str(error))
                    else:
                        self.assertTrue(result.is_error, result)
                    self.assertFalse((self.root / "notes.txt").exists())
            self.assertEqual([], self.prompts)

        self.run_async(scenario)

    def test_project_authorization_is_exact_and_cannot_be_reused_after_drift(self):
        other = Fixture()
        other.setUp()
        self.addCleanup(other.doCleanups)

        async def scenario():
            async with self.plugin_client(callback=None) as client:
                inspected = await client.call_tool(
                    "relkit_project", {"request": {"action": "inspect", "root": str(self.root)}}
                )
                authorization = {
                    "source": "user_request",
                    "scope": "project_checks",
                    "review_sha256": inspected.structured_content["review_sha256"],
                }
                wrong_project = await client.call_tool(
                    "relkit_project",
                    {
                        "request": {
                            "action": "bind",
                            "root": str(other.root),
                            "authorization": authorization,
                        }
                    },
                )
                self.assertTrue(wrong_project.is_error, wrong_project)
                for action, changes in (
                    ("inspect", {}),
                    ("bind", {"scope": "sync_update"}),
                    ("bind", {"review_sha256": "0" * 64}),
                    ("bind", {"source": "repository"}),
                ):
                    result = await client.call_tool(
                        "relkit_project",
                        {
                            "request": {
                                "action": action,
                                "root": str(self.root),
                                "authorization": {**authorization, **changes},
                            }
                        },
                    )
                    self.assertTrue(result.is_error, result)
                (self.root / "relkit.toml").write_text("# policy drift\n")
                result = await client.call_tool(
                    "relkit_project",
                    {
                        "request": {
                            "action": "bind",
                            "root": str(self.root),
                            "authorization": authorization,
                        }
                    },
                )
                self.assertTrue(result.is_error, result)
                self.assertEqual([], self.prompts)

        self.run_async(scenario)

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
