"""Read the installed plugin's fixed update source without executing project code."""

import json
import tomllib
from pathlib import Path

from . import __version__, distribution, storage


def component_versions(root: Path) -> dict[str, str]:
    """Versions of our components; third-party dependency versions are independent."""
    manifest = json.loads(
        storage.inside(root, root / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    runtime = tomllib.loads(
        storage.inside(root, root / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    lock = tomllib.loads(storage.inside(root, root / "uv.lock").read_text(encoding="utf-8"))
    packages = [item for item in lock["package"] if item["name"] == runtime["name"]]
    if len(packages) != 1:
        raise ValueError("plugin lock must contain exactly one runtime package")
    return {
        "plugin": manifest["version"],
        "runtime": runtime["version"],
        "lock": packages[0]["version"],
    }


def check_versions(root: Path, version: str) -> dict[str, str]:
    versions = component_versions(root)
    if any(value != version for value in versions.values()):
        raise ValueError(
            f"plugin, runtime, lock and CLI versions must agree ({version}): {versions}"
        )
    return versions


class Bundle:
    def __init__(self, root: Path):
        self.root = storage.checked(root)
        self.path = storage.inside(self.root, self.root / "tools/relkit.pyz")
        self.inventory_path = storage.inside(self.root, self.root / "package.json")
        self.inventory_sha256 = storage.digest(self.inventory_path)
        inventory = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        if inventory.get("schema") != 1 or inventory.get("version") != __version__:
            raise ValueError("plugin inventory and running version must agree")
        self.versions = {
            **check_versions(self.root, __version__),
            "cli": __version__,
            "inventory": inventory["version"],
        }
        if self.path.stat().st_size > distribution.MAX_ARCHIVE_BYTES:
            raise ValueError("bundled distribution exceeds the size limit")
        self.artifact = distribution.inspect(self.path.read_bytes())
        if (
            self.artifact.version != __version__
            or inventory.get("files", {}).get("tools/relkit.pyz") != self.artifact.sha256
        ):
            raise ValueError("bundled CLI version/hash differs from the installed plugin")
        self.check()

    def check(self):
        if (
            storage.digest(self.inventory_path) != self.inventory_sha256
            or storage.digest(self.path) != self.artifact.sha256
        ):
            raise ValueError("installed plugin update source changed; reinstall and restart")

    def alignment(self, current):
        self.check()
        if current.sha256 == self.artifact.sha256:
            state = "aligned"
        elif current.version == self.artifact.version:
            state = "same_version_drift"
        elif distribution.version_tuple(current.version) > distribution.version_tuple(
            self.artifact.version
        ):
            state = "project_newer"
        else:
            state = "update_available"
        return {
            "state": state,
            "project_version": current.version,
            "project_sha256": current.sha256,
            "plugin_version": __version__,
            "target_version": self.artifact.version,
            "target_sha256": self.artifact.sha256,
            "source": "installed_plugin",
        }
