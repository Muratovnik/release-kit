"""The installed `relkit` command: run the projection a repository pins, else this CLI.

A repository that commits `.github/relkit.pyz` has decided which release-kit judges
it; its hooks and CI run those bytes. A machine-installed command answering with its
own version would give that repository a second, possibly different verdict, and
every hint that says `relkit ...` would be wrong wherever the pinned form is used.
So the installed command hands the whole invocation to the pinned projection when
there is one and runs itself only when there is not. Without an explicit `--root` it
runs the projection from the repository root, as the hooks do, because every command
takes `.` as its repository and a subdirectory is not one.

The pinned projection is repository code, and it runs without review, the way a
Gradle wrapper does. `RELKIT_DELEGATE=0` keeps the installed version, for example to
audit a checkout whose projection is not trusted.

Only the console script comes here. The zipapp's entry point stays `cli.main`, so the
projection that receives an invocation can never hand it on again.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from . import cli, processes, protection

ENVIRONMENT = "RELKIT_DELEGATE"


def _root(argv: Sequence[str]) -> str | None:
    """The repository an invocation names with `--root`, if it names one."""
    options = argv[: list(argv).index("--")] if "--" in argv else argv
    for index, argument in enumerate(options):
        if argument == "--root" and index + 1 < len(options):
            return options[index + 1]
        if argument.startswith("--root="):
            return argument.removeprefix("--root=")
    return None


def pinned_projection(
    argv: Sequence[str], environment: dict[str, str], cwd: Path | None = None
) -> Path | None:
    """The projection pinned by the repository this invocation works on, if any."""
    if environment.get(ENVIRONMENT) == "0":
        return None
    root = Path(cwd or Path.cwd()) / (_root(argv) or ".")
    try:
        found = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # No Git, or no such directory: this CLI reports that itself, as before.
        return None
    if found.returncode:
        return None
    projection = Path(found.stdout.strip()) / protection.PROJECTION_PATH
    return projection if projection.is_file() else None


def _run(projection: Path, argv: Sequence[str]) -> int:
    # An explicit --root may be relative to where it was typed; otherwise the
    # repository the projection pins is the root, however deep the shell stands.
    repository = projection.parent.parent
    cwd = None if _root(argv) is not None else repository
    # The interrupt reaches the child too; it decides how to stop, and killing it here
    # would cut short the cleanup it reports on.
    lifetime = None
    try:
        options = {}
        if os.name != "nt":
            lifetime = processes.LifetimePipe(None)
            options = {"pass_fds": lifetime.descriptors, "env": lifetime.environment}
        process = subprocess.Popen([sys.executable, str(projection), *argv], cwd=cwd, **options)
        if lifetime is not None:
            lifetime.spawned()
        while True:
            try:
                code = process.wait()
            except KeyboardInterrupt:
                continue
            if lifetime is not None and not lifetime.finished():
                raise processes.CleanupError(
                    "projection process cleanup is unconfirmed; inspect retained state"
                )
            # A POSIX child killed by a signal reports -N; a shell reports that as 128+N.
            return 128 - code if code < 0 else code
    finally:
        if lifetime is not None:
            lifetime.close()


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    projection = pinned_projection(argv, dict(os.environ))
    if projection is None:
        return cli.main(argv)
    try:
        return _run(projection, argv)
    except processes.CleanupError as error:
        print(f"relkit: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
