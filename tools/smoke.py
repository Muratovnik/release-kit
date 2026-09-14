"""Check the downloaded release assets behave, without trusting this source tree.

Validate the complete release set before executing any candidate code, including
when invoked without a coordinator. Signatures remain the caller's responsibility.
What metadata cannot check is that the published bytes are a working tool, so
every assertion here reads the downloaded files and runs the downloaded zipapp. The
source snapshot supplies only input to read, never code to import: importing
`releasekit` from it would prove the source works, which is not what shipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

CLI = "relkit.pyz"
MANIFEST = "relkit.pyz.sha256"
PLUGIN = "release-kit-plugin.zip"
RECEIPT = "release.json"
WHEEL = "release_kit-{version}-py3-none-any.whl"
ENTRY = "release-kit/tools/relkit.pyz"
INVENTORY = "release-kit/package.json"
# This distribution's contract, independent of the receipt under inspection. The
# wheel carries the version in its name, so the set is a function of the version.
FIXED = frozenset({CLI, MANIFEST, PLUGIN, PLUGIN + ".sha256", RECEIPT})


def expected_assets(version: str) -> frozenset[str]:
    wheel = WHEEL.format(version=version)
    return FIXED | {wheel, wheel + ".sha256"}


class SmokeError(RuntimeError):
    """One published asset does not behave as its release claims."""


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SmokeError(f"duplicate release manifest key: {key}")
        value[key] = item
    return value


def inventory(assets: Path, version: str) -> dict[str, str]:
    """Require every ordinary file and complete, consistent hash declarations."""
    wanted = expected_assets(version)
    observed = {path.name for path in assets.iterdir()}
    if observed != wanted:
        raise SmokeError(
            f"release asset set differs: missing={sorted(wanted - observed)}, "
            f"unexpected={sorted(observed - wanted)}"
        )
    hashes = {}
    for name in sorted(wanted):
        path = assets / name
        if not stat.S_ISREG(path.lstat().st_mode):
            raise SmokeError(f"release asset must be an ordinary file: {name}")
        hashes[name] = digest(path.read_bytes())
    for name in (CLI, PLUGIN, WHEEL.format(version=version)):
        sidecar = name + ".sha256"
        words = (assets / sidecar).read_text(encoding="utf-8").split()
        if len(words) != 2 or words[0] != hashes[name] or words[1] not in (name, "*" + name):
            raise SmokeError(f"{sidecar} must name exactly {name} and its SHA-256")
    receipt = json.loads(
        (assets / RECEIPT).read_text(encoding="utf-8"), object_pairs_hook=_unique_object
    )
    if (
        not isinstance(receipt, dict)
        or type(receipt.get("schema")) is not int
        or receipt["schema"] != 1
        or receipt.get("version") != version
    ):
        raise SmokeError(f"{RECEIPT} must declare schema 1 and version {version}")
    files = receipt.get("files")
    if not isinstance(files, dict) or set(files) != wanted - {RECEIPT}:
        raise SmokeError(f"{RECEIPT} must cover every other release asset exactly once")
    for name, expected in files.items():
        if (
            not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
            or hashes[name] != expected
        ):
            raise SmokeError(f"{RECEIPT} digest does not match {name}")
    return hashes


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
    before = inventory(assets, version)
    payload = (assets / CLI).read_bytes()

    with zipfile.ZipFile(assets / PLUGIN) as archive:
        bundled = archive.read(ENTRY)
        package_inventory = json.loads(archive.read(INVENTORY))
    if bundled != payload:
        problems.append(f"{PLUGIN} bundles a different {CLI} than the one published beside it")
    if package_inventory.get("version") != version or package_inventory.get("files", {}).get(
        "tools/relkit.pyz"
    ) != digest(bundled):
        problems.append(f"{PLUGIN} inventory disagrees with its own bundled tool or the version")

    # Metadata problems must not be followed by candidate execution.
    if problems:
        return problems
    reported = _run(assets, ["--version"]).strip()
    if not reported.startswith(f"release-kit {version} "):
        problems.append(f"published tool reports {reported!r}, not release-kit {version}")

    # One real command, not only --version: a zipapp can print a version and still
    # fail to load its own modules. `notes` reads the pinned source and needs no
    # network, no engine and no Git.
    heading = _run(assets, ["notes", version, "--root", str(source)]).splitlines()
    if not heading or version not in heading[0]:
        problems.append(f"published tool did not read the {version} changelog entry from source")
    if inventory(assets, version) != before:
        problems.append("release assets changed while being smoke-tested")
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
