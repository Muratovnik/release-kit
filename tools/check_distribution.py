"""Check source and exact CLI/plugin packages without publishing or registering them.

Source checks and package checks are separate phases. The coordinator supplies a
Git-free snapshot and the actual candidate; development mode builds a test package.
The selected parent owns one reusable SDK/cache, compact reports and fresh fixtures.
"""

from __future__ import annotations

import argparse
import json
import os
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
        "--exact",
        "--no-config",
        "--no-dev",
        "--no-editable",
        "--no-python-downloads",
        "--no-build",
        "--project",
        str(root / "plugins/release-kit"),
        "--python",
        # uv compares the base interpreter, not the development venv's launcher.
        str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
        "python",
        str(root / script),
        *arguments,
    ]


def source_checks(root: Path, uv: str):
    return (
        ("base", [sys.executable, str(root / "tools/check.py")]),
        ("mcp", locked_command(uv, root, "tools/check_mcp.py")),
    )


def package_checks(root: Path, workspace: Path, uv: str, version: str, assets: Path):
    return (
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


def run_stages(
    commands, *, root: Path, environment: dict[str, str], report: dict, timeout: float = 1800
) -> None:
    from releasekit import processes

    for name, command in commands:
        print(f"distribution-check: {name}", flush=True)
        entry = {"name": name, "status": "running"}
        report["checks"].append(entry)
        try:
            completed = processes.run(
                command, cwd=root, env=environment, check=False, timeout=timeout
            )
        except processes.CleanupError:
            entry["status"] = "cleanup-unconfirmed"
            report["process_cleanup"] = "unconfirmed"
            raise
        except subprocess.TimeoutExpired:
            entry["status"] = "timed-out"
            raise
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
    """A snapshot has no Git metadata; never discover an ancestor's checkout."""
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
    """Bind all package checks to one complete, unchanged set of bytes."""
    before = inventory(assets, version)
    report["artifacts"] = before
    run_stages(commands, root=root, environment=environment, report=report)
    if inventory(assets, version) != before:
        raise CheckFailure("candidate assets changed during qualification")
    report["artifacts_unchanged"] = True


def check_environment(root: Path, state: Path, workspace: Path):
    from releasekit import storage

    environment = {
        key: value
        for key, value in storage.environment(workspace / "tmp").items()
        if not key.startswith("UV_") and key not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")
    }
    environment.update(
        PYTHONPATH=str(root / "src"),
        UV_CACHE_DIR=str(storage.inside(state, state / "uv-cache")),
        UV_PROJECT_ENVIRONMENT=str(
            storage.inside(state, state / f"sdk-{sys.implementation.cache_tag}")
        ),
        UV_PYTHON_DOWNLOADS="never",
        UV_LINK_MODE="copy",
    )
    return environment


def clean_success(workspace: Path, identity) -> bool:
    """Delete only this run's newly allocated fixtures, never caches or input assets.

    Phase outputs have fixed, task-owned roots. Unexpected top-level data or leftover
    process scratch retains the run for inspection. Tests use disposable projects;
    this is not protection against hostile concurrent writers inside those projects.
    """
    from releasekit import storage

    if storage.identity(workspace) != identity:
        return False
    owned = {"assets", "onboarding space", "plugin space", "tmp"}
    if {p.name for p in workspace.iterdir()} - owned:
        return False
    if any((workspace / "tmp").iterdir()):
        return False
    # Check the roots, not venv internals: rmtree unlinks internal symlinks without
    # following them. An aliased fixture root must never be recursively removed.
    for child in workspace.iterdir():
        storage.checked(child)
        if not child.is_dir():
            return False
    shutil.rmtree(workspace)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, help="Existing assets; never rebuild them")
    parser.add_argument("--version", help="Required version of the existing assets")
    parser.add_argument("--source-only", action="store_true", help="Base and MCP source tests only")
    parser.add_argument(
        "--work-dir", type=Path, help="Explicit existing parent, required for a snapshot"
    )
    arguments = parser.parse_args(argv)
    if (arguments.assets is None) != (arguments.version is None):
        parser.error("--assets and --version must be supplied together")
    if arguments.source_only and arguments.assets is not None:
        parser.error("--source-only cannot be combined with --assets")
    workspace = state = lock = lock_identity = None
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
        assets = storage.checked(ROOT / arguments.assets) if arguments.assets is not None else None
        if assets is not None and not assets.is_dir():
            raise CheckFailure("--assets must name an existing asset directory")
        if arguments.work_dir is not None:
            parent = storage.checked(ROOT / arguments.work_dir)
            if not parent.is_dir():
                raise CheckFailure("--work-dir must be an existing, explicitly owned directory")
        else:
            if report["source_kind"] != "checkout":
                raise CheckFailure("a Git-free snapshot requires an explicit --work-dir")
            parent = storage.inside(ROOT, ROOT / ".cache")
            ignored = subprocess.run(
                [
                    "git",
                    "check-ignore",
                    "--quiet",
                    "--no-index",
                    "--",
                    ".cache/release-kit-checks/",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            if ignored.returncode:
                raise CheckFailure(
                    "ignore .cache/release-kit-checks/ before provisioning check tools"
                )
        state = storage.checked(parent / "release-kit-checks")
        if assets is not None and (state.is_relative_to(assets) or assets.is_relative_to(state)):
            raise CheckFailure("candidate assets must be separate from check state")
        marker = {"schema": 1, "tool": "check_distribution"}
        if state.exists():
            if json.loads(storage.inside(state, state / "owner.json").read_text()) != marker:
                raise CheckFailure("check state ownership differs; choose another owned parent")
        else:
            state.mkdir(parents=True)
            storage.atomic_json(state / "owner.json", marker)
        lock = storage.inside(state, state / "check.lock")
        try:
            with lock.open("x", encoding="utf-8") as handle:
                lock_identity = storage.identity(lock)
                handle.write(str(os.getpid()) + "\n")
        except FileExistsError as error:
            raise CheckFailure(
                f"check state is busy: {lock}; a dead PID does not prove its descendants "
                "stopped. Preserve the lock and investigate, or select another owned parent."
            ) from error
        workspace = Path(tempfile.mkdtemp(prefix="run-", dir=state))
        identity = storage.identity(workspace)
        (workspace / "tmp").mkdir()
        report["workspace"] = str(workspace)
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        version = project["version"]
        report["version"] = version
        if arguments.version is not None and arguments.version != version:
            raise CheckFailure("requested asset version differs from this source snapshot")
        environment = check_environment(ROOT, state, workspace)
        print(f"distribution-check: {report['kind']}; workspace {workspace}", flush=True)
        if assets is None:
            run_stages(
                source_checks(ROOT, uv),
                root=ROOT,
                environment=environment,
                report=report,
                timeout=3600,
            )
            if not arguments.source_only:
                assets = workspace / "assets"
                run_stages(
                    (
                        (
                            "build",
                            [
                                sys.executable,
                                str(ROOT / "tools/build_release.py"),
                                "--allow-divergent",
                                str(assets),
                            ],
                        ),
                    ),
                    root=ROOT,
                    environment=environment,
                    report=report,
                )
        if not arguments.source_only:
            check_packages(
                package_checks(ROOT, workspace, uv, version, assets),
                assets=assets,
                version=version,
                root=ROOT,
                environment=environment,
                report=report,
            )
        report["status"] = "passed"
        try:
            report["cleanup"] = "removed" if clean_success(workspace, identity) else "retained"
        except (OSError, RuntimeError) as error:
            report["cleanup"] = "retained"
            report["cleanup_error"] = str(error)
        return 0
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        print(f"distribution-check: {error}", file=sys.stderr)
        return 1
    finally:
        try:
            if workspace is not None:
                report.setdefault("cleanup", "retained")
                output = storage.inside(state, state / "reports" / (workspace.name + ".json"))
                storage.atomic_json(output, report)
                print(f"distribution-check: report {output}")
        finally:
            if (
                lock_identity is not None
                and report.get("process_cleanup") != "unconfirmed"
                and storage.identity(lock) == lock_identity
            ):
                lock.unlink()
            # Hosted runners are ephemeral. Keep the compact result in the job log
            # as well; no upload of runtime directories or unredacted command output.
            summary = {
                key: value
                for key, value in report.items()
                if key not in {"workspace", "error", "cleanup_error"}
            }
            print("distribution-check: result " + json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    from releasekit import processes

    with processes.termination_handler():
        raise SystemExit(main())
