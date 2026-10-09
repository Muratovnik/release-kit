"""Build one synchronized CLI/plugin release set; never publish it implicitly."""

import argparse
import hashlib
import json
import os
import subprocess
import tomllib
import zipfile
from pathlib import Path

from build_plugin import DOCUMENTS, FILES, ROOT, TEMPLATE, build_plugin
from build_wheel import PACKAGES
from build_wheel import build as build_wheel

from releasekit import distribution, storage

# Git's own object header, so a worktree file hashes to the id its blob would have.
BLOB = b"blob %d\0"
CONTENT_MODES = frozenset({"100644", "100755"})
# Share the plugin's maintained inventory; the other builders read these three
# fixed root inputs and the README selected by the committed package metadata.
REQUIRED_INPUTS = frozenset({"pyproject.toml", "CHANGELOG.md", "LICENSE", *DOCUMENTS}) | frozenset(
    (TEMPLATE / name).relative_to(ROOT).as_posix() for name in FILES
)


def diverged(root: Path) -> list[str]:
    """Missing, changed or extra inputs that cannot belong to the committed release.

    Raw bytes, and neither `git status` nor `git hash-object`: both compare through
    Git's clean filter, which calls a CRLF worktree equal to its LF blob. That is
    precisely the divergence that made a local build produce a different artifact
    than the published release, because the builder packs the bytes it reads. It also
    reports files this repository's own status called clean.

    A Git-free snapshot has no commit to compare here. Never discover a parent
    checkout; a broken checkout, in contrast, must not bypass this check.
    """
    if not (root / ".git").exists() and not (root / ".git").is_symlink():
        return []
    try:
        actual = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=True,
        ).stdout
        if Path(os.fsdecode(actual).rstrip("\r\n")).resolve() != root.resolve():
            raise ValueError("could not verify committed release inputs in this checkout")
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "--full-tree", "-r", "-z", "HEAD"],
            capture_output=True,
            check=True,
        ).stdout
        untracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("could not verify committed release inputs") from error
    found = {os.fsdecode(name) for name in untracked.split(b"\0") if name}
    committed = {}
    unsupported = set()
    listing = os.fsdecode(listing)
    for entry in listing.split("\0"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        mode, kind, object_id = metadata.split(" ", 2)
        committed[name] = object_id
        if kind != "blob" or mode not in CONTENT_MODES:
            unsupported.add(name)
    # A tracked link records only its target text, and a gitlink records a foreign
    # commit. Neither proves the filesystem bytes the builders would consume.
    # Reject the whole input set before reading files or traversing package roots.
    if unsupported:
        return sorted(found | unsupported)
    project_metadata = None
    for name, object_id in committed.items():
        try:
            path = storage.inside(root, root / name)
        except storage.StorageError:
            found.add(name)
            continue
        if not path.is_file():
            found.add(name)
            continue
        payload = path.read_bytes()
        if hashlib.sha1(BLOB % len(payload) + payload).hexdigest() != object_id:
            found.add(name)
        elif name == "pyproject.toml":
            project_metadata = payload
    # A clean status says nothing about ignored files absent from HEAD. Builders
    # still consume their fixed documents and the declared README, so each must
    # belong to the input set whose raw bytes were verified above.
    found.update(REQUIRED_INPUTS.difference(committed))
    if project_metadata is not None:
        project = tomllib.loads(project_metadata.decode("utf-8")).get("project", {})
        readme = project.get("readme") if isinstance(project, dict) else None
        if not isinstance(readme, str) or not readme:
            raise ValueError("committed package metadata must declare a README file path")
        try:
            path = storage.inside(root, root / readme)
        except (storage.StorageError, ValueError):
            found.add(readme)
        else:
            name = path.relative_to(storage.checked(root)).as_posix()
            # The wheel reads the original spelling. Normalizing `..` here could
            # hide a link component that the actual read would follow first.
            if ".." in Path(readme).parts or name not in committed:
                found.add(readme)
    # All builders include Python modules by glob. Even ignored local modules
    # would enter the artifacts, so ordinary `ls-files --others` is insufficient.
    for package in PACKAGES:
        for path in (root / "src" / package).rglob("*.py"):
            name = path.relative_to(root).as_posix()
            if name not in committed:
                found.add(name)
    return sorted(found)


def build_release(output: Path, *, allow_divergent: bool = False):
    if not allow_divergent and (divergent := diverged(ROOT)):
        raise ValueError(
            "release inputs are not the committed bytes; the published set must be a "
            "function of one commit: " + ", ".join(divergent)
        )
    output = storage.checked(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("release output must be new or empty; preserve existing release bytes")
    output.mkdir(parents=True, exist_ok=True)
    plugin = output / "release-kit-plugin.zip"
    build_plugin(plugin)
    with zipfile.ZipFile(plugin) as archive:
        inventory = json.loads(archive.read("release-kit/package.json"))
        payload = archive.read("release-kit/tools/relkit.pyz")
        artifact = distribution.inspect(payload)
        if (
            artifact.version != inventory["version"]
            or artifact.sha256 != inventory["files"]["tools/relkit.pyz"]
        ):
            raise ValueError("plugin and CLI release identity differs")
        (output / "relkit.pyz").write_bytes(payload)
        (output / "relkit.pyz.sha256").write_bytes(
            archive.read("release-kit/tools/relkit.pyz.sha256")
        )
    build_wheel(output)
    receipt = {
        "schema": 1,
        "version": artifact.version,
        "files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output.iterdir())
        },
    }
    storage.atomic_json(output / "release.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--allow-divergent",
        action="store_true",
        help="Build from the worktree even where it is not the committed tree",
    )
    args = parser.parse_args()
    print(
        json.dumps(build_release(args.output, allow_divergent=args.allow_divergent), sort_keys=True)
    )
