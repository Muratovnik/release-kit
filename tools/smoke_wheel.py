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
    return {
        **os.environ,
        "UV_TOOL_DIR": str(work / "tools"),
        "UV_TOOL_BIN_DIR": str(work / "bin"),
        "UV_CACHE_DIR": str(work / "cache"),
        "UV_NO_MODIFY_PATH": "1",
    }


def _run(command: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=TIMEOUT, env=environment, check=False
    )


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
        reported = _run([str(work / "bin" / "relkit"), "--version"], environment)
        expected = f"release-kit {version}"
        if reported.returncode or not reported.stdout.startswith(expected):
            raise WheelSmokeError(
                f"installed command did not report {expected!r}: {reported.stdout.strip()!r}"
            )
        print(f"wheel-smoke: installed and ran {reported.stdout.strip()}")
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
