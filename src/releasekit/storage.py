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
from contextlib import contextmanager
from pathlib import Path


class StorageError(RuntimeError):
    """An owned path cannot safely be used."""


def checked(path: Path) -> Path:
    """Reject links, junctions, hard-linked files and path aliases before writes."""
    path = Path(os.path.abspath(path))
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise StorageError(f"service path contains a link or junction: {item}")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise StorageError(f"service path is hard-linked: {item}")
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


def environment(path: Path) -> dict[str, str]:
    local = str(checked(path))
    return {
        **os.environ,
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
        temporary.replace(path)
    finally:
        checked(temporary).unlink(missing_ok=True)


class Workspace:
    """Only files inventoried before a consumer runs are eligible for removal."""

    def __init__(self, root: Path, prefix: str = "run-"):
        parent = service_root(root) / "tmp"
        checked(parent).mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
        self.identity = identity(self.path)
        self.files: dict[str, tuple[str, tuple[int, int, int]]] = {}
        self.directories: dict[str, tuple[int, int, int]] = {}

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

    def cleanup(self) -> bool:
        checked(self.path)
        if identity(self.path) != self.identity:
            raise StorageError("temporary directory identity changed; refusing cleanup")
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
    try:
        yield workspace
    finally:
        try:
            clean = workspace.cleanup()
        except (OSError, StorageError):
            clean = False
        if not clean:
            print(
                f"relkit: retained unowned or changed temporary files: {workspace.path}",
                file=sys.stderr,
            )
