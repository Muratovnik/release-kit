"""Build one synchronized CLI/plugin release set; never publish it implicitly."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from build_plugin import build_plugin

from releasekit import distribution, storage


def build_release(output: Path):
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
    args = parser.parse_args()
    print(json.dumps(build_release(args.output), sort_keys=True))
