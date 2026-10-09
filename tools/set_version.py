"""Declare the release version once and propagate it to the files that must carry it.

`src/releasekit/__init__.py` is the declaration: `distribution.source_version` parses it
and requires exactly one literal, and every builder already reads the version from there.
Three other files cannot hold a reference instead of a number. The plugin manifest is
what a client displays, PEP 621 wants a literal in the plugin project, and `uv.lock`
belongs to uv. So this writes the first three and asks uv to regenerate the fourth, then
refuses any other lock change: a version bump must not carry a dependency update with it.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from releasekit import distribution, plugin, processes, storage

# One pattern per file, each required to match exactly once. A second match would mean
# the file gained another version and this tool no longer knows which one is the number.
DECLARED = re.compile(rb'(?m)^(__version__ = ")[^"]+(")$')
MANIFEST = re.compile(rb'(?m)^(\s*"version": ")[^"]+(",?)$')
PROJECT = re.compile(rb'(?m)^(version = ")[^"]+(")$')
# A README installs a specific release, so its references are the version too.
# Unlike the carriers above there are several of them and they must all move,
# which is why they are rewritten by a separate pass rather than by `rewrite`.
RELEASE_REFERENCE = re.compile(rb"(\bv|release_kit-)([0-9]+\.[0-9]+\.[0-9]+)")
LOCK_TIMEOUT = 300


def plugin_root(root: Path) -> Path:
    return root / "plugins" / "release-kit"


def declaration(root: Path) -> Path:
    return root / "src" / "releasekit" / "__init__.py"


def carriers(root: Path) -> tuple[tuple[Path, re.Pattern[bytes]], ...]:
    template = plugin_root(root)
    return (
        (declaration(root), DECLARED),
        (template / ".codex-plugin" / "plugin.json", MANIFEST),
        (template / "pyproject.toml", PROJECT),
    )


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


def _lock(root: Path) -> dict:
    return tomllib.loads((plugin_root(root) / "uv.lock").read_bytes().decode("utf-8"))


def _resolved(lock: dict) -> dict[str, str]:
    return {package["name"]: package["version"] for package in lock["package"]}


def relock(root: Path, version: str, *, uv: str = "uv") -> bool:
    """Let uv write its own lock, then refuse any resolved version but the runtime's.

    Deliberately not `--offline`: an incomplete cache makes offline resolution answer
    with whatever it already has, which downgraded a pinned dependency here instead of
    reporting that it could not confirm the pin. Plain `uv lock` keeps locked versions
    unless a constraint changed, and the comparison below is what makes sure of it.

    What is compared is the resolved versions, not the whole document. A uv other than
    the one that wrote the lock legitimately rewrites environment markers from the same
    inputs, and refusing that would only teach the operator to bypass this tool. Such a
    rewrite is reported instead, because it still belongs in the diff they review.
    """
    template = plugin_root(root)
    before = _lock(root)
    completed = processes.run(
        [uv, "lock"],
        cwd=template,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=LOCK_TIMEOUT,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise SystemExit(f"set-version: uv lock failed\n{detail}")
    after = _lock(root)
    runtime = tomllib.loads((template / "pyproject.toml").read_bytes().decode("utf-8"))
    expected = {**_resolved(before), runtime["project"]["name"]: version}
    observed = _resolved(after)
    if observed != expected:
        moved = sorted(
            f"{name} {expected.get(name, 'absent')} -> {observed.get(name, 'absent')}"
            for name in expected.keys() | observed.keys()
            if expected.get(name) != observed.get(name)
        )
        raise SystemExit(
            "set-version: uv lock resolved different dependency versions; review that "
            f"change on its own instead of carrying it in a version bump: {', '.join(moved)}"
        )
    if before != after and _without_runtime(before, version, runtime) != after:
        print("set-version: uv also rewrote lock metadata; review the lock diff before committing")
    return before != after


def _without_runtime(lock: dict, version: str, runtime: dict) -> dict:
    """The lock as it would read if only the runtime version had moved."""
    expected = deepcopy(lock)
    for package in expected["package"]:
        if package["name"] == runtime["project"]["name"]:
            package["version"] = version
    return expected


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
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("set-version: uv is required to regenerate the plugin lock")
    root = storage.checked(ROOT)
    paths = [
        *(path for path, _ in carriers(root)),
        *readmes(root),
        plugin_root(root) / "uv.lock",
    ]
    # A failed resolver or a malformed later carrier must not leave a half-bumped
    # release. Preserve bytes, including line endings; uv remains the lock's writer.
    before = {storage.inside(root, path): path.read_bytes() for path in paths}
    recovery = preserve(root, before, version)
    discard = False
    try:
        written = [path for path, pattern in carriers(root) if rewrite(path, pattern, version)]
        written.extend(path for path in readmes(root) if retarget_readme(path, version))
        if relock(root, version, uv=uv):
            written.append(plugin_root(root) / "uv.lock")
        if distribution.source_version(declaration(root).read_text(encoding="utf-8")) != version:
            raise SystemExit("set-version: the runtime declaration did not take the new version")
        plugin.check_versions(plugin_root(root), version)
        discard = True
    except processes.CleanupError as error:
        raise SystemExit(
            "set-version: resolver process cleanup is unconfirmed; previous bytes retained at "
            f"{recovery.path}. Inspect owned processes before restoring files"
        ) from error
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
    print(f"set-version: plugin, runtime, lock and CLI agree on {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
