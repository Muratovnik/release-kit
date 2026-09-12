"""Qualify the CLI and built plugin locally, without publishing or registering them.

The base check stays SDK-free. This explicitly selected full gate additionally
uses the plugin's existing uv lock for integrations and tests the installed shape,
not just source imports. Workspaces and evidence are retained inside the project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

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


def stages(root: Path, workspace: Path, uv: str, version: str, assets: Path | None = None):
    provided = assets is not None
    assets = assets or workspace / "assets"
    prepare = (
        ("base", [sys.executable, str(root / "tools/check.py")]),
        ("mcp", locked_command(uv, root, "tools/check_mcp.py")),
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
    return checks if provided else prepare + checks


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, help="Existing project-local assets; do not rebuild")
    parser.add_argument("--version", help="Required version of the existing assets")
    arguments = parser.parse_args(argv)
    if (arguments.assets is None) != (arguments.version is None):
        parser.error("--assets and --version must be supplied together")
    workspace = None
    report = {
        "schema": 1,
        "kind": "provided-assets-check" if arguments.assets else "working-tree-distribution-check",
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
            raise CheckFailure("install uv on PATH before the full distribution check")
        sys.path.insert(0, str(ROOT / "src"))
        from releasekit import storage

        if storage.redirected_git():
            raise CheckFailure("unset Git location overrides before selecting this checkout")
        workspace = storage.Workspace(ROOT, "distribution-check-")
        report["workspace"] = str(workspace.path)
        storage.atomic_json(
            workspace.path / "owner.json", {"schema": 1, "tool": "check_distribution"}
        )
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        version = project["version"]
        report["version"] = version
        if arguments.version is not None and arguments.version != version:
            raise CheckFailure("requested asset version differs from this source snapshot")
        assets = None
        if arguments.assets is not None:
            assets = storage.inside(ROOT, ROOT / arguments.assets)
            if not assets.is_dir():
                raise CheckFailure("--assets must name an existing directory inside this checkout")
        identity = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        report["source_head"] = identity.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        report["source_dirty"] = bool(dirty.stdout)
        environment = {
            key: value
            for key, value in workspace.environment().items()
            if not key.startswith("UV_") and key not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")
        }
        environment.update(
            PYTHONPATH=str(ROOT / "src"),
            UV_CACHE_DIR=str(workspace.path / "uv-cache"),
            UV_PROJECT_ENVIRONMENT=str(workspace.path / "sdk-venv"),
            UV_PYTHON_DOWNLOADS="never",
            UV_LINK_MODE="copy",
        )
        print(f"distribution-check: retained workspace {workspace.path}", flush=True)
        print(
            "distribution-check: checking provided bytes without rebuilding"
            if assets is not None
            else "distribution-check: working-tree test build, not a publication candidate",
            flush=True,
        )
        commands = stages(ROOT, workspace.path, uv, version, assets)
        report["required_checks"] = [name for name, _ in commands]
        run_stages(
            commands,
            root=ROOT,
            environment=environment,
            report=report,
        )
        report["artifacts"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((assets or workspace.path / "assets").iterdir())
            if path.is_file()
        }
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
            # SDK/plugin environments are deliberate retained evidence, not generic
            # scratch to sweep. A later cleanup needs a separate ownership review.
            from releasekit import storage

            storage.atomic_json(workspace.path / "result.json", report)
            print(f"distribution-check: evidence retained at {workspace.path / 'result.json'}")
        else:
            print(json.dumps(report, sort_keys=True), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
