"""Declare the release version once and propagate it to the files that must carry it.

`src/releasekit/__init__.py` is the declaration: `distribution.source_version` parses it
and requires exactly one literal, and every builder already reads the version from there.
Packaging metadata takes the version from that declaration rather than repeating it. The
only other places that must carry the number are the READMEs, because each installs a
specific release; this writes the declaration and retargets them in one transaction.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from releasekit import distribution, storage

# Required to match exactly once. A second match would mean the file gained another
# version and this tool no longer knows which one is the number.
DECLARED = re.compile(rb'(?m)^(__version__ = ")[^"]+(")$')
# A README installs a specific release, so its references are the version too.
# Unlike the carriers above there are several of them and they must all move,
# which is why they are rewritten by a separate pass rather than by `rewrite`.
RELEASE_REFERENCE = re.compile(rb"(\bv|release_kit-)([0-9]+\.[0-9]+\.[0-9]+)")


def declaration(root: Path) -> Path:
    return root / "src" / "releasekit" / "__init__.py"


def carriers(root: Path) -> tuple[tuple[Path, re.Pattern[bytes]], ...]:
    return ((declaration(root), DECLARED),)


def rewrite(path: Path, pattern: re.Pattern[bytes], version: str) -> bool:
    """Substitute in bytes, so a rewrite never depends on this host's line endings."""
    original = path.read_bytes()
    replaced, count = pattern.subn(rb"\g<1>" + version.encode() + rb"\g<2>", original)
    if count != 1:
        raise SystemExit(f"set-version: expected exactly one version in {path}, found {count}")
    if replaced == original:
        return False
    path.write_bytes(replaced)
    return True


def readmes(root: Path) -> tuple[Path, ...]:
    """Every README, found rather than listed.

    A translated quick start installs a release exactly as the English one does, so a
    translation left behind pins an old tool. Discovering the pages means adding a
    language cannot forget this step; naming them would have to be remembered.
    """
    return tuple(sorted(root.glob("README*.md")))


def retarget_readme(path: Path, version: str) -> bool:
    """Point every release reference in one README at the version being declared."""
    original = path.read_bytes()
    replaced, count = RELEASE_REFERENCE.subn(
        lambda match: match.group(1) + version.encode(), original
    )
    if not count and path.name == "README.md":
        raise SystemExit("set-version: the README names no release to install")
    if replaced == original:
        return False
    path.write_bytes(replaced)
    return True


@dataclass
class Recovery:
    path: Path
    identity: tuple[int, int, int]
    files: dict[Path, tuple[bytes, tuple[int, int, int]]]


def preserve(root: Path, before: dict[Path, bytes], version: str) -> Recovery:
    """Keep original bytes until resolver teardown and any rollback are known."""
    recovery = None
    try:
        parent = storage.inside(root, root / ".cache")
        parent.mkdir(exist_ok=True)
        path = Path(tempfile.mkdtemp(prefix="version-recovery-", dir=parent))
        recovery = Recovery(path, storage.identity(path), {})
        inventory = {}
        for index, (source, payload) in enumerate(before.items()):
            name = f"{index:02d}-{source.name}"
            target = storage.inside(path, path / name)
            with target.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            recovery.files[target] = (payload, storage.identity(target))
            inventory[source.relative_to(root).as_posix()] = {
                "backup": name,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        manifest = path / "recovery.json"
        storage.atomic_json(
            manifest,
            {
                "schema": 1,
                "tool": "set_version",
                "root": str(root),
                "requested_version": version,
                "files": inventory,
            },
        )
        recovery.files[manifest] = (manifest.read_bytes(), storage.identity(manifest))
        return recovery
    except (OSError, storage.StorageError) as error:
        retained = f"; partial recovery retained at {recovery.path}" if recovery else ""
        raise SystemExit(
            "set-version: could not preserve previous bytes before changing versions"
            + retained
            + f": {error}"
        ) from error


def discard_recovery(recovery: Recovery) -> bool:
    """Remove only the unchanged files this invocation wrote; retain any unknown data."""
    try:
        if storage.identity(recovery.path) != recovery.identity:
            return False
        if set(recovery.path.iterdir()) != set(recovery.files):
            return False
        for path, (payload, identity) in recovery.files.items():
            if storage.identity(path) != identity or path.read_bytes() != payload:
                return False
        for path in recovery.files:
            path.unlink()
        recovery.path.rmdir()
        return True
    except (OSError, storage.StorageError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="Stable X.Y.Z")
    version = parser.parse_args(argv).version
    distribution.version_tuple(version)
    root = storage.checked(ROOT)
    paths = [*(path for path, _ in carriers(root)), *readmes(root)]
    # A malformed later carrier must not leave a half-bumped release. Preserve the
    # bytes, including line endings, until the whole transaction is known.
    before = {storage.inside(root, path): path.read_bytes() for path in paths}
    recovery = preserve(root, before, version)
    discard = False
    try:
        written = [path for path, pattern in carriers(root) if rewrite(path, pattern, version)]
        written.extend(path for path in readmes(root) if retarget_readme(path, version))
        if distribution.source_version(declaration(root).read_text(encoding="utf-8")) != version:
            raise SystemExit("set-version: the runtime declaration did not take the new version")
        discard = True
    except BaseException as error:
        unrestored = []
        for path, payload in before.items():
            try:
                storage.inside(root, path)
                if not path.exists() or path.read_bytes() != payload:
                    path.write_bytes(payload)
            except (OSError, storage.StorageError):
                unrestored.append(path.relative_to(root).as_posix())
        if unrestored:
            raise SystemExit(
                "set-version: bump failed and previous bytes could not be restored in: "
                + ", ".join(unrestored)
                + f"; previous bytes retained at {recovery.path}"
            ) from error
        discard = True
        raise
    finally:
        if discard and not discard_recovery(recovery):
            print(f"set-version: recovery retained at {recovery.path}", file=sys.stderr)
    for path in written:
        print(f"set-version: wrote {path.relative_to(root).as_posix()}")
    print(f"set-version: declaration and READMEs agree on {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
