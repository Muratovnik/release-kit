"""Read the installed plugin's fixed update source without executing project code."""

import json
from pathlib import Path

from . import __version__, distribution, storage


class Bundle:
    def __init__(self, root: Path):
        self.root = storage.checked(root)
        self.path = storage.inside(self.root, self.root / "tools/relkit.pyz")
        self.inventory_path = storage.inside(self.root, self.root / "package.json")
        self.inventory_sha256 = storage.digest(self.inventory_path)
        inventory = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        if inventory.get("schema") != 1 or inventory.get("version") != __version__:
            raise ValueError("plugin inventory and running version must agree")
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
