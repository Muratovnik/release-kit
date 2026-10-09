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

from . import processes, storage, toolchain
from .exposure import audit
from .exposure.audit import _skip_worktree_paths, scannable_paths, worktree_paths

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdown", ".mkd", ".mdx"})
BETTERLEAKS_FINDINGS_EXIT = 10
LYCHEE_FINDINGS_EXIT = 2
ENGINE_TIMEOUT_SECONDS = 600
HISTORY_LOG_OPTS = (
    "HEAD --branches --remotes --tags "
    "--glob=refs/pull/* --glob=refs/merge-requests/* --glob=refs/changes/* "
    "--glob=refs/notes/*"
)


def _run(
    command: Sequence[str],
    *,
    root: Path,
    environment: dict[str, str] | None = None,
) -> int:
    try:
        result = processes.run(
            list(command),
            cwd=root,
            check=False,
            env=environment,
            timeout=ENGINE_TIMEOUT_SECONDS,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("publication engine timed out") from error
    return result.returncode


def _run_owned(
    workspace: storage.Workspace,
    command: Sequence[str],
    *,
    root: Path,
    findings_exit: int,
    environment: dict[str, str] | None = None,
) -> int:
    runtime = storage.inside(workspace.path, workspace.path / "runtime")
    runtime.mkdir()
    workspace.remember(runtime)
    workspace.scratch(runtime)
    environment = {
        **(os.environ if environment is None else environment),
        **storage.confinement(runtime),
    }
    code = _run(command, root=root, environment=environment)
    if code not in (0, findings_exit):
        workspace.retain_scratch()
    return code


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
        "--exit-code",
        str(BETTERLEAKS_FINDINGS_EXIT),
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
            working_directory = root
            if staged:
                # The scanner's configuration (including relative extension files)
                # belongs to the same index as the scanned changes.
                snapshot = workspace.path / "source"
                snapshot.mkdir()
                _checkout_index(root, snapshot)
                workspace.remember(snapshot)
                command[command.index("--config") + 1] = str(
                    storage.inside(snapshot, snapshot / config)
                )
                command[command.index("git") + 1] = str(root.resolve())
                working_directory = snapshot
            return _run_owned(
                workspace,
                command,
                root=working_directory,
                findings_exit=BETTERLEAKS_FINDINGS_EXIT,
                environment=environment,
            )

    # Directory mode does not use Git's publication boundary and would otherwise
    # inspect .git, caches, dependencies, and ignored private mounts. Materialize
    # exactly the tracked plus untracked/unignored candidates that release-kit's
    # policy scanner sees, preserving symlinks as their published link text.
    with storage.temporary(root, "worktree-") as workspace:
        snapshot = workspace.path / "source"
        snapshot.mkdir()
        _materialize_worktree(root, snapshot, include_candidates=include_candidates)
        workspace.remember(snapshot)
        command += ["dir", str(snapshot)]
        return _run_owned(
            workspace,
            command,
            root=root,
            findings_exit=BETTERLEAKS_FINDINGS_EXIT,
            environment=environment,
        )


def _checkout_index(root: Path, destination: Path) -> None:
    # A checkout applies repository-defined smudge filters and line-ending
    # conversion. Read Git's blobs directly so the scan sees the indexed bytes
    # without executing filters or downloading their external content.
    result = audit._git_bytes(root, ["ls-files", "--stage", "--cached", "-z"])
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    entries: dict[str, list[tuple[str, str]]] = {}
    gitlinks: list[str] = []
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        metadata, separator, raw_path = entry.partition(b"\t")
        fields = metadata.decode("ascii").split()
        if not separator or len(fields) != 3:
            raise RuntimeError("git ls-files returned a malformed index entry")
        mode, object_id, stage = fields
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        if stage != "0":
            raise RuntimeError("cannot scan an unmerged index")
        if mode == "160000":
            gitlinks.append(relative)
        elif mode in {"100644", "100755", "120000"}:
            entries.setdefault(object_id, []).append((relative, mode))
        else:
            raise RuntimeError("git ls-files returned an unsupported index mode")
    if entries:
        sizes = audit._git(
            root,
            ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
            stdin="\n".join(entries) + "\n",
        )
        if sizes.returncode:
            raise RuntimeError(sizes.stderr.strip() or "git cat-file --batch-check failed")
        sized: list[tuple[str, int]] = []
        for line in sizes.stdout.splitlines():
            fields = line.split()
            if len(fields) != 3 or fields[1] != "blob" or fields[0] not in entries:
                raise RuntimeError("git cat-file returned an unexpected index object")
            sized.append((fields[0], int(fields[2])))
        if {object_id for object_id, _ in sized} != entries.keys():
            raise RuntimeError("git cat-file did not describe every indexed blob")
        for batch in audit._history_batches(sized):
            for object_id, payload in audit._batch_blobs(root, batch):
                for relative, mode in entries[object_id]:
                    target = storage.inside(destination, destination / relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # Symlinks remain their published text, never live links that
                    # let an engine traverse outside its owned snapshot.
                    with target.open("xb") as stream:
                        stream.write(payload)
                    target.chmod(0o755 if mode == "100755" else 0o644)
    # The policy pass reports gitlinks as external content. Keep the empty mount
    # point for link checking without reading the external repository.
    for relative in gitlinks:
        storage.inside(destination, destination / relative).mkdir(parents=True, exist_ok=True)


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
    parent = destination.parent
    while parent != snapshot and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def _materialize_worktree(
    root: Path,
    destination: Path,
    *,
    include_candidates: bool,
) -> None:
    """Build the next-add boundary from the index plus actual worktree changes."""
    skipped = _skip_worktree_paths(root)
    if any(audit._has_symlink_parent(root, relative) for relative in skipped):
        raise RuntimeError("a worktree symlink conflicts with a sparse index path")
    _checkout_index(root, destination)
    tracked = set(scannable_paths(root, include_candidates=False))
    candidates = worktree_paths(root, include_candidates=include_candidates)
    # Remove obsolete children before writing a file or link that replaces their
    # directory. The inventory never follows the worktree link into private data.
    for relative in sorted(tracked - set(candidates)):
        _clear_snapshot_path(destination, destination / relative)
    for relative in candidates:
        if relative in skipped:
            continue
        source = root / relative
        target = destination / relative
        if source.is_symlink() or source.is_file():
            _clear_snapshot_path(destination, target)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.write_text(os.readlink(source), encoding="utf-8")
            else:
                shutil.copyfile(source, target)


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
    with storage.temporary(root, "index-" if staged else "worktree-") as workspace:
        snapshot = workspace.path / "source"
        snapshot.mkdir()
        if staged:
            _checkout_index(root, snapshot)
        else:
            _materialize_worktree(
                root,
                snapshot,
                include_candidates=include_candidates,
            )
        workspace.remember(snapshot)
        inputs = storage.inside(workspace.path, workspace.path / "markdown-inputs.txt")
        with inputs.open("x", encoding="utf-8") as stream:
            stream.write("\n".join(paths) + "\n")
        workspace.remember(inputs)
        command = [
            str(executable),
            "--offline",
            "--no-progress",
            "--mode",
            "plain",
            "--files-from",
            str(inputs),
        ]
        return _run_owned(
            workspace,
            command,
            root=snapshot,
            findings_exit=LYCHEE_FINDINGS_EXIT,
        )
