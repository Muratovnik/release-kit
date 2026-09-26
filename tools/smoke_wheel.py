"""Install the published wheel the way a person would, and run what it installed.

Metadata a builder wrote is not evidence that an installer accepts it, and an accepted
install is not evidence that the console script runs. This installs the exact wheel
from the candidate assets into a directory it owns, runs the command it advertises and
removes it again. Nothing outside the given work directory is read or written.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

TIMEOUT = 600


class WheelSmokeError(RuntimeError):
    """The published wheel does not install or does not run."""


def _uv() -> str:
    found = shutil.which("uv")
    if not found:
        raise WheelSmokeError("uv is required to install the wheel")
    return found


def _environment(work: Path) -> dict[str, str]:
    """Confine every uv location, so the operator's own tools are never touched."""
    inherited = {key: value for key, value in os.environ.items() if key != "RELKIT_DELEGATE"}
    return {
        **inherited,
        "UV_TOOL_DIR": str(work / "tools"),
        "UV_TOOL_BIN_DIR": str(work / "bin"),
        "UV_CACHE_DIR": str(work / "cache"),
        "UV_NO_MODIFY_PATH": "1",
    }


def _run(
    command: list[str], environment: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=environment,
        cwd=cwd,
        check=False,
    )


def _pinned_repository(work: Path, marker: str) -> Path:
    """A repository whose pinned projection answers with `marker` instead of a version."""
    root = work / "pinned"
    projection = root / ".github" / "relkit.pyz"
    projection.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(projection, "w") as archive:
        archive.writestr("__main__.py", f"print({marker!r})\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=60)
    return root


def check(wheel: Path, version: str, work: Path) -> None:
    uv, environment = _uv(), _environment(work)
    for name in ("tools", "bin", "cache"):
        (work / name).mkdir(parents=True, exist_ok=True)
    # Offline: a wheel with no runtime dependencies needs no index, and requiring one
    # here would test the network instead of the artifact.
    installed = _run([uv, "tool", "install", "--offline", str(wheel)], environment)
    if installed.returncode:
        raise WheelSmokeError(f"wheel did not install:\n{installed.stderr.strip()}")
    try:
        for command in ("relkit", "relkit-mcp"):
            shim = work / "bin" / (command + (".exe" if sys.platform == "win32" else ""))
            if not shim.is_file():
                raise WheelSmokeError(f"wheel did not install the {command} command")
        relkit = str(work / "bin" / "relkit")
        # Its own version, whichever repository the smoke happens to be started from.
        reported = _run([relkit, "--version"], {**environment, "RELKIT_DELEGATE": "0"})
        expected = f"release-kit {version}"
        if reported.returncode or not reported.stdout.startswith(expected):
            raise WheelSmokeError(
                f"installed command did not report {expected!r}: {reported.stdout.strip()!r}"
            )
        print(f"wheel-smoke: installed and ran {reported.stdout.strip()}")
        marker = "wheel-smoke pinned projection"
        pinned = _pinned_repository(work, marker)
        delegated = _run([relkit, "--version"], environment, cwd=pinned)
        if delegated.returncode or delegated.stdout.strip() != marker:
            raise WheelSmokeError(
                "installed command did not hand the invocation to the pinned projection: "
                f"{delegated.stdout.strip()!r}"
            )
        print("wheel-smoke: installed command ran the repository's pinned projection")
    finally:
        _run([uv, "tool", "uninstall", "release-kit"], environment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    check(arguments.wheel.resolve(), arguments.version, arguments.work_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
