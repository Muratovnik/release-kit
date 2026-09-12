"""Direct release instructions must not depend on a second native dialog."""

import contextlib
import io
import json
import runpy
from unittest.mock import patch

import test_plugin
from mcp import Client
from mcp.types import ElicitResult
from test_stdio import BUILD, ROOT, Fixture

from releasekit import __version__, cli, protection, storage
from releasekit.release.backend import ReleaseError
from releasekit.result import Result
from releasekit_mcp import models
from releasekit_mcp.bridge import Bridge
from releasekit_mcp.server import create_server


def authorization(scope, preview):
    return {
        "source": "user_request",
        "scope": scope,
        "review_sha256": preview.structured_content["review_sha256"],
    }


class GuardAuthorizationTests(Fixture):
    plugin_client = test_plugin.PluginTests.plugin_client

    async def authorized_binding(self, client):
        inspected = await client.call_tool(
            "relkit_project", {"request": {"action": "inspect", "root": str(self.root)}}
        )
        bound = await client.call_tool(
            "relkit_project",
            {
                "request": {
                    "action": "bind",
                    "root": str(self.root),
                    "authorization": authorization("project_checks", inspected),
                }
            },
        )
        self.assertFalse(bound.is_error, bound)
        return bound.structured_content["binding"]

    def test_direct_guard_request_installs_real_hook_without_elicitation(self):
        async def scenario():
            for mode in ("auto", "legacy"):
                async with self.plugin_client(mode, callback=None) as client:
                    binding = await self.authorized_binding(client)
                    preview = await client.call_tool(
                        "relkit_protect", {"binding": binding, "request": {"action": "plan"}}
                    )
                    self.assertFalse(preview.is_error, preview)
                    self.assertIsInstance(preview.structured_content["review_sha256"], str)
                    plan = preview.structured_content["result"]["data"]["plan"]
                    result = await client.call_tool(
                        "relkit_protect",
                        {
                            "binding": binding,
                            "request": {
                                "action": "install",
                                "plan_hash": plan["plan_sha256"],
                                "authorization": authorization("protect_install", preview),
                            },
                        },
                    )
                    self.assertFalse(result.is_error, result)
                    self.assertEqual(
                        "user_request", result.structured_content["authorization_source"]
                    )
                    self.assertIsNone(protection.problem(self.root))
                    self.assertEqual([], self.prompts)

        self.run_async(scenario)

    def test_decline_does_not_install_and_foreign_hook_is_not_replaced(self):
        async def decline(ctx, params):
            self.prompts.append(params.message)
            return ElicitResult(action="decline")

        async def scenario():
            async with self.plugin_client(callback=decline) as client:
                binding = await self.authorized_binding(client)
                preview = await client.call_tool(
                    "relkit_protect", {"binding": binding, "request": {"action": "plan"}}
                )
                plan = preview.structured_content["result"]["data"]["plan"]
                result = await client.call_tool(
                    "relkit_protect",
                    {
                        "binding": binding,
                        "request": {
                            "action": "install",
                            "plan_hash": plan["plan_sha256"],
                        },
                    },
                )
                self.assertTrue(result.is_error, result)
                self.assertEqual("confirmation_decline", result.structured_content["error_code"])
                guard = self.root / ".git/hooks/pre-push"
                self.assertFalse(guard.exists())
                # A new user request cannot grant ownership over a foreign hook.
                guard.write_text("#!/bin/sh\nexit 0\n")
                binding = await self.authorized_binding(client)
                result = await client.call_tool(
                    "relkit_protect",
                    {
                        "binding": binding,
                        "request": {
                            "action": "install",
                            "plan_hash": plan["plan_sha256"],
                            "authorization": authorization("protect_install", preview),
                        },
                    },
                )
                self.assertTrue(result.is_error, result)
                self.assertEqual("#!/bin/sh\nexit 0\n", guard.read_text())
                self.assertEqual(1, len(self.prompts))

        self.run_async(scenario)


class ReleaseAuthorizationTests(Fixture):
    def test_next_and_prepare_have_no_publication_write_or_approval(self):
        async def scenario():
            for request, expected in [
                (
                    models.Release(action="next", bump="patch"),
                    ["release", "next", "--bump", "patch"],
                ),
                (
                    models.Release(action="prepare", version="v1.0.0"),
                    ["release", "prepare", "v1.0.0"],
                ),
                (
                    models.Release(action="prepare", version="v1.0.0", ci_run=35),
                    ["release", "prepare", "v1.0.0", "--ci-run", "35"],
                ),
            ]:
                prepared = await self.bridge.prepare(request)
                self.assertEqual(expected, prepared.argv)
                self.assertFalse(prepared.write)
            for options in [
                {"action": "next", "version": "v1.0.0", "bump": "patch"},
                {
                    "action": "prepare",
                    "version": "v1.0.0",
                    "authorization": {
                        "source": "user_request",
                        "scope": "release_run",
                        "review_sha256": "a" * 64,
                    },
                },
            ]:
                with self.assertRaises(ValueError):
                    models.Release(**options)

        self.run_async(scenario)

    """Real SDK/bridge; the CLI boundary is captured, never a hosted publisher."""

    def setUp(self):
        super().setUp()
        self.bridge = Bridge(self.root, self.digest)
        self.writes = []
        self.plan = {
            "plan_sha256": "a" * 64,
            "sha": self.git("rev-parse", "HEAD").decode().strip(),
            "tag": "v1.0.0",
            "root": str(self.root),
            "pushes": ["refs/tags/v1.0.0:refs/tags/v1.0.0"],
        }

    async def cli(self, argv, command, **kwargs):
        if command == ["release", "plan"]:
            data = {"plan": dict(self.plan)}
        elif command == ["release", "status"]:
            data = {"release": {"plan": dict(self.plan), "publication": "unknown"}}
        else:
            self.writes.append(list(argv))
            data = {"release": {"publication": "observed"}}
        return models.Response(
            adapter_version=__version__,
            project=str(self.root),
            projection_sha256=self.digest,
            result=Result(data=data).envelope(command, str(self.root), 0),
        )

    def test_run_and_resume_use_separate_reviewed_authority_without_dialogs(self):
        async def scenario():
            for mode in ("auto", "legacy"):
                with patch.object(self.bridge, "call", self.cli):
                    async with Client(create_server(self.bridge), mode=mode) as client:
                        for preview_action, action, scope, attempt in (
                            ("plan", "run", "release_run", 0),
                            ("resume_plan", "resume", "release_resume", 2),
                        ):
                            options = {
                                "version": "v1.0.0",
                                "no_download": True,
                                "accept_ci_attempt": attempt,
                            }
                            preview = await client.call_tool(
                                "relkit_release", {"request": {"action": preview_action, **options}}
                            )
                            self.assertFalse(preview.is_error, preview)
                            self.assertIsInstance(preview.structured_content["review_sha256"], str)
                            review = preview.structured_content["authorization_review"]
                            self.assertEqual(scope, review["operation"])
                            self.assertEqual(attempt, review["options"]["accept_ci_attempt"])
                            before = len(self.writes)
                            result = await client.call_tool(
                                "relkit_release",
                                {
                                    "request": {
                                        "action": action,
                                        **options,
                                        "plan_hash": "a" * 64,
                                        "authorization": authorization(scope, preview),
                                    }
                                },
                            )
                            self.assertFalse(result.is_error, result)
                            self.assertEqual(
                                "user_request", result.structured_content["authorization_source"]
                            )
                            self.assertEqual(before + 1, len(self.writes))
                            self.assertEqual(
                                [
                                    "release",
                                    action,
                                    "v1.0.0",
                                    "--no-download",
                                    "--publish",
                                    "--plan-hash=" + "a" * 64,
                                    "--accept-ci-attempt=" + str(attempt),
                                ],
                                self.writes[-1],
                            )

        self.run_async(scenario)

    def test_local_release_recovers_after_push_and_ci_disconnect_through_mcp(self):
        # The existing coordinator fixture uses real Git and project commands.
        # Only the GitHub service/gh identity is fake; it refuses live GH calls.
        fixture_type = runpy.run_path(str(ROOT / "tests/test_release.py"))["ReleaseFixture"]
        fixture = fixture_type()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        projection = fixture.root / ".github/relkit.pyz"
        BUILD(projection)
        fixture.commit()
        bridge = Bridge(fixture.root, storage.digest(projection))
        fixture.runner.lost_push_response = True

        def disconnected():
            raise ReleaseError("connection lost during CI observation")

        fixture.github.on_wait = disconnected

        async def local_cli(argv, command, **kwargs):
            output, diagnostic = io.StringIO(), io.StringIO()
            with (
                patch("releasekit.release.coordinator.Runner", return_value=fixture.runner),
                patch("releasekit.release.coordinator.GitHub", return_value=fixture.github),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(diagnostic),
            ):
                cli.main(["--json", *argv, "--root", str(fixture.root)])
            return models.Response(
                adapter_version=__version__,
                project=str(fixture.root),
                projection_sha256=bridge.artifact.sha256,
                result=json.loads(output.getvalue()),
                diagnostics=diagnostic.getvalue(),
            )

        async def scenario():
            with patch.object(bridge, "call", local_cli):
                async with Client(create_server(bridge)) as client:
                    for preview_action, action, scope in (
                        ("plan", "run", "release_run"),
                        ("resume_plan", "resume", "release_resume"),
                    ):
                        options = {"version": "v1.0.0", "no_download": True}
                        preview = await client.call_tool(
                            "relkit_release",
                            {
                                "request": {
                                    "action": preview_action,
                                    **options,
                                }
                            },
                        )
                        self.assertFalse(preview.is_error, preview)
                        data = preview.structured_content["result"]["data"]
                        plan = data["plan"] if action == "run" else data["release"]["plan"]
                        result = await client.call_tool(
                            "relkit_release",
                            {
                                "request": {
                                    "action": action,
                                    **options,
                                    "plan_hash": plan["plan_sha256"],
                                    "authorization": authorization(scope, preview),
                                }
                            },
                        )
                        self.assertEqual(
                            "user_request", result.structured_content["authorization_source"]
                        )
                        if action == "run":
                            self.assertTrue(result.is_error, result)
                            self.assertIn(
                                "connection lost during CI observation",
                                result.structured_content["diagnostics"],
                            )
                            self.assertEqual(1, fixture.runner.pushes)
                            # New local edits after the tag push are not released source.
                            (fixture.root / "check.py").write_text("local work after push\n")
                        else:
                            self.assertFalse(result.is_error, result)
                            self.assertEqual("passed", fixture.receipt()["verification"])
                            self.assertEqual(1, fixture.runner.pushes)
                            self.assertEqual(
                                "local work after push\n", (fixture.root / "check.py").read_text()
                            )
                            self.assertEqual(
                                fixture.sha, fixture.runner.git("rev-parse", "v1.0.0^{}")
                            )
                    fixture.github.ci_result = "failure"
                    verified = await client.call_tool(
                        "relkit_release",
                        {"request": {"action": "verify", "version": "v1.0.0"}},
                    )
                    self.assertTrue(verified.is_error, verified)
                    release = verified.structured_content["result"]["data"]["release"]
                    self.assertEqual("passed", release["verification"])
                    self.assertEqual("failed", release["ci_verdict"])
                    self.assertEqual("incomplete", release["acceptance"])
                    self.assertEqual(1, fixture.runner.pushes)

        self.run_async(scenario)

    def test_wrong_scope_stale_review_options_and_policy_cannot_publish(self):
        async def scenario():
            with patch.object(self.bridge, "call", self.cli):
                async with Client(create_server(self.bridge)) as client:
                    preview = await client.call_tool(
                        "relkit_release", {"request": {"action": "plan", "version": "v1.0.0"}}
                    )
                    self.assertIsInstance(preview.structured_content["review_sha256"], str)
                    request = {
                        "action": "run",
                        "version": "v1.0.0",
                        "plan_hash": "a" * 64,
                        "authorization": authorization("release_run", preview),
                    }
                    for changes in (
                        {"no_download": True},
                        {"action": "resume"},
                        {"action": "plan"},
                        {"authorization": {**request["authorization"], "scope": "sync_update"}},
                        {"authorization": {**request["authorization"], "review_sha256": "0" * 64}},
                        {"authorization": {**request["authorization"], "source": "repository"}},
                    ):
                        result = await client.call_tool(
                            "relkit_release", {"request": {**request, **changes}}
                        )
                        self.assertTrue(result.is_error, result)
                    (self.root / "AGENTS.md").write_text("Changed release policy\n")
                    result = await client.call_tool("relkit_release", {"request": request})
                    self.assertTrue(result.is_error, result)
                    self.assertEqual([], self.writes)

        self.run_async(scenario)

    def test_release_decline_keeps_publication_untouched(self):
        async def decline(ctx, params):
            self.prompts.append(params.message)
            return ElicitResult(action="decline")

        async def scenario():
            with patch.object(self.bridge, "call", self.cli):
                async with Client(
                    create_server(self.bridge), elicitation_callback=decline
                ) as client:
                    result = await client.call_tool(
                        "relkit_release",
                        {
                            "request": {
                                "action": "run",
                                "version": "v1.0.0",
                                "plan_hash": "a" * 64,
                            }
                        },
                    )
                    self.assertTrue(result.is_error, result)
                    self.assertEqual(
                        "confirmation_decline", result.structured_content["error_code"]
                    )
                    self.assertEqual([], self.writes)
                    self.assertEqual(1, len(self.prompts))

        self.run_async(scenario)

    def test_resume_preview_does_not_send_verification_flags_to_status(self):
        async def scenario():
            prepared = await self.bridge.prepare(
                models.Release(
                    action="resume_plan",
                    version="v1.0.0",
                    no_download=True,
                    accept_ci_attempt=2,
                )
            )
            self.assertFalse(prepared.write)
            self.assertEqual(["release", "status", "v1.0.0"], prepared.argv)
            self.assertEqual(["release", "status"], prepared.command)

        self.run_async(scenario)

    def test_resume_attempt_and_other_project_require_their_own_review(self):
        other = Fixture()
        other.setUp()
        self.addCleanup(other.doCleanups)
        other_bridge = Bridge(other.root, other.digest)

        async def scenario():
            with (
                patch.object(self.bridge, "call", self.cli),
                patch.object(other_bridge, "call", self.cli),
            ):
                async with Client(create_server(self.bridge)) as client:
                    preview = await client.call_tool(
                        "relkit_release",
                        {
                            "request": {
                                "action": "resume_plan",
                                "version": "v1.0.0",
                                "accept_ci_attempt": 2,
                            }
                        },
                    )
                    self.assertFalse(preview.is_error, preview)
                    request = {
                        "action": "resume",
                        "version": "v1.0.0",
                        "plan_hash": "a" * 64,
                        "accept_ci_attempt": 2,
                        "authorization": authorization("release_resume", preview),
                    }
                    result = await client.call_tool(
                        "relkit_release",
                        {
                            "request": {
                                **request,
                                "accept_ci_attempt": 3,
                            }
                        },
                    )
                    self.assertTrue(result.is_error, result)
                async with Client(create_server(other_bridge)) as client:
                    result = await client.call_tool("relkit_release", {"request": request})
                    self.assertTrue(result.is_error, result)
                self.assertEqual([], self.writes)

        self.run_async(scenario)
