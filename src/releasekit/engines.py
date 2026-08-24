"""Adapters for maintained secret and link engines.

This module intentionally contains command construction, not parsing. Betterleaks and
Lychee own their findings and exit codes; release-kit supplies a verified executable,
the correct Git scope, and one stable command for adopters.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from . import toolchain
from .exposure.audit import scannable_paths

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdown", ".mkd", ".mdx"})
HISTORY_LOG_OPTS = "HEAD --branches --remotes --tags"


def _run(
    command: Sequence[str],
    *,
    root: Path,
    stdin: str | None = None,
    environment: dict[str, str] | None = None,
) -> int:
    result = subprocess.run(
        list(command),
        cwd=root,
        input=stdin,
        check=False,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=600,
    )
    return result.returncode


def betterleaks(
    root: Path,
    *,
    config: str,
    history: bool,
    staged: bool,
    allow_download: bool,
) -> int:
    executable = toolchain.resolve("betterleaks", root=root, allow_download=allow_download)
    config_path = root / config
    command = [
        str(executable),
        "--no-banner",
        "--no-color",
        "--redact",
        "--verbose",
        "--config",
        str(config_path),
    ]
    if staged:
        command += ["git", str(root), "--pre-commit", "--staged"]
    elif history:
        command += ["git", str(root)]
        # A product's publishable history is HEAD plus local/remote branches and tags. Desktop
        # clients may keep synthetic checkpoint refs whose objects are pruned
        # independently; Betterleaks' default --all traversal then fails before
        # reaching product history. Limit the scan explicitly without weakening
        # the public ref boundary.
        command += [f"--log-opts={HISTORY_LOG_OPTS}"]
    # Betterleaks invokes Git as a child process. Desktop/container workspaces can
    # legitimately be owned by the host account rather than the current process
    # account; scope the exception to this exact audited root instead of mutating
    # global Git configuration or accepting every path.
    environment = os.environ.copy()
    count = int(environment.get("GIT_CONFIG_COUNT", "0"))
    environment["GIT_CONFIG_COUNT"] = str(count + 1)
    environment[f"GIT_CONFIG_KEY_{count}"] = "safe.directory"
    environment[f"GIT_CONFIG_VALUE_{count}"] = str(root.resolve())
    if staged or history:
        return _run(command, root=root, environment=environment)

    # Directory mode does not use Git's publication boundary and would otherwise
    # inspect .git, caches, dependencies, and ignored private mounts. Materialize
    # exactly the tracked plus untracked/unignored candidates that release-kit's
    # policy scanner sees, preserving symlinks as their published link text.
    with tempfile.TemporaryDirectory(prefix="relkit-worktree-") as temporary:
        snapshot = Path(temporary)
        for relative in scannable_paths(root, include_candidates=True):
            source = root / relative
            destination = snapshot / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                destination.write_text(os.readlink(source), encoding="utf-8")
            elif source.is_file():
                shutil.copyfile(source, destination)
        command += ["dir", str(snapshot)]
        return _run(command, root=root, environment=environment)


def _checkout_index(root: Path, destination: Path) -> None:
    prefix = destination.as_posix().rstrip("/") + "/"
    result = subprocess.run(
        ["git", "checkout-index", "--all", "--force", f"--prefix={prefix}"],
        cwd=root,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git checkout-index failed")


def _markdown_paths(root: Path, *, include_candidates: bool) -> tuple[str, ...]:
    return tuple(
        relative
        for relative in scannable_paths(root, include_candidates=include_candidates)
        if Path(relative).suffix.lower() in MARKDOWN_SUFFIXES
    )


def lychee(
    root: Path,
    *,
    staged: bool,
    include_candidates: bool,
    allow_download: bool,
) -> int:
    executable = toolchain.resolve("lychee", root=root, allow_download=allow_download)
    paths = _markdown_paths(root, include_candidates=include_candidates and not staged)
    if not paths:
        return 0
    if not staged:
        command = [
            str(executable),
            "--offline",
            "--no-progress",
            "--mode",
            "plain",
            "--files-from",
            "-",
        ]
        return _run(command, root=root, stdin="\n".join(paths) + "\n")

    with tempfile.TemporaryDirectory(prefix="relkit-index-") as temporary:
        snapshot = Path(temporary)
        _checkout_index(root, snapshot)
        command = [
            str(executable),
            "--offline",
            "--no-progress",
            "--mode",
            "plain",
            "--files-from",
            "-",
        ]
        return _run(command, root=snapshot, stdin="\n".join(paths) + "\n")
