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
import time
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path

from . import processes, storage, toolchain
from .exposure import audit
from .exposure.audit import _skip_worktree_paths, scannable_paths, worktree_paths

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdown", ".mkd", ".mdx"})
BETTERLEAKS_FINDINGS_EXIT = 10
LYCHEE_FINDINGS_EXIT = 2
LYCHEE_ARGUMENT_BYTES = 16 * 1024
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
    deadline: float | None = None,
) -> int:
    timeout = ENGINE_TIMEOUT_SECONDS if deadline is None else deadline - time.monotonic()
    if timeout <= 0:
        raise RuntimeError("publication engine timed out")
    try:
        result = processes.run(
            list(command),
            cwd=root,
            check=False,
            env=environment,
            timeout=timeout,
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
    runtime_name: str = "runtime",
    deadline: float | None = None,
) -> int:
    runtime = storage.inside(workspace.path, workspace.path / runtime_name)
    runtime.mkdir()
    workspace.remember(runtime)
    workspace.scratch(runtime)
    environment = {
        **(os.environ if environment is None else environment),
        **storage.confinement(runtime),
    }
    code = _run(command, root=root, environment=environment, deadline=deadline)
    if code not in (0, findings_exit):
        workspace.retain_scratch()
    return code


class Shared:
    """One owned workspace and one publication snapshot for every engine of a run.

    Each engine used to build its own copy of the tree, and building it was most of
    what an audit cost. The two engines read the same boundary - the index in a staged
    run, the next-add tree otherwise - so the copy is made once, on first use, and an
    engine that does not need it (history secrets, a tree without Markdown) never
    causes one.
    """

    def __init__(
        self,
        workspace: storage.Workspace | Callable[[], storage.Workspace],
        root: Path,
        *,
        staged: bool,
        include_candidates: bool,
    ):
        # A run whose engines need no files (no Markdown, no secrets scan) must not
        # require writable service storage, so the workspace may be opened on demand.
        self._workspace = workspace if isinstance(workspace, storage.Workspace) else None
        self._open = None if isinstance(workspace, storage.Workspace) else workspace
        self.root = root
        self.staged = staged
        self.include_candidates = include_candidates and not staged
        self._inventory: tuple[str, ...] | None = None
        self._source: Path | None = None

    @property
    def workspace(self) -> storage.Workspace:
        if self._workspace is None:
            assert self._open is not None
            self._workspace = self._open()
        return self._workspace

    def inventory(self) -> tuple[str, ...]:
        if self._inventory is None:
            self._inventory = (
                scannable_paths(self.root, include_candidates=False)
                if self.staged
                else worktree_paths(self.root, include_candidates=self.include_candidates)
            )
        return self._inventory

    def materialize(self, *, secrets: bool, links: bool, history: bool) -> None:
        """Build the snapshot now if an enabled engine will read it.

        Writing a thousand files is a thousand short system calls, and a thread that
        needs the interpreter lock back after each of them waits behind any other
        Python thread for every one. A caller that overlaps other work with the
        engines builds the snapshot first, while nothing else competes.
        """
        markdown = links and _markdown_paths(
            self.root,
            include_candidates=self.include_candidates,
            staged=self.staged,
            inventory=self.inventory(),
        )
        if (secrets and not history) or markdown:
            self.source()

    def source(self) -> Path:
        if self._source is None:
            snapshot = storage.inside(self.workspace.path, self.workspace.path / "source")
            snapshot.mkdir()
            if self.staged:
                _checkout_index(self.root, snapshot)
            else:
                built = _materialize_worktree(
                    self.root,
                    snapshot,
                    include_candidates=self.include_candidates,
                    candidates=self._inventory,
                )
                if self._inventory is None and built is not None:
                    self._inventory = tuple(built)
            self.workspace.remember_written(snapshot)
            self._source = snapshot
        return self._source


@contextmanager
def shared(root: Path, *, staged: bool, include_candidates: bool) -> Iterator[Shared]:
    """Engines of one run share a workspace, created only if one of them needs it."""
    with ExitStack() as stack:
        yield Shared(
            lambda: stack.enter_context(
                storage.temporary(root, "index-" if staged else "worktree-")
            ),
            root,
            staged=staged,
            include_candidates=include_candidates,
        )


def betterleaks(
    root: Path,
    *,
    config: str,
    history: bool,
    staged: bool,
    include_candidates: bool,
    allow_download: bool,
    shared: Shared | None = None,
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
    if shared is not None:
        return _betterleaks_in(
            shared,
            command,
            config=config,
            history=history,
            environment=environment,
            runtime_name="runtime-betterleaks",
        )
    with storage.temporary(root, "engine-" if staged or history else "worktree-") as workspace:
        return _betterleaks_in(
            Shared(workspace, root, staged=staged, include_candidates=include_candidates),
            command,
            config=config,
            history=history,
            environment=environment,
            runtime_name="runtime",
        )


def _betterleaks_in(
    shared: Shared,
    command: list[str],
    *,
    config: str,
    history: bool,
    environment: dict[str, str],
    runtime_name: str,
) -> int:
    root = shared.root
    working_directory = root
    if shared.staged:
        # The scanner's configuration (including relative extension files)
        # belongs to the same index as the scanned changes.
        snapshot = shared.source()
        command[command.index("--config") + 1] = str(storage.inside(snapshot, snapshot / config))
        command[command.index("git") + 1] = str(root.resolve())
        working_directory = snapshot
    elif not history:
        # Directory mode does not use Git's publication boundary and would otherwise
        # inspect .git, caches, dependencies, and ignored private mounts. Materialize
        # exactly the tracked plus untracked/unignored candidates that release-kit's
        # policy scanner sees, preserving symlinks as their published link text.
        command += ["dir", str(shared.source())]
    return _run_owned(
        shared.workspace,
        command,
        root=working_directory,
        findings_exit=BETTERLEAKS_FINDINGS_EXIT,
        environment=environment,
        runtime_name=runtime_name,
    )


def _checkout_index(root: Path, destination: Path, *, only: Collection[str] | None = None) -> None:
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
        if only is not None and relative not in only:
            continue
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
    candidates: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Build the next-add boundary: each candidate as the worktree has it.

    A sparse (skip-worktree) path is absent from the worktree by design, so its
    published bytes are the indexed ones; everything else is written once, from the
    worktree. Checking out the whole index first and then replacing almost every file
    produced the same tree at twice the writes and a removal per file. Returns the
    candidates it built, so a caller that also needs the inventory does not walk the
    worktree again.
    """
    skipped = _skip_worktree_paths(root)
    if any(audit._has_symlink_parent(root, relative) for relative in skipped):
        raise RuntimeError("a worktree symlink conflicts with a sparse index path")
    if candidates is None:
        candidates = worktree_paths(root, include_candidates=include_candidates)
    candidates = tuple(candidates)
    from_index = skipped.intersection(candidates)
    if from_index:
        _checkout_index(root, destination, only=from_index)
    modes = audit._tracked_modes(root)
    parents: set[Path] = {destination}
    for relative in candidates:
        if relative in skipped:
            continue
        source = root / relative
        target = storage.inside(destination, destination / relative)
        is_link = source.is_symlink()
        if is_link or source.is_file():
            if from_index and os.path.lexists(target):
                _clear_snapshot_path(destination, target)
            if target.parent not in parents:
                target.parent.mkdir(parents=True, exist_ok=True)
                parents.add(target.parent)
            # The inventory never follows a worktree link into private data: a
            # symlink is published as its link text.
            if is_link:
                target.write_text(os.readlink(source), encoding="utf-8")
            else:
                shutil.copyfile(source, target)
        elif modes.get(relative) == "160000" and source.is_dir():
            # The policy pass reports gitlinks as external content. Keep the empty
            # mount point for link checking without reading the external repository.
            target.mkdir(parents=True, exist_ok=True)
    return candidates


def _markdown_paths(
    root: Path,
    *,
    include_candidates: bool,
    staged: bool = False,
    inventory: Sequence[str] | None = None,
) -> tuple[str, ...]:
    if inventory is None:
        inventory = (scannable_paths if staged else worktree_paths)(
            root, include_candidates=include_candidates
        )
    return tuple(
        relative for relative in inventory if Path(relative).suffix.lower() in MARKDOWN_SUFFIXES
    )


def _lychee_inputs(paths: Sequence[str]) -> tuple[list[str], list[list[str]]]:
    """Preserve literal paths through Lychee's line and glob input grammar."""
    file_inputs: list[str] = []
    argument_batches: list[list[str]] = [[]]
    used = 0
    for path in paths:
        # Prefixing also prevents comment, whitespace, URL and option parsing.
        # Lychee treats glob characters as patterns even when the file exists.
        literal = "./" + "".join(f"[{char}]" if char in "*?[]" else char for char in path)
        if "\n" not in path and "\r" not in path:
            file_inputs.append(literal)
            continue
        # Only line-breaking names need argv. Keep the ordinary file list scalable
        # and leave room for argument pointers/quoting within each small batch.
        size = len(os.fsencode(literal)) + 64
        if argument_batches[-1] and used + size > LYCHEE_ARGUMENT_BYTES:
            argument_batches.append([])
            used = 0
        argument_batches[-1].append(literal)
        used += size
    return file_inputs, argument_batches


def lychee(
    root: Path,
    *,
    staged: bool,
    include_candidates: bool,
    allow_download: bool,
    shared: Shared | None = None,
) -> int:
    executable = toolchain.resolve("lychee", root=root, allow_download=allow_download)
    paths = _markdown_paths(
        root,
        include_candidates=include_candidates and not staged,
        staged=staged,
        inventory=None if shared is None else shared.inventory(),
    )
    if not paths:
        return 0
    if shared is not None:
        return _lychee_in(shared, executable, paths, runtime_name="runtime-lychee")
    with storage.temporary(root, "index-" if staged else "worktree-") as workspace:
        return _lychee_in(
            Shared(workspace, root, staged=staged, include_candidates=include_candidates),
            executable,
            paths,
            runtime_name="runtime",
        )


def _lychee_in(shared: Shared, executable: Path, paths: Sequence[str], *, runtime_name: str) -> int:
    workspace = shared.workspace
    snapshot = shared.source()
    file_inputs, argument_batches = _lychee_inputs(paths)
    inputs = storage.inside(workspace.path, workspace.path / "markdown-inputs.txt")
    with inputs.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(file_inputs) + "\n")
    workspace.remember(inputs)
    base_command = [
        str(executable),
        "--offline",
        "--no-progress",
        "--mode",
        "plain",
    ]
    deadline = time.monotonic() + ENGINE_TIMEOUT_SECONDS
    verdict = 0
    for index, arguments in enumerate(argument_batches):
        command = base_command.copy()
        if index == 0 and file_inputs:
            command += ["--files-from", str(inputs)]
        if arguments:
            command += ["--", *arguments]
        code = _run_owned(
            workspace,
            command,
            root=snapshot,
            findings_exit=LYCHEE_FINDINGS_EXIT,
            runtime_name=runtime_name if index == 0 else f"{runtime_name}-{index}",
            deadline=deadline,
        )
        if code not in (0, LYCHEE_FINDINGS_EXIT):
            return code
        if code == LYCHEE_FINDINGS_EXIT:
            verdict = code
    return verdict
