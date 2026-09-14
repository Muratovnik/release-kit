"""Project-local service files and conservative, inventory-based cleanup.

This is ownership checking, not a sandbox for arbitrary project commands. External
cache writes require an explicit, exact path approval in the invoking environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .processes import CleanupError


class StorageError(RuntimeError):
    """An owned path cannot safely be used."""


# Directories this process already inspected. A repository has a handful of ancestors
# and thousands of service paths beneath them, so the same parents were lstat-ed tens
# of thousands of times in one run. Only directories are remembered: a missing path may
# appear later, and a regular file can gain a hard link after it is written. This makes
# no promise across runs and adds no trust in metadata - it declines to re-ask about a
# directory already inspected in this process.
_VERIFIED_DIRECTORIES: set[str] = set()


def forget_verified_paths() -> None:
    """Drop the per-process directory cache; a test reshaping a tree in place needs it."""
    _VERIFIED_DIRECTORIES.clear()


def checked(path: Path) -> Path:
    """Reject links, junctions, hard-linked files and path aliases before writes."""
    path = Path(os.path.abspath(path))
    for item in (*reversed(path.parents), path):
        key = str(item)
        if key in _VERIFIED_DIRECTORIES:
            continue
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise StorageError(f"service path contains a link or junction: {item}")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise StorageError(f"service path is hard-linked: {item}")
        if stat.S_ISDIR(info.st_mode):
            _VERIFIED_DIRECTORIES.add(key)
    return path


def inside(root: Path, path: Path) -> Path:
    root, path = checked(root), checked(path)
    if not path.is_relative_to(root) or path == root:
        raise StorageError(f"service path must stay inside the project: {path}")
    return path


def service_root(root: Path) -> Path:
    root = checked(root)
    metadata = root / ".git"
    if metadata.is_dir():
        return inside(root, metadata / "relkit")
    # Linked worktrees must not put temporary data in the other checkout's .git.
    local = inside(root, root / ".cache" / "release-kit")
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", ".cache/release-kit/"],
        cwd=root,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise StorageError("ignore .cache/release-kit/ in this project before using local storage")
    return local


def cache_write_path(root: Path, path: Path) -> Path:
    """An inherited cache location is not by itself permission to write outside."""
    root, path = checked(root), checked(path)
    cache = Path(os.environ.get("RELKIT_CACHE_DIR", root / ".cache" / "release-kit"))
    if not cache.is_absolute():
        cache = root / cache
    cache = checked(cache)
    if not path.is_relative_to(cache):
        raise StorageError("write escaped the configured cache")
    if path != root and path.is_relative_to(root):
        if (root / ".git").exists():
            ignored = subprocess.run(
                ["git", "check-ignore", "-q", "--", cache.relative_to(root).as_posix() + "/"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
            )
            if ignored.returncode:
                raise StorageError(
                    "ignore the project-local release-kit cache before provisioning engines"
                )
        return path
    approved = os.environ.get("RELKIT_APPROVED_EXTERNAL_CACHE", "")
    if (
        not approved
        or not Path(approved).is_absolute()
        or checked(Path(approved)) != checked(cache)
    ):
        raise StorageError(
            "external cache writes need explicit approval of its exact absolute path via "
            "RELKIT_APPROVED_EXTERNAL_CACHE, or unset RELKIT_CACHE_DIR to use project storage"
        )
    return path


def digest(path: Path) -> str:
    with checked(path).open("rb") as stream:
        value = hashlib.file_digest(stream, "sha256").hexdigest()
    return value


def identity(path: Path) -> tuple[int, int, int]:
    info = checked(path).stat()
    return info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)


# Only these move Git's directory, index or object store. The rest of the GIT_*
# namespace describes how Git talks to its operator (pager, askpass, ssh command,
# committer identity); refusing those rejects ordinary workstations for nothing.
GIT_LOCATION_OVERRIDES = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_WORK_TREE",
)


def redirected_git() -> list[str]:
    """Inherited variables that would silently point Git at another repository."""
    return sorted(name for name in GIT_LOCATION_OVERRIDES if os.environ.get(name))


def confinement(path: Path) -> dict[str, str]:
    """Point every scratch and cache variable a child may honour at one owned path."""
    local = str(checked(path))
    return {
        **{
            key: local
            for key in (
                "TMP",
                "TEMP",
                "TMPDIR",
                "XDG_CACHE_HOME",
                "XDG_STATE_HOME",
                "XDG_DATA_HOME",
            )
        },
        "PYTHONDONTWRITEBYTECODE": "1",
        "GH_NO_UPDATE_NOTIFIER": "1",
    }


def environment(path: Path) -> dict[str, str]:
    return {**os.environ, **confinement(path)}


# Windows can refuse a rename for a moment after the bytes are written, and a
# receipt is written at exactly the points a release must not lose.
REPLACE_ATTEMPTS = 6
REPLACE_BACKOFF = 0.05


def _replace(temporary: Path, path: Path) -> None:
    """Rename over the destination, tolerating a brief refusal.

    Nothing of ours holds either name by now: the descriptor is flushed, synced and
    closed above. A scanner opening the file it has just seen created is enough for
    Windows to answer `replace` with WinError 5, and that is what interrupted a
    release of this repository between publishing and recording its receipt. Retry
    briefly; a destination something genuinely holds still raises the same error.
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_BACKOFF * (attempt + 1))


def atomic_json(path: Path, value: object) -> None:
    path = checked(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        checked(path)
        _replace(temporary, path)
    finally:
        checked(temporary).unlink(missing_ok=True)


def _remove(path: Path, *, directory: bool) -> None:
    """Remove one entry, clearing the read-only bit Windows refuses to unlink through.

    A run that built a Git repository left its object files read-only, which is how
    Git writes them, and on Windows that is enough for `unlink` to answer WinError 5.
    The bit is cleared on the entry being removed, never on anything it points at.
    """
    action = path.rmdir if directory else path.unlink
    try:
        action()
    except PermissionError:
        path.chmod(stat.S_IWRITE)
        action()


def _discard_contents(path: Path) -> None:
    """Empty a directory this run owns whole, never following a link or junction.

    `os.walk` is not usable here: it decides what to descend into with `is_symlink`,
    which is false for a Windows junction, so it would walk into the target and
    delete another directory's contents. Every entry is classified from its own
    `lstat` instead, and a reparse point is removed as the link it is.
    """
    for entry in path.iterdir():
        info = entry.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode):
            entry.unlink()
        elif attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            _remove(entry, directory=bool(attributes & stat.FILE_ATTRIBUTE_DIRECTORY))
        elif stat.S_ISDIR(info.st_mode):
            _discard_contents(entry)
            _remove(entry, directory=True)
        else:
            _remove(entry, directory=False)


def discard_tree(path: Path) -> None:
    """Remove a directory the caller owns whole, never following a link or junction."""
    path = checked(path)
    _discard_contents(path)
    _remove(path, directory=True)


# A workspace a run left behind is a diagnostic while the failure is fresh, and after
# that it is a directory nothing will ever read again. Two weeks outlives an
# investigation and is far longer than any run, so a live workspace is never a
# candidate, and receipts live beside `tmp`, never inside it.
TEMPORARY_RETENTION_DAYS = 14


def _retention_days() -> float:
    configured = os.environ.get("RELKIT_TEMPORARY_RETENTION_DAYS", "").strip()
    if not configured:
        return TEMPORARY_RETENTION_DAYS
    try:
        return float(configured)
    except ValueError:
        return TEMPORARY_RETENTION_DAYS


def prune_temporaries(parent: Path, *, now: float | None = None) -> list[Path]:
    """Discard workspaces older than the retention window and report what went.

    Conservative cleanup keeps whatever a run did not inventory, which is correct
    for one run and unbounded across many: nothing else ever removed these, so a
    project accumulated them until someone noticed the disk. Age is the only signal
    used, and a failure outlives the window it plausibly needs.
    """
    days = _retention_days()
    if days < 0:
        return []
    cutoff = (time.time() if now is None else now) - days * 86400
    removed: list[Path] = []
    try:
        entries = sorted(parent.iterdir())
    except OSError:
        return removed
    for entry in entries:
        try:
            info = entry.lstat()
            attributes = getattr(info, "st_file_attributes", 0)
            # Never age out something reached through a link: it is not ours to time.
            if stat.S_ISLNK(info.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                continue
            if info.st_mtime >= cutoff:
                continue
            if stat.S_ISDIR(info.st_mode):
                _discard_contents(entry)
                _remove(entry, directory=True)
            else:
                _remove(entry, directory=False)
        except OSError:
            # A workspace still held open is simply not removed; the run matters more.
            continue
        removed.append(entry)
    return removed


class Workspace:
    """Only files inventoried before a consumer runs are eligible for removal."""

    def __init__(self, root: Path, prefix: str = "run-"):
        parent = service_root(root) / "tmp"
        checked(parent).mkdir(parents=True, exist_ok=True)
        if expired := prune_temporaries(parent):
            print(
                f"relkit: discarded {len(expired)} temporary workspace(s) older than "
                f"{_retention_days():g} days",
                file=sys.stderr,
            )
        self.path = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
        self.identity = identity(self.path)
        self.files: dict[str, tuple[str, tuple[int, int, int]]] = {}
        self.directories: dict[str, tuple[int, int, int]] = {}
        self.scratches: dict[str, tuple[int, int, int]] = {}

    def remember(self, path: Path | None = None) -> None:
        target = inside(self.path, path) if path is not None else checked(self.path)
        for parent in (target if target.is_dir() else target.parent, *target.parents):
            if parent == self.path:
                break
            self.directories.setdefault(parent.relative_to(self.path).as_posix(), identity(parent))
        if target.is_file():
            self.files.setdefault(
                target.relative_to(self.path).as_posix(), (digest(target), identity(target))
            )
            return
        for directory, dirs, files in os.walk(target, followlinks=False):
            for name in dirs:
                item = inside(self.path, Path(directory) / name)
                self.directories.setdefault(item.relative_to(self.path).as_posix(), identity(item))
            for name in files:
                item = inside(self.path, Path(directory) / name)
                self.files.setdefault(
                    item.relative_to(self.path).as_posix(), (digest(item), identity(item))
                )

    def scratch(self, path: Path) -> None:
        """Declare a directory this run creates only to be another process's temporary one.

        `remember` inventories what this tool wrote, so a child's output is correctly
        unknown to it and survives. A directory whose entire purpose is to be handed
        to a child as its TMP is different: nothing in it is meant to outlive the run.
        Leaving it retained the child's package cache on every successful release,
        hundreds of megabytes each, which nothing ever removed. Ownership is still
        declared before the consumer runs and only a successful run discards it.
        """
        target = inside(self.path, path)
        self.scratches[target.relative_to(self.path).as_posix()] = identity(target)

    def cleanup(self, *, discard_scratch: bool = False) -> bool:
        checked(self.path)
        if identity(self.path) != self.identity:
            raise StorageError("temporary directory identity changed; refusing cleanup")
        if discard_scratch:
            for relative, expected in self.scratches.items():
                item = inside(self.path, self.path / relative)
                # A replaced directory is a different one; only what this run made is ours.
                if item.is_dir() and identity(item) == expected:
                    _discard_contents(item)
                    _remove(item, directory=True)
        for relative, expected in self.files.items():
            item = inside(self.path, self.path / relative)
            if item.is_file() and (digest(item), identity(item)) == expected:
                item.unlink()
        for relative in sorted(self.directories, key=lambda value: value.count("/"), reverse=True):
            item = inside(self.path, self.path / relative)
            if (
                item.is_dir()
                and identity(item) == self.directories[relative]
                and not any(item.iterdir())
            ):
                item.rmdir()
        if any(self.path.iterdir()):
            return False
        self.path.rmdir()
        return True

    def environment(self) -> dict[str, str]:
        return environment(self.path)


@contextmanager
def temporary(root: Path, prefix: str):
    workspace = Workspace(root, prefix)
    unconfirmed = False
    failed = False
    try:
        yield workspace
    except CleanupError:
        unconfirmed = True
        failed = True
        raise
    except BaseException:
        failed = True
        raise
    finally:
        try:
            # Scratch is discarded only on the way out of a run that raised nothing:
            # a failure keeps the child's temporary files as the diagnostic they are.
            clean = False if unconfirmed else workspace.cleanup(discard_scratch=not failed)
        except (OSError, StorageError):
            clean = False
        if not clean:
            print(
                f"relkit: retained unowned or changed temporary files: {workspace.path}",
                file=sys.stderr,
            )
