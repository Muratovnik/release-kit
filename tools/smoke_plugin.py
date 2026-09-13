"""Start a trusted built plugin using its shipped MCP launch configuration.

This checks local SDK stdio startup, not native desktop discovery. It authorizes
no project binding, hook or publication. The caller owns cleanup of the fresh fixtures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import zipfile
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Launch:
    command: str
    args: list[str]
    cwd: Path
    env: dict[str, str]
    startup_timeout: float
    tool_timeout: float


def load_launch(package: Path, environment: dict[str, str]) -> Launch:
    """Read this package's stdio contract; do not substitute a working launcher."""
    document = json.loads((package / ".mcp.json").read_text(encoding="utf-8"))
    server = document["mcpServers"]["releasekit"]
    command, args, cwd = server["command"], server["args"], server["cwd"]
    if not isinstance(command, str) or not command or "\0" in command:
        raise ValueError("plugin command must be a nonempty executable name")
    if not isinstance(args, list) or not all(
        isinstance(arg, str) and "\0" not in arg for arg in args
    ):
        raise ValueError("plugin args must be separate string arguments")
    if not isinstance(cwd, str) or not cwd or Path(cwd).is_absolute():
        raise ValueError("plugin cwd must be relative to the installed package")
    working = (package / cwd).resolve()
    if not working.is_relative_to(package.resolve()) or not working.is_dir():
        raise ValueError("plugin cwd must be an existing directory inside the package")
    overrides = server.get("env", {})
    if not isinstance(overrides, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in overrides.items()
    ):
        raise ValueError("plugin env must map strings to strings")
    times = [server["startup_timeout_sec"], server["tool_timeout_sec"]]
    if any(
        type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in times
    ):
        raise ValueError("plugin timeouts must be finite positive seconds")
    return Launch(command, args, working, {**environment, **overrides}, *times)


def check_launch(launch: Launch, version: str) -> None:
    """Use exactly the installed command/args/cwd/env for the launcher's check mode."""
    checked = subprocess.run(
        [launch.command, *launch.args, "--check"],
        cwd=launch.cwd,
        env=launch.env,
        capture_output=True,
        encoding="utf-8",
        timeout=launch.startup_timeout,
        check=True,
    )
    check = json.loads(checked.stdout)
    if check.get("valid") is not True or set(check["components"].values()) != {version}:
        raise RuntimeError("built plugin component check disagrees with the release version")


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
                or "\\" in item.orig_filename
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

    if sys.platform == "win32":
        # Exercise uv's real cache and wheel installation beyond MAX_PATH even
        # when the caller selected a short workspace. Keep the adopter path short.
        directory.mkdir()
        padding = max(0, 180 - len(str(directory / "plugin space" / "release-kit")))
        package = extract(archive_path, directory / ("plugin space" + "x" * padding))
    else:
        package = extract(archive_path, directory)
    launch = load_launch(package, fixture_environment())
    check_launch(launch, version)
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
            command=launch.command, args=launch.args, cwd=launch.cwd, env=launch.env
        )
        # Keep the startup scope outside the client's own task groups until close.
        # Popping it immediately after __aenter__ would corrupt AnyIO scope ordering.
        with anyio.fail_after(launch.startup_timeout) as startup:
            async with Client(parameters, elicitation_callback=reject_prompt) as client:
                startup.deadline = math.inf
                with anyio.fail_after(launch.tool_timeout):
                    discovered = await client.list_tools()
                missing = REQUIRED_TOOLS - {tool.name for tool in discovered.tools}
                if missing:
                    raise RuntimeError(f"built plugin is missing tools: {sorted(missing)}")
                with anyio.fail_after(launch.tool_timeout):
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
                with anyio.fail_after(launch.tool_timeout):
                    aligned = await client.call_tool(
                        "relkit_sync", {"request": {"action": "status", "root": str(project)}}
                    )
                if aligned.is_error or aligned.structured_content["sync"]["state"] != "aligned":
                    raise RuntimeError(f"built plugin failed read-only alignment: {aligned}")

    async def concurrent_start():
        async with anyio.create_task_group() as group:
            group.start_soon(scenario)
            group.start_soon(scenario)

    anyio.run(concurrent_start)
    if git(project, project_environment, "rev-parse", "HEAD") != before:
        raise RuntimeError("plugin read changed the fixture commit")
    if git(project, project_environment, "status", "--porcelain"):
        raise RuntimeError("plugin reads left unexpected project changes")
    if (
        git(project, project_environment, "tag", "--list")
        or (project / ".git/hooks/pre-push").exists()
    ):
        raise RuntimeError("plugin reads installed a hook or created a tag")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Trusted built plugin ZIP")
    parser.add_argument(
        "--work-dir", type=Path, required=True, help="New check workspace directory"
    )
    parser.add_argument("--version", required=True)
    arguments = parser.parse_args()
    try:
        smoke(arguments.archive.absolute(), arguments.work_dir.absolute(), arguments.version)
    except subprocess.CalledProcessError as error:
        detail = error.stderr or error.stdout or "no launcher diagnostics"
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        print(
            f"plugin-smoke: launcher exited {error.returncode}\n{detail[-8192:]}", file=sys.stderr
        )
        return 1
    except (
        ExceptionGroup,
        OSError,
        ValueError,
        RuntimeError,
        ImportError,
        KeyError,
        subprocess.SubprocessError,
    ) as error:
        # Native SDK failures include exception groups; never downgrade to --version.
        print(f"plugin-smoke: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print("plugin-smoke: shipped MCP configuration, native stdio and project reads passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
