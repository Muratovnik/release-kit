"""Local publishers must be inspectable without inventing a workflow file."""

import os
import sys

from mcp import Client, StdioServerParameters
from test_stdio import POLICY, SOURCE, Fixture

from releasekit_mcp.projects import Projects


class PublisherBindingTests(Fixture):
    def configure(self, publisher):
        workflow = self.root / ".github/workflows/release.yml"
        workflow.parent.mkdir(exist_ok=True)
        workflow.write_text("name: example\n", encoding="utf-8")
        (self.root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        settings = (
            POLICY
            + rf"""
[release]
publisher = "{publisher}"
version_file = "VERSION"
version_pattern = '^([0-9]+\.[0-9]+\.[0-9]+)$'
assets = ["example.zip"]
checks = [["python", "-c", "pass"]]
build = [["python", "-c", "pass"]]
smoke = [["python", "-c", "pass"]]
smoke_platforms = ["linux", "darwin", "win32"]
"""
        )
        if publisher != "directory":
            settings += 'repository = "example/project"\n'
        if publisher == "github-actions":
            settings += 'workflow = ".github/workflows/release.yml"\nrequired_jobs = ["publish"]\n'
        (self.root / "relkit.toml").write_text(settings, encoding="utf-8")
        return workflow

    def test_all_publishers_allow_inspect_bind_and_preserve_policy_drift_checks(self):
        for publisher in ("directory", "github", "github-actions"):
            with self.subTest(publisher=publisher):
                self.configure(publisher)
                projects = Projects()
                reviewed = projects.inspect(str(self.root))
                self.assertNotIn("", reviewed.review["inputs"])
                self.assertEqual(
                    publisher == "github-actions",
                    ".github/workflows/release.yml" in reviewed.review["inputs"],
                )
                binding = projects.bind(reviewed)
                self.assertEqual(self.root, projects.get(binding).root)
                policy = self.root / "relkit.toml"
                policy.write_text(
                    policy.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(ValueError, "inputs changed"):
                    projects.get(binding)

    def test_actions_workflow_still_expires_a_binding(self):
        workflow = self.configure("github-actions")
        projects = Projects()
        binding = projects.bind(projects.inspect(str(self.root)))
        workflow.write_text("name: changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            projects.get(binding)

    def test_without_release_configuration_remains_supported(self):
        projects = Projects()
        binding = projects.bind(projects.inspect(str(self.root)))
        self.assertEqual(self.root, projects.get(binding).root)

    def test_local_publishers_can_be_bound_over_real_stdio_without_publishing(self):
        async def scenario():
            for publisher in ("directory", "github"):
                self.configure(publisher)
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "releasekit_mcp.server", "--plugin"],
                    env={**os.environ, "PYTHONPATH": str(SOURCE), "PYTHONDONTWRITEBYTECODE": "1"},
                )
                async with Client(parameters, elicitation_callback=self.accept) as client:
                    inspected = await client.call_tool(
                        "relkit_project", {"request": {"action": "inspect", "root": str(self.root)}}
                    )
                    self.assertFalse(inspected.is_error, inspected)
                    bound = await client.call_tool(
                        "relkit_project", {"request": {"action": "bind", "root": str(self.root)}}
                    )
                    self.assertFalse(bound.is_error, bound)
                    version = await client.call_tool(
                        "relkit_version",
                        {"binding": bound.structured_content["binding"], "request": {}},
                    )
                    self.assertFalse(version.is_error, version)
                self.assertEqual(b"", self.git("tag", "--list"))
                self.assertFalse((self.root / ".git/hooks/pre-push").exists())

        self.run_async(scenario)
