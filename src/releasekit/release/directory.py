"""Hosting-independent publication of an exact release directory and manifest."""

from __future__ import annotations

import json
import re
import shutil

from .. import canonical, storage
from .backend import CommandError, ReleaseError

MANIFEST = "relkit-release.json"


class Directory:
    def __init__(self, runner, release):
        self.runner = runner
        self.path = storage.inside(runner.root, runner.root / release.directory)

    def identity(self):
        return {"id": "directory:" + canonical.fingerprint(str(self.path)), "full_name": ""}

    def preflight(self):
        try:
            self.runner.git("check-ignore", "--quiet", str(self.path / "probe"))
        except CommandError as error:
            raise ReleaseError(
                "release.directory must be ignored by Git before preparation"
            ) from error

    def release(self, tag):
        if not re.fullmatch(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", tag):
            raise ReleaseError("invalid release directory tag")
        folder = storage.inside(self.path, self.path / tag)
        if not folder.exists():
            return None
        manifest = storage.checked(folder / MANIFEST)
        if not manifest.is_file() or manifest.stat().st_size > 2 * 1024 * 1024:
            raise ReleaseError("release directory has no valid publication manifest")
        record = json.loads(manifest.read_text(encoding="utf-8"))
        if record.get("schema") != 1 or record.get("tag") != tag:
            raise ReleaseError("release directory manifest identity differs")
        return {
            "id": canonical.fingerprint(record),
            "tag_name": tag,
            "draft": False,
            "prerelease": False,
            "body": record["notes"],
            "record": record,
        }

    def releases(self):
        if not self.path.exists():
            return []
        return [
            self.release(p.name)
            for p in storage.checked(self.path).iterdir()
            if re.fullmatch(r"v\d+\.\d+\.\d+", p.name)
        ]

    def release_by_id(self, identifier):
        """A directory is read straight from disk, so nothing here can lag behind a write.

        The identifier is honoured anyway, so a caller that recorded one keeps checking
        the same publication rather than whichever one now sits under the tag.
        """
        return next((r for r in self.releases() if r["id"] == identifier), None)

    def locate(self, release_id):
        for release in self.releases():
            if release["id"] == release_id:
                return release
        raise ReleaseError("published directory disappeared or changed")

    def assets(self, release_id):
        release = self.locate(release_id)
        return [
            {**item, "id": i + 1, "state": "uploaded"}
            for i, item in enumerate(release["record"]["files"])
        ]

    def verify(self, value, release, tag_oid):
        from .candidate import inventory

        record = release["record"]
        if record["sha"] != value["sha"] or record["tag_oid"] != tag_oid:
            raise ReleaseError("published directory source/tag identity differs")
        directory = storage.inside(self.path, self.path / value["tag"] / "assets")
        if {item.name for item in directory.parent.iterdir()} != {MANIFEST, "assets"}:
            raise ReleaseError("published directory contains unexpected files")
        if inventory(value, directory) != record["files"]:
            raise ReleaseError("published directory files changed")

    def download(self, asset, destination):
        # Asset ids are scoped by release; the verifier sets its selected release.
        source = storage.inside(self.path, self.path / self.selected_tag / "assets" / asset["name"])
        shutil.copyfile(source, destination)

    def export(self, value, directory, tag_oid):
        from .candidate import inventory

        self.preflight()
        record = {
            "schema": 1,
            "tag": value["tag"],
            "version": value["version"],
            "sha": value["sha"],
            "tag_oid": tag_oid,
            "notes": value["notes"],
            "files": inventory(value, directory),
        }
        # Staging is on the destination filesystem, then one rename publishes the
        # complete set. An interrupted stage can be inspected and safely retried.
        self.path.mkdir(parents=True, exist_ok=True)
        staged = storage.inside(
            self.path,
            self.path / (".pending-" + value["tag"] + "-" + canonical.fingerprint(record)),
        )
        if staged.exists():
            raise ReleaseError(f"retained export stage exists; inspect before retrying: {staged}")
        staged.mkdir()
        shutil.copytree(directory, staged / "assets")
        storage.atomic_json(staged / MANIFEST, record)
        if inventory(value, staged / "assets") != record["files"]:
            raise ReleaseError("exported files changed during copy")
        destination = storage.inside(self.path, self.path / value["tag"])
        if destination.exists():
            raise ReleaseError("release directory already exists; never overwrite a publication")
        staged.rename(destination)
        return self.release(value["tag"])
