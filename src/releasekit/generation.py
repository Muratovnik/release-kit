"""Run a project's declared changelog generator to produce a draft for review.

A draft is not release notes. Publication reads the entry a person reviewed and
committed, so this writes nothing into the changelog and authorizes nothing: it turns
history into a starting point and then holds that starting point to the same profile
the published entry must satisfy. A generator that produces an invalid layout is a
misconfiguration, not a reason to relax the check.

Two adapters, and the difference between them is who vouches for what runs. A named
engine is provisioned and verified by release-kit, exactly like the scanners. Anything
else is an exact argv the project supplies, executed as written: a short name for a
third-party tool would have to guess at the operator's environment, and guessing is
what this tool refuses to do.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import storage, toolchain
from .config import ChangelogConfig
from .release.changelog import normalize

TIMEOUT = 300


class GenerationError(RuntimeError):
    """A draft could not be produced."""


def command_for(
    policy: ChangelogConfig, version: str, *, root: Path, allow_download: bool
) -> list[str]:
    generator = policy.generator
    if generator is None:
        raise GenerationError(
            "no [changelog.generator] is configured; declare an engine or a command"
        )
    if generator.command:
        return list(generator.command)
    try:
        executable = toolchain.resolve(generator.engine, root=root, allow_download=allow_download)
    except toolchain.ToolchainError as error:
        raise GenerationError(str(error)) from error
    # git-cliff reads its own configuration from the project, so the template stays the
    # project's to own. Without one it emits its default layout, which the profile then
    # refuses — a clearer failure than silently accepting a shape nobody chose.
    return [str(executable), "--unreleased", "--tag", f"v{normalize(version)}"]


def draft(policy: ChangelogConfig, version: str, *, root: Path, allow_download: bool = True) -> str:
    root = storage.checked(root)
    command = command_for(policy, version, root=root, allow_download=allow_download)
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GenerationError(f"could not run {command[0]}: {error}") from error
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise GenerationError(
            f"{Path(command[0]).name} exited {completed.returncode}"
            + (f": {detail[-1]}" if detail else "")
        )
    if not completed.stdout.strip():
        raise GenerationError(
            f"{Path(command[0]).name} produced no entry for {version}; "
            "there may be no releasable commits since the last tag"
        )
    return completed.stdout
