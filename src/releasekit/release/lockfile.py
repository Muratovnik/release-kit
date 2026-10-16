"""The repository-wide release lock: who holds it, and when it can be removed.

One lock file, `.git/relkit/release.lock`, serializes every command that writes release
state. It is never taken over automatically. It stays behind on purpose when owned
commands could not be confirmed stopped, and it stays behind by accident when the owner
dies without releasing it. In both cases a stopped PID alone proves nothing, because the
owner's commands and their descendants may still be running.

`unlock` removes a stale lock only after proving each of these, and refuses with the
unmet condition and the lock's path otherwise. It never signals a process.

1. The lock names an owner this checkout can read: a PID, a tag and this root.
2. The owner is not running on this machine. A running process with the recorded PID
   might be a reused PID, but release-kit cannot tell, so it refuses.
3. No receipt written under this lock records that owned commands could not be
   confirmed stopped, which is the case the lock is retained for.
4. No receipt written under this lock was left mid-stage by the owner's death, unless
   the platform ends the owner's commands with it. A Windows job object does, so a dead
   owner has no descendants; a POSIX process group does not, and its members are not
   recorded, so the death of an owner mid-command cannot be shown to have ended them.
5. The lock is the one inspected: its bytes are read again immediately before removal.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

from .. import storage
from .backend import ReleaseError

READ_LIMIT = 2 * 1024 * 1024


def path(root: Path) -> Path:
    return storage.service_root(root) / "release.lock"


def process_alive(pid: int) -> bool | None:
    """Best-effort liveness that never signals the process (Windows os.kill terminates)."""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return {5: True, 87: False}.get(ctypes.get_last_error())
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def jobs_end_with_owner() -> bool:
    """Whether the platform ends an owner's commands when the owner itself dies.

    A Windows job object is created with KILL_ON_JOB_CLOSE, and the system closes the
    owner's handle when it dies. A POSIX process group is only signalled by its owner.
    """
    return sys.platform == "win32"


def describe(lock: Path) -> str:
    """The recorded owner and whether its PID is running, for a refusal that names both."""
    try:
        owner_record = json.loads(lock.read_text(encoding="utf-8"))
        pid, tag, root = owner_record["pid"], owner_record["tag"], owner_record["root"]
    except (OSError, ValueError, KeyError, TypeError):
        return "its recorded owner is unreadable"
    alive = process_alive(pid) if type(pid) is int else None
    liveness = {
        True: "a process with this PID is running",
        False: "no process with this PID is running",
        None: "PID liveness could not be checked",
    }[alive]
    return f"pid {pid} ({liveness}), tag {tag}, root {root}"


def acquire(root: Path, tag: str) -> Path:
    lock = path(root)
    storage.checked(lock).parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "tag": tag,
                        "root": str(root),
                        "host": socket.gethostname(),
                        "at": datetime.now(UTC).isoformat(),
                    }
                )
            )
    except FileExistsError as error:
        raise ReleaseError(
            f"another release owns {lock}: {describe(lock)}; after a crash `relkit release "
            f"unlock --root {root}` removes it once it has proven that the owner and its "
            "commands are gone. A stopped PID alone is insufficient, so nothing here is "
            "removed automatically"
        ) from error
    return lock


def _read_json(file: Path):
    if file.stat().st_size > READ_LIMIT:
        raise ValueError("receipt exceeds the read limit")
    return json.loads(file.read_text(encoding="utf-8"))


def _receipts(root: Path, tag: str, since: float) -> list[tuple[Path, dict | None]]:
    """Receipts of this tag written while the lock existed, which its owner wrote.

    A receipt that cannot be read is returned as `None`: what it says is unknown, and an
    unknown answer is not proof that cleanup was confirmed.
    """
    service = storage.service_root(root)
    found: list[tuple[Path, dict | None]] = []
    candidates = [service / "releases" / tag / "state.json"]
    folder = service / "candidates" / tag
    if folder.is_dir():
        candidates += sorted(
            item
            for item in folder.glob("*.json")
            if item.name not in {"ready.json", "promoted.json"}
        )
    for file in candidates:
        try:
            storage.checked(file)
            if not file.is_file() or file.stat().st_mtime < since:
                continue
            value = _read_json(file)
            found.append((file, value if isinstance(value, dict) else None))
        except (OSError, ValueError, storage.StorageError):
            found.append((file, None))
    return found


def _stale(lock: Path, record: dict, root: Path) -> list[str]:
    """What is unproven about a lock whose owner is gone; empty when it can be removed."""
    unmet: list[str] = []
    since = lock.stat().st_mtime
    for file, value in _receipts(root, record["tag"], since):
        if value is None:
            unmet.append(
                f"{file} cannot be read, so whether its commands were confirmed stopped is "
                "unknown; verify that they have stopped, then remove the lock by hand"
            )
            continue
        released = file.name == "state.json"
        if (
            value.get("process_cleanup") == "unconfirmed"
            or value.get("cleanup") == "retained-process-cleanup-unconfirmed"
            or value.get("status") == "cleanup-unconfirmed"
        ):
            unmet.append(
                f"{file} records that owned commands could not be confirmed stopped; verify "
                "that they and their descendants have stopped, then remove the lock by hand"
            )
            continue
        stage = value.get("stage") if released else None
        running = (
            value.get("stages", {}).get(stage) == "running"
            if released
            else value.get("status") == "running"
        )
        if running and not jobs_end_with_owner():
            unmet.append(
                f"{file} was left mid-stage"
                + (f" ({stage})" if stage else "")
                + " by the owner's death, and on this platform its commands can outlive the "
                "owner without a record of them; verify that they have stopped, then remove "
                "the lock by hand"
            )
    return unmet


def unlock(root: Path, tag: str = "") -> dict:
    """Remove the release lock if its owner and commands are provably gone, else refuse."""
    lock = path(root)
    if not (lock.exists() or lock.is_symlink()):
        return {"removed": False, "lock": str(lock), "reason": "no release lock exists"}
    refused = f"cannot remove {lock}: "
    try:
        raw = storage.checked(lock).read_bytes()
        record = json.loads(raw.decode("utf-8"))
        pid, held, recorded = record["pid"], record["tag"], record["root"]
        if type(pid) is not int or pid <= 0 or not isinstance(held, str):
            raise TypeError("malformed owner")
    except (OSError, ValueError, KeyError, TypeError, storage.StorageError) as error:
        raise ReleaseError(
            refused + f"its owner record is unreadable or malformed ({error}); release-kit "
            "cannot tell who holds it, so it will not decide that nobody does"
        ) from error
    if recorded != str(root):
        raise ReleaseError(refused + f"it was taken for {recorded}, not for {root}")
    if tag and held != tag:
        raise ReleaseError(
            refused + f"it is held for {held}, not for {tag}; name {held} or omit it"
        )
    host = record.get("host")
    if host and host != socket.gethostname():
        raise ReleaseError(
            refused + f"its owner ran on {host}, and this machine cannot see that host's processes"
        )
    alive = process_alive(pid)
    if alive is not False:
        raise ReleaseError(
            refused
            + (
                f"a process with its owner's PID {pid} is running (it may be the owner, or "
                "the PID may have been reused; release-kit cannot tell which)"
                if alive
                else f"whether its owner's PID {pid} is running could not be checked"
            )
        )
    if unmet := _stale(lock, record, root):
        raise ReleaseError(refused + "; ".join(unmet))
    if storage.checked(lock).read_bytes() != raw:
        raise ReleaseError(refused + "it changed while it was being checked; nothing was removed")
    storage.checked(lock).unlink()
    return {
        "removed": True,
        "lock": str(lock),
        "owner": {"pid": pid, "tag": held, "root": recorded, "host": host},
        "proof": [
            f"owner pid {pid} is not running",
            f"no receipt for {held} written under this lock records unconfirmed cleanup",
            "the lock was unchanged between inspection and removal",
        ],
    }
