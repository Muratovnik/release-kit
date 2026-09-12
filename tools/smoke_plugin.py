"""Start a trusted built plugin through its launcher and perform real SDK reads.

This is a local package qualification, not native desktop-client discovery. It
requires the locked optional SDK environment. No project binding, hook or release
is authorized, and all fixtures/runtime outputs are retained in the given new root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

from smoke_onboarding import create_project, fixture_environment, git

REQUIRED_TOOLS = {
    "relkit_version",
    "relkit_audit",
    "relkit_exposure",
    "relkit_overlay",
    "relkit_notes",
    "relkit_protect",
    "relkit_release",
    "relkit_update",
    "relkit_project",
    "relkit_sync",
}


def extract(archive_path: Path, destination: Path) -> Path:
    """Accept the built package's single-root shape, never an existing destination."""
    destination.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if (
                not path.parts
                or path.parts[0] != "release-kit"
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in item.filename
                or ":" in item.filename
                or (item.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("built plugin contains an unsafe or unexpected archive entry")
        if len(names) != len(set(names)):
            raise ValueError("built plugin contains duplicate archive entries")
        archive.extractall(destination)
    return destination / "release-kit"


def smoke(archive_path: Path, directory: Path, version: str) -> None:
    import anyio
    from mcp import Client, StdioServerParameters

    package = extract(archive_path, directory)
    environment = fixture_environment()
    launcher = package / "scripts/launch.py"
    checked = subprocess.run(
        [sys.executable, str(launcher), "--check"],
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    check = json.loads(checked.stdout)
    if check.get("valid") is not True or set(check["components"].values()) != {version}:
        raise RuntimeError("built plugin component check disagrees with the release version")
    if (package / ".runtime").exists():
        raise RuntimeError("package hash check unexpectedly provisioned the runtime")

    project = directory / "adopter"
    artifact = package / "tools/relkit.pyz"
    project_environment = create_project(project, artifact, examples=package / "examples/audit")
    before = git(project, project_environment, "rev-parse", "HEAD")
    expected_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    async def reject_prompt(ctx, params):
        raise RuntimeError("read-only package smoke unexpectedly requested confirmation")

    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(launcher)],
            env=environment,
        )
        with anyio.fail_after(600):
            async with Client(parameters, elicitation_callback=reject_prompt) as client:
                discovered = await client.list_tools()
                missing = REQUIRED_TOOLS - {tool.name for tool in discovered.tools}
                if missing:
                    raise RuntimeError(f"built plugin is missing tools: {sorted(missing)}")
                inspected = await client.call_tool(
                    "relkit_project", {"request": {"action": "inspect", "root": str(project)}}
                )
                if inspected.is_error:
                    raise RuntimeError(f"built plugin project inspection failed: {inspected}")
                review = inspected.structured_content["review"]
                if (
                    review["project"] != str(project)
                    or review["projection_version"] != version
                    or review["projection_sha256"] != expected_digest
                ):
                    raise RuntimeError("built plugin inspected a different project or projection")
                aligned = await client.call_tool(
                    "relkit_sync", {"request": {"action": "status", "root": str(project)}}
                )
                if aligned.is_error or aligned.structured_content["sync"]["state"] != "aligned":
                    raise RuntimeError(f"built plugin failed read-only alignment: {aligned}")

    anyio.run(scenario)
    if git(project, project_environment, "rev-parse", "HEAD") != before:
        raise RuntimeError("plugin read changed the fixture commit")
    if git(project, project_environment, "status", "--porcelain"):
        raise RuntimeError("plugin reads left unexpected project changes")
    if git(project, project_environment, "tag", "--list") or (
        project / ".git/hooks/pre-push"
    ).exists():
        raise RuntimeError("plugin reads installed a hook or created a tag")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Trusted built plugin ZIP")
    parser.add_argument(
        "--work-dir", type=Path, required=True, help="New directory inside check workspace"
    )
    parser.add_argument("--version", required=True)
    arguments = parser.parse_args()
    try:
        smoke(arguments.archive.absolute(), arguments.work_dir.absolute(), arguments.version)
    except Exception as error:
        # Native SDK/transport failures include exception groups. They are a failed
        # package check, not a reason to fall back to a --version-only smoke.
        print(f"plugin-smoke: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print("plugin-smoke: extracted launcher, native stdio discovery and project reads passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
