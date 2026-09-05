"""Build one deterministic plugin snapshot from the maintained source tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import tomllib
import zipfile
from pathlib import Path

from build_zipapp import ROOT, SOURCE, _write, build

TEMPLATE = ROOT / "plugins/release-kit"
FILES = (
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "pyproject.toml",
    "uv.lock",
    "scripts/launch.py",
    "scripts/serve.py",
    "skills/release-kit/SKILL.md",
    "skills/release-kit/references/updates.md",
    "skills/release-kit/references/releases.md",
)


def payloads():
    from releasekit import distribution, storage

    version = distribution.source_version((SOURCE / "__init__.py").read_text(encoding="utf-8"))
    manifest = json.loads((TEMPLATE / FILES[0]).read_text(encoding="utf-8"))
    runtime = tomllib.loads((TEMPLATE / "pyproject.toml").read_text(encoding="utf-8"))
    if manifest["version"] != version or runtime["project"]["version"] != version:
        raise ValueError("plugin, runtime and CLI versions must agree")
    if manifest["name"] != TEMPLATE.name:
        raise ValueError("plugin name must match its directory")
    payload = {name: storage.inside(ROOT, TEMPLATE / name).read_bytes() for name in FILES}
    for package in ("releasekit", "releasekit_mcp"):
        for path in sorted((ROOT / "src" / package).rglob("*.py")):
            name = "lib/" + path.relative_to(ROOT / "src").as_posix()
            payload[name] = storage.inside(ROOT, path).read_bytes()
    payload["README.md"] = b"# Release Kit\n\nSee [installation and use](docs/plugin.md).\n"
    payload["LICENSE"] = (ROOT / "LICENSE").read_bytes()
    for name in ("plugin.md", "mcp.md", "cli-json.md"):
        payload["docs/" + name] = (ROOT / "docs" / name).read_bytes()
    return version, payload


def build_plugin(output: Path):
    from releasekit import storage

    output = storage.checked(output)
    version, files = payloads()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="plugin-build-", dir=output.parent) as folder:
        projection = Path(folder) / "relkit.pyz"
        build(projection, repository="Muratovnik/release-kit")
        files["tools/relkit.pyz"] = projection.read_bytes()
        files["tools/relkit.pyz.sha256"] = projection.with_suffix(".pyz.sha256").read_bytes()
        files["package.json"] = (
            json.dumps(
                {
                    "schema": 1,
                    "version": version,
                    "files": {
                        name: hashlib.sha256(body).hexdigest()
                        for name, body in sorted(files.items())
                    },
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode()
        temporary = Path(folder) / "plugin.zip"
        with zipfile.ZipFile(temporary, "w") as archive:
            for name, body in sorted(files.items()):
                _write(archive, "release-kit/" + name, body)
        storage.checked(output)
        temporary.replace(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    receipt = storage.checked(output.with_suffix(output.suffix + ".sha256"))
    receipt.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return digest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(build_plugin(args.output))
