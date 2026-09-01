"""Adapters for maintained secret and link engines.

This module intentionally contains command construction, not parsing. Betterleaks and
Lychee own their findings and exit codes; release-kit supplies a verified executable,
the correct Git scope, and one stable command for adopters.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from . import storage, toolchain
from .exposure.audit import _skip_worktree_paths, scannable_paths, worktree_paths

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdown", ".mkd", ".mdx"})
HISTORY_LOG_OPTS = (
    "HEAD --branches --remotes --tags "
    "--glob=refs/pull/* --glob=refs/merge-requests/* --glob=refs/changes/* "
    "--glob=refs/notes/*"
)


def _run(
    command: Sequence[str],
    *,
    root: Path,
    stdin: str | None = None,
    environment: dict[str, str] | None = None,
) -> int:
    try:
        result = subprocess.run(
            list(command),
            cwd=root,
            input=stdin,
            check=False,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=600,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("publication engine timed out") from error
    return result.returncode


def betterleaks(
    root: Path,
    *,
    config: str,
    history: bool,
    staged: bool,
    include_candidates: bool,
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
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        count = int(environment.get("GIT_CONFIG_COUNT", "0"))
    except ValueError as error:
        raise RuntimeError("GIT_CONFIG_COUNT is not a valid integer") from error
    if count < 0:
        raise RuntimeError("GIT_CONFIG_COUNT must not be negative")
    environment["GIT_CONFIG_COUNT"] = str(count + 1)
    environment[f"GIT_CONFIG_KEY_{count}"] = "safe.directory"
    environment[f"GIT_CONFIG_VALUE_{count}"] = str(root.resolve())
    if staged or history:
        with storage.temporary(root, "engine-") as workspace:
            environment.update({key: str(workspace.path) for key in ("TMP", "TEMP", "TMPDIR")})
            return _run(command, root=root, environment=environment)

    # Directory mode does not use Git's publication boundary and would otherwise
    # inspect .git, caches, dependencies, and ignored private mounts. Materialize
    # exactly the tracked plus untracked/unignored candidates that release-kit's
    # policy scanner sees, preserving symlinks as their published link text.
    with storage.temporary(root, "worktree-") as workspace:
        snapshot = workspace.path
        _materialize_worktree(root, snapshot, include_candidates=include_candidates)
        workspace.remember()
        environment.update({key: str(snapshot) for key in ("TMP", "TEMP", "TMPDIR")})
        command += ["dir", str(snapshot)]
        return _run(command, root=root, environment=environment)


def _checkout_index(root: Path, destination: Path) -> None:
    prefix = destination.as_posix().rstrip("/") + "/"
    try:
        result = subprocess.run(
            [
                "git",
                "checkout-index",
                "--all",
                "--force",
                "--ignore-skip-worktree-bits",
                f"--prefix={prefix}",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Git index checkout timed out") from error
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git checkout-index failed")
    # Preserve a tracked link as published text, never let an engine follow it
    # outside the owned snapshot. Git may create links when core.symlinks is true.
    for item in destination.rglob("*"):
        if item.is_symlink():
            storage.checked(item.parent)
            blob = subprocess.run(
                ["git", "cat-file", "blob", ":" + item.relative_to(destination).as_posix()],
                cwd=root,
                check=False,
                capture_output=True,
                timeout=120,
            )
            if blob.returncode:
                raise RuntimeError("could not read the indexed link text")
            item.unlink()
            item.write_bytes(blob.stdout)


def _clear_snapshot_path(snapshot: Path, destination: Path) -> None:
    try:
        destination.relative_to(snapshot)
    except ValueError as error:
        raise RuntimeError("snapshot path escaped its temporary root") from error
    storage.inside(snapshot, destination)
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.is_dir():
        shutil.rmtree(destination)


def _materialize_worktree(
    root: Path,
    destination: Path,
    *,
    include_candidates: bool,
) -> None:
    """Build the next-add boundary from the index plus actual worktree changes."""
    _checkout_index(root, destination)
    tracked = set(scannable_paths(root, include_candidates=False))
    skipped = _skip_worktree_paths(root)
    for relative in scannable_paths(root, include_candidates=include_candidates):
        source = root / relative
        target = destination / relative
        if source.is_symlink() or source.is_file():
            _clear_snapshot_path(destination, target)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.write_text(os.readlink(source), encoding="utf-8")
            else:
                shutil.copyfile(source, target)
        elif relative in tracked and relative not in skipped:
            _clear_snapshot_path(destination, target)


def _markdown_paths(
    root: Path, *, include_candidates: bool, staged: bool = False
) -> tuple[str, ...]:
    inventory = scannable_paths if staged else worktree_paths
    return tuple(
        relative
        for relative in inventory(root, include_candidates=include_candidates)
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
    paths = _markdown_paths(
        root,
        include_candidates=include_candidates and not staged,
        staged=staged,
    )
    if not paths:
        return 0
    if not staged:
        with storage.temporary(root, "worktree-") as workspace:
            snapshot = workspace.path
            _materialize_worktree(
                root,
                snapshot,
                include_candidates=include_candidates,
            )
            workspace.remember()
            command = [
                str(executable),
                "--offline",
                "--no-progress",
                "--mode",
                "plain",
                "--files-from",
                "-",
            ]
            return _run(
                command,
                root=snapshot,
                stdin="\n".join(paths) + "\n",
                environment=workspace.environment(),
            )

    with storage.temporary(root, "index-") as workspace:
        snapshot = workspace.path
        _checkout_index(root, snapshot)
        workspace.remember()
        command = [
            str(executable),
            "--offline",
            "--no-progress",
            "--mode",
            "plain",
            "--files-from",
            "-",
        ]
        return _run(
            command,
            root=snapshot,
            stdin="\n".join(paths) + "\n",
            environment=workspace.environment(),
        )
