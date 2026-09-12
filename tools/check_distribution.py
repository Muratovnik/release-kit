"""Check source or exact CLI/plugin assets, without publishing or registering them.

Default mode checks source and a development build. The release coordinator uses
--source-only before building, then --assets with its Git-free source snapshot and
explicit --work-dir to qualify the actual candidate. Reports bind the checked bytes.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from smoke import inventory

ROOT = Path(__file__).resolve().parents[1]


class CheckFailure(RuntimeError):
    """A required check did not complete successfully."""


def locked_command(uv: str, root: Path, script: str, *arguments: str) -> list[str]:
    return [
        uv,
        "run",
        "--locked",
        "--no-config",
        "--no-dev",
        "--no-editable",
        "--no-python-downloads",
        "--no-build",
        "--project",
        str(root / "plugins/release-kit"),
        "--python",
        sys.executable,
        "python",
        str(root / script),
        *arguments,
    ]


def stages(
    root: Path,
    workspace: Path,
    uv: str,
    version: str,
    assets: Path | None = None,
    *,
    source_only: bool = False,
):
    prepare = (
        ("base", [sys.executable, str(root / "tools/check.py")]),
        ("mcp", locked_command(uv, root, "tools/check_mcp.py")),
    )
    if source_only:
        return prepare
    provided = assets is not None
    assets = assets or workspace / "assets"
    build = (
        (
            "build",
            [
                sys.executable,
                str(root / "tools/build_release.py"),
                "--allow-divergent",
                str(assets),
            ],
        ),
    )
    checks = (
        (
            "cli-smoke",
            [
                sys.executable,
                str(root / "tools/smoke.py"),
                "--assets",
                str(assets),
                "--source",
                str(root),
                "--version",
                version,
            ],
        ),
        (
            "onboarding",
            [
                sys.executable,
                str(root / "tools/smoke_onboarding.py"),
                "--artifact",
                str(assets / "relkit.pyz"),
                "--work-dir",
                str(workspace / "onboarding space"),
            ],
        ),
        (
            "plugin-stdio",
            locked_command(
                uv,
                root,
                "tools/smoke_plugin.py",
                "--archive",
                str(assets / "release-kit-plugin.zip"),
                "--work-dir",
                str(workspace / "plugin space"),
                "--version",
                version,
            ),
        ),
    )
    return checks if provided else prepare + build + checks


def run_stages(commands, *, root: Path, environment: dict[str, str], report: dict) -> None:
    for name, command in commands:
        print(f"distribution-check: {name}", flush=True)
        entry = {"name": name, "status": "running"}
        report["checks"].append(entry)
        try:
            completed = subprocess.run(command, cwd=root, env=environment, check=False)
        except (OSError, KeyboardInterrupt):
            entry["status"] = "interrupted-or-unavailable"
            raise
        entry.update(
            exit_code=completed.returncode,
            status="passed" if not completed.returncode else "failed",
        )
        if completed.returncode:
            raise CheckFailure(f"{name} failed with exit {completed.returncode}")


def source_identity(root: Path) -> dict:
    """A coordinator snapshot has no Git metadata; never discover an ancestor's Git."""
    if not (root / ".git").exists():
        return {"source_kind": "git-free-snapshot", "source_head": None, "source_dirty": None}
    found = subprocess.run(
        ["git", "rev-parse", "--show-toplevel", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    if len(found) != 2 or Path(found[0]).resolve() != root.resolve():
        raise CheckFailure("source directory is not its own Git checkout")
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
    )
    return {"source_kind": "checkout", "source_head": found[1], "source_dirty": bool(dirty.stdout)}


def check_packages(commands, *, assets, version, root, environment, report):
    """All package checks must apply to one unchanged, complete set of bytes."""
    before = inventory(assets, version)
    report["artifacts"] = before
    run_stages(commands, root=root, environment=environment, report=report)
    after = inventory(assets, version)
    if after != before:
        raise CheckFailure("candidate assets changed during qualification")
    report["artifacts_unchanged"] = True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, help="Existing assets; never rebuild them")
    parser.add_argument("--version", help="Required version of the existing assets")
    parser.add_argument("--source-only", action="store_true", help="Base and MCP source tests only")
    parser.add_argument(
        "--work-dir", type=Path, help="Explicit existing scratch parent, required for a snapshot"
    )
    arguments = parser.parse_args(argv)
    if (arguments.assets is None) != (arguments.version is None):
        parser.error("--assets and --version must be supplied together")
    if arguments.source_only and arguments.assets is not None:
        parser.error("--source-only cannot be combined with --assets")
    workspace = None
    report = {
        "schema": 1,
        "kind": (
            "source-check"
            if arguments.source_only
            else "provided-assets-check"
            if arguments.assets
            else "working-tree-distribution-check"
        ),
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "checks": [],
        "status": "failed",
        "native_client_discovery": "not-verified",
        "publication": "not-attempted",
    }
    try:
        uv = shutil.which("uv")
        if uv is None:
            raise CheckFailure("install uv on PATH before the distribution check")
        sys.path.insert(0, str(ROOT / "src"))
        from releasekit import storage

        if storage.redirected_git():
            raise CheckFailure("unset Git location overrides before selecting this source")
        report.update(source_identity(ROOT))
        if arguments.assets is None and report["source_kind"] != "checkout":
            raise CheckFailure("source checks require this project's checkout")
        assets = None
        if arguments.assets is not None:
            # The coordinator places assets beside its source snapshot, not inside it.
            assets = storage.checked(ROOT / arguments.assets)
            if not assets.is_dir():
                raise CheckFailure("--assets must name an existing asset directory")
        if arguments.work_dir is not None:
            parent = storage.checked(ROOT / arguments.work_dir)
            if not parent.is_dir():
                raise CheckFailure("--work-dir must be an existing, explicitly owned directory")
            if assets is not None and parent.is_relative_to(assets):
                raise CheckFailure("scratch cannot be inside the candidate assets")
            workspace = Path(tempfile.mkdtemp(prefix="distribution-check-", dir=parent))
        else:
            if report["source_kind"] != "checkout":
                raise CheckFailure("a Git-free snapshot requires an explicit --work-dir")
            workspace = storage.Workspace(ROOT, "distribution-check-").path
        report["workspace"] = str(workspace)
        storage.atomic_json(workspace / "owner.json", {"schema": 1, "tool": "check_distribution"})
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        version = project["version"]
        report["version"] = version
        if arguments.version is not None and arguments.version != version:
            raise CheckFailure("requested asset version differs from this source snapshot")
        environment = {
            key: value
            for key, value in storage.environment(workspace).items()
            if not key.startswith("UV_") and key not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")
        }
        environment.update(
            PYTHONPATH=str(ROOT / "src"),
            UV_CACHE_DIR=str(workspace / "uv-cache"),
            UV_PROJECT_ENVIRONMENT=str(workspace / "sdk-venv"),
            UV_PYTHON_DOWNLOADS="never",
            UV_LINK_MODE="copy",
        )
        commands = stages(ROOT, workspace, uv, version, assets, source_only=arguments.source_only)
        report["required_checks"] = [name for name, _ in commands]
        print(f"distribution-check: {report['kind']}; retained workspace {workspace}", flush=True)
        source_commands = [
            (name, cmd) for name, cmd in commands if name in {"base", "mcp", "build"}
        ]
        package_commands = [
            (name, cmd) for name, cmd in commands if name not in {"base", "mcp", "build"}
        ]
        run_stages(source_commands, root=ROOT, environment=environment, report=report)
        if not arguments.source_only:
            check_packages(
                package_commands,
                assets=assets or workspace / "assets",
                version=version,
                root=ROOT,
                environment=environment,
                report=report,
            )
        report["status"] = "passed"
        print("distribution-check: all selected checks passed on this host")
        return 0
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        report["error"] = str(error)
        print(f"distribution-check: {error}", file=sys.stderr)
        return 1
    finally:
        if workspace is not None:
            from releasekit import storage

            storage.atomic_json(workspace / "result.json", report)
            print(f"distribution-check: evidence retained at {workspace / 'result.json'}")
        else:
            print(json.dumps(report, sort_keys=True), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
