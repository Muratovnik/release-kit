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
import re
import shutil
import subprocess
import sys
import tomllib
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from releasekit import distribution, plugin

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


def relock(root: Path, version: str) -> bool:
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
    completed = subprocess.run(
        ["uv", "lock"],
        cwd=template,
        capture_output=True,
        text=True,
        timeout=LOCK_TIMEOUT,
        check=False,
    )
    if completed.returncode:
        raise SystemExit(f"set-version: uv lock failed\n{completed.stderr.strip()}")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="Stable X.Y.Z")
    version = parser.parse_args(argv).version
    distribution.version_tuple(version)
    if shutil.which("uv") is None:
        raise SystemExit("set-version: uv is required to regenerate the plugin lock")
    written = [path for path, pattern in carriers(ROOT) if rewrite(path, pattern, version)]
    written.extend(path for path in readmes(ROOT) if retarget_readme(path, version))
    if relock(ROOT, version):
        written.append(plugin_root(ROOT) / "uv.lock")
    if distribution.source_version(declaration(ROOT).read_text(encoding="utf-8")) != version:
        raise SystemExit("set-version: the runtime declaration did not take the new version")
    plugin.check_versions(plugin_root(ROOT), version)
    for path in written:
        print(f"set-version: wrote {path.relative_to(ROOT).as_posix()}")
    print(f"set-version: plugin, runtime, lock and CLI agree on {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
