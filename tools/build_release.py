"""Build one synchronized CLI/plugin release set; never publish it implicitly."""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

from build_plugin import ROOT, build_plugin
from build_wheel import build as build_wheel

from releasekit import distribution, storage

# Git's own object header, so a worktree file hashes to the id its blob would have.
BLOB = b"blob %d\0"
CONTENT_MODES = frozenset({"100644", "100755"})


def diverged(root: Path) -> list[str]:
    """Tracked files whose exact worktree bytes are not the committed ones.

    Raw bytes, and neither `git status` nor `git hash-object`: both compare through
    Git's clean filter, which calls a CRLF worktree equal to its LF blob. That is
    precisely the divergence that made a local build produce a different artifact
    than the published release, because the builder packs the bytes it reads. It also
    reports files this repository's own status called clean.

    Outside a Git work tree there is no commit to disagree with, so nothing diverges.
    """
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "-z", "HEAD"],
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        return []
    found = []
    for entry in listing.split("\0"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        mode, kind, object_id = metadata.split(" ", 2)
        if kind != "blob" or mode not in CONTENT_MODES:
            continue
        path = root / name
        if not path.is_file():
            continue
        payload = path.read_bytes()
        if hashlib.sha1(BLOB % len(payload) + payload).hexdigest() != object_id:
            found.append(name)
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
