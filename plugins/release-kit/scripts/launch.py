"""Start a locked optional runtime; never discover a project from this directory."""

import argparse
import errno
import hashlib
import json
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def process_path(path):
    """Pass checked absolute runtime paths to Windows tools without MAX_PATH."""
    value = str(path)
    if sys.platform != "win32" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


@contextmanager
def initialization_lock(path):
    """Serialize receipt publication; the OS releases the lock after a crash.

    Keep the lock file: unlinking it would let contenders lock different files.
    This protects receipt publication and uv synchronization, never the live server.
    """
    with path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            def acquire():
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

        else:
            import fcntl

            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        deadline = time.monotonic() + 150
        while True:
            try:
                acquire()
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError("plugin runtime initialization is still busy") from error
                time.sleep(0.05)
        # Closing the non-inheritable descriptor releases the lock, even on error.
        yield


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    interpreter = tuple(sys.version_info[:2])
    if interpreter < (3, 11):
        raise ValueError("release-kit plugin requires Python 3.11 or newer")
    if not (ROOT / "lib/releasekit_mcp/server.py").is_file():
        raise ValueError("install a built release-kit plugin, not the source template")
    sys.path.insert(0, str(ROOT / "lib"))
    from releasekit import storage
    from releasekit.plugin import Bundle

    package = json.loads(storage.inside(ROOT, ROOT / "package.json").read_text(encoding="utf-8"))
    for name, expected_hash in package["files"].items():
        if storage.digest(storage.inside(ROOT, ROOT / name)) != expected_hash:
            raise ValueError(f"plugin payload changed: {name}")
    bundle = Bundle(ROOT)
    if arguments.check:
        print(
            json.dumps(
                {
                    "version": package["version"],
                    "components": bundle.versions,
                    "files": len(package["files"]),
                    "valid": True,
                }
            )
        )
        return 0
    uv = shutil.which("uv")
    if not uv:
        raise ValueError("install uv before starting the release-kit plugin")
    runtime = storage.inside(ROOT, ROOT / ".runtime")
    expected = {
        "schema": 1,
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "python": list(sys.version_info[:2]),
    }
    temporary = storage.inside(ROOT, runtime / "tmp")
    cache = storage.inside(ROOT, runtime / "cache")
    virtualenv = storage.inside(ROOT, runtime / "venv")
    environment = storage.environment(temporary)
    for key in list(environment):
        if key.startswith("UV_") or key in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
            del environment[key]
    environment.update(
        UV_CACHE_DIR=process_path(cache),
        UV_PROJECT_ENVIRONMENT=process_path(virtualenv),
        UV_PYTHON_DOWNLOADS="never",
        UV_LINK_MODE="copy",
    )
    for key in ("TMP", "TEMP", "TMPDIR"):
        environment[key] = process_path(temporary)
    options = [
        "--locked",
        "--no-config",
        "--no-dev",
        "--no-editable",
        "--no-python-downloads",
        "--no-build",
        "--project",
        process_path(ROOT),
        "--python",
        sys.executable,
    ]
    with initialization_lock(storage.inside(ROOT, ROOT / ".runtime.lock")):
        if runtime.exists():
            receipt = storage.inside(ROOT, runtime / "owner.json")
            if not receipt.is_file() or json.loads(receipt.read_text(encoding="utf-8")) != expected:
                raise ValueError(
                    "plugin runtime ownership/version differs; reinstall into a fresh path"
                )
        else:
            runtime.mkdir()
            storage.atomic_json(runtime / "owner.json", expected)
        temporary.mkdir(exist_ok=True)
        synced = subprocess.call([uv, "sync", "--inexact", *options], cwd=ROOT, env=environment)
        if synced:
            return synced
    return subprocess.call(
        [
            uv,
            "run",
            "--no-sync",
            *options,
            "python",
            str(ROOT / "scripts/serve.py"),
        ],
        cwd=ROOT,
        env=environment,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as error:
        print(f"release-kit plugin: {error}", file=sys.stderr)
        raise SystemExit(2)
