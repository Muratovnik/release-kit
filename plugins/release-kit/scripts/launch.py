"""Start a locked optional runtime; never discover a project from this directory."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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

    package = json.loads(storage.inside(ROOT, ROOT / "package.json").read_text(encoding="utf-8"))
    for name, expected_hash in package["files"].items():
        if storage.digest(storage.inside(ROOT, ROOT / name)) != expected_hash:
            raise ValueError(f"plugin payload changed: {name}")
    if arguments.check:
        print(
            json.dumps(
                {"version": package["version"], "files": len(package["files"]), "valid": True}
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
    if runtime.exists():
        receipt = storage.inside(ROOT, runtime / "owner.json")
        if json.loads(receipt.read_text(encoding="utf-8")) != expected:
            raise ValueError(
                "plugin runtime ownership/version differs; reinstall into a fresh path"
            )
    else:
        runtime.mkdir()
        storage.atomic_json(runtime / "owner.json", expected)
    temporary = storage.inside(ROOT, runtime / "tmp")
    cache = storage.inside(ROOT, runtime / "cache")
    virtualenv = storage.inside(ROOT, runtime / "venv")
    temporary.mkdir(exist_ok=True)
    environment = storage.environment(temporary)
    for key in list(environment):
        if key.startswith("UV_") or key in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
            del environment[key]
    environment.update(
        UV_CACHE_DIR=str(cache),
        UV_PROJECT_ENVIRONMENT=str(virtualenv),
        UV_PYTHON_DOWNLOADS="never",
        UV_LINK_MODE="copy",
    )
    return subprocess.call(
        [
            uv,
            "run",
            "--locked",
            "--no-config",
            "--no-dev",
            "--no-editable",
            "--no-python-downloads",
            "--no-build",
            "--project",
            str(ROOT),
            "--python",
            sys.executable,
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
