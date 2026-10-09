"""Run a project's declared changelog generator to produce a draft for review.

A draft is not release notes. Publication reads the entry a person reviewed and
committed. The wrapper validates stdout without inserting it into the changelog or
authorizing publication. It turns history into a starting point and holds that
starting point to the same profile the published entry must satisfy. A generator
that produces an invalid layout is a misconfiguration, not a reason to relax the check.

Two adapters, and the difference between them is who vouches for what runs. A named
engine is provisioned and verified by release-kit, exactly like the scanners. Anything
else is an exact argv the project supplies, executed as written: a short name for a
third-party tool would have to guess at the operator's environment, and guessing is
what this tool refuses to do.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import processes, storage, toolchain
from .config import ChangelogConfig
from .release.changelog import normalize

TIMEOUT = 300
_DIAGNOSTIC_LIMIT = 4096
_ANSI_COLOR = re.compile(r"\x1b\[[0-9;:]*m")


class GenerationError(RuntimeError):
    """A draft could not be produced."""


def _failure_detail(stderr: bytes, stdout: bytes) -> str:
    """Keep the cause and final hint within a 4096-character diagnostic."""
    for payload in (stderr, stdout):
        detail = _ANSI_COLOR.sub("", payload.decode("utf-8", errors="replace")).strip()
        if detail:
            break
    if len(detail) <= _DIAGNOSTIC_LIMIT:
        return detail
    marker = "\n[... generator output truncated ...]\n"
    available = _DIAGNOSTIC_LIMIT - len(marker)
    head = (available + 1) // 2
    return detail[:head] + marker + detail[-(available - head) :]


def command_for(
    policy: ChangelogConfig,
    version: str,
    *,
    root: Path,
    allow_download: bool,
    from_tag: str = "",
) -> list[str]:
    generator = policy.generator
    if generator is None:
        raise GenerationError(
            "no [changelog.generator] is configured; declare an engine or a command"
        )
    if from_tag and generator.engine != "git-cliff":
        raise GenerationError(
            "--from-tag requires the git-cliff engine; custom commands run exactly as declared"
        )
    if generator.command:
        return list(generator.command)
    if from_tag:
        _check_from_tag(root, from_tag)
    try:
        executable = toolchain.resolve(generator.engine, root=root, allow_download=allow_download)
    except toolchain.ToolchainError as error:
        raise GenerationError(str(error)) from error
    # git-cliff reads its own configuration from the project, so the template stays the
    # project's to own. Without one it emits its default layout, which the profile then
    # refuses — a clearer failure than silently accepting a shape nobody chose.
    command = [str(executable), "--tag", f"v{normalize(version)}", "--output", "-", "--no-exec"]
    if from_tag:
        # Only this boundary remains a tag in the generator's view. Otherwise an
        # intermediate release candidate splits the requested range and loses its
        # changes when notes selects the final entry.
        command.extend(
            [
                "--tag-pattern",
                "^" + re.escape(from_tag) + "$",
                "--skip-tags",
                "$^",
                "--ignore-tags",
                "$^",
                "--",
                f"refs/tags/{from_tag}..HEAD",
            ]
        )
    else:
        command.append("--unreleased")
    return command


def _check_from_tag(root: Path, tag: str) -> None:
    reference = "refs/tags/" + tag
    try:
        for arguments in (
            ["check-ref-format", reference],
            ["rev-parse", "--verify", "--end-of-options", reference + "^{commit}"],
            ["merge-base", "--is-ancestor", reference, "HEAD"],
        ):
            result = processes.run(
                ["git", *arguments],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=TIMEOUT,
            )
            if result.returncode:
                raise GenerationError(
                    "--from-tag must name an existing local tag reachable from HEAD"
                )
        # git-cliff also resolves short names while constructing tag context. A
        # colliding branch or other ref can change that context even when the range
        # itself uses the verified full tag reference.
        result = processes.run(
            ["git", "rev-parse", "--symbolic-full-name", "--verify", "--end-of-options", tag],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
        )
        if result.returncode or result.stdout.rstrip(b"\r\n") != os.fsencode(reference):
            raise GenerationError("--from-tag must name an unambiguous local tag")
    except (OSError, subprocess.SubprocessError) as error:
        raise GenerationError(f"could not validate --from-tag: {error}") from error


def draft(
    policy: ChangelogConfig,
    version: str,
    *,
    root: Path,
    allow_download: bool = True,
    from_tag: str = "",
) -> str:
    root = storage.checked(root)
    command = command_for(
        policy, version, root=root, allow_download=allow_download, from_tag=from_tag
    )
    environment = None
    if policy.generator and policy.generator.engine:
        environment = dict(os.environ)
        # --output overrides the configured output file; --prepend is a separate
        # environment-controlled write that has no place in a draft operation.
        environment.pop("GIT_CLIFF_PREPEND", None)
    try:
        completed = processes.run(
            command,
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GenerationError(f"could not run {command[0]}: {error}") from error
    if completed.returncode:
        detail = _failure_detail(completed.stderr, completed.stdout)
        raise GenerationError(
            f"{Path(command[0]).name} exited {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    try:
        text = completed.stdout.decode("utf-8")
    except UnicodeError as error:
        raise GenerationError(f"{Path(command[0]).name} did not produce UTF-8 output") from error
    if not text.strip():
        raise GenerationError(
            f"{Path(command[0]).name} produced no entry for {version}; "
            "there may be no releasable commits since the last tag"
        )
    return text
