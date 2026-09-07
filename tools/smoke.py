"""Check the downloaded release assets behave, without trusting this source tree.

The coordinator has already verified names, sizes, digests and signatures by the time
this runs. What it cannot check is that the published bytes are a working tool, so
every assertion here reads the downloaded files and runs the downloaded zipapp. The
source snapshot supplies only input to read, never code to import: importing
`releasekit` from it would prove the source works, which is not what shipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

CLI = "relkit.pyz"
MANIFEST = "relkit.pyz.sha256"
PLUGIN = "release-kit-plugin.zip"
RECEIPT = "release.json"
ENTRY = "release-kit/tools/relkit.pyz"
INVENTORY = "release-kit/package.json"


class SmokeError(RuntimeError):
    """One published asset does not behave as its release claims."""


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _run(assets: Path, arguments: list[str]) -> str:
    completed = subprocess.run(
        [sys.executable, str(assets / CLI), *arguments],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise SmokeError(
            f"published {CLI} failed `{' '.join(arguments)}` with exit "
            f"{completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout


def smoke(assets: Path, source: Path, version: str) -> list[str]:
    """Every check, so one failure does not hide the next; the caller reports them."""
    problems = []
    payload = (assets / CLI).read_bytes()
    exact = digest(payload)

    recorded = (assets / MANIFEST).read_text(encoding="utf-8").split()
    if recorded[:1] != [exact] or [name.lstrip("*") for name in recorded[1:]] != [CLI]:
        problems.append(f"{MANIFEST} does not record exactly the published {CLI} digest")

    receipt = json.loads((assets / RECEIPT).read_text(encoding="utf-8"))
    if receipt.get("schema") != 1 or receipt.get("version") != version:
        problems.append(f"{RECEIPT} does not declare schema 1 and version {version}")
    for name, expected in sorted((receipt.get("files") or {}).items()):
        path = assets / name
        if not path.is_file():
            problems.append(f"{RECEIPT} names an asset that was not published: {name}")
        elif digest(path.read_bytes()) != expected:
            problems.append(f"{RECEIPT} digest does not match the published {name}")

    with zipfile.ZipFile(assets / PLUGIN) as archive:
        bundled = archive.read(ENTRY)
        inventory = json.loads(archive.read(INVENTORY))
    if bundled != payload:
        problems.append(f"{PLUGIN} bundles a different {CLI} than the one published beside it")
    if inventory.get("version") != version or inventory.get("files", {}).get(
        "tools/relkit.pyz"
    ) != digest(bundled):
        problems.append(f"{PLUGIN} inventory disagrees with its own bundled tool or the version")

    reported = _run(assets, ["--version"]).strip()
    if not reported.startswith(f"release-kit {version} "):
        problems.append(f"published tool reports {reported!r}, not release-kit {version}")

    # One real command, not only --version: a zipapp can print a version and still
    # fail to load its own modules. `notes` reads the pinned source and needs no
    # network, no engine and no Git.
    heading = _run(assets, ["notes", version, "--root", str(source)]).splitlines()
    if not heading or version not in heading[0]:
        problems.append(f"published tool did not read the {version} changelog entry from source")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True, help="Downloaded release assets")
    parser.add_argument("--version", required=True, help="Exact published version")
    parser.add_argument("--source", type=Path, default=Path.cwd(), help="Pinned source snapshot")
    arguments = parser.parse_args()
    try:
        problems = smoke(arguments.assets.resolve(), arguments.source.resolve(), arguments.version)
    except (SmokeError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"smoke: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"smoke: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"smoke: published release-kit {arguments.version} assets are consistent and runnable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
