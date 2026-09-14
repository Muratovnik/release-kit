"""Build a deterministic, dependency-free release-kit projection."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "releasekit"
TIMESTAMP = (1980, 1, 1, 0, 0, 0)
sys.path.insert(0, str(SOURCE.parent))
from releasekit import distribution


def _write(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    # `ZipInfo` takes this from `sys.platform`, so the same content produced a
    # different archive on Windows than on Linux: `version made by` said FAT while
    # `external_attr` above already carried Unix permission bits. Declare the host
    # the mode bits belong to, and the projection stops depending on who built it.
    info.create_system = 3
    archive.writestr(info, payload)


def build(output: Path, *, repository: str = "") -> None:
    version = distribution.source_version((SOURCE / "__init__.py").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    # Packaging metadata takes its version from the runtime declaration above, so there
    # is no second number to compare. Refuse a literal that reintroduces one.
    if "version" in project or "version" not in project.get("dynamic", ()):
        raise ValueError("package metadata must take its version from the runtime declaration")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # The coordinator requires the released heading to carry the compare link for the
    # actual Git boundary. Accepting only an unlinked heading here is what made this
    # repository unable to publish itself with its own release command.
    # Both dated heading forms the supported profiles use: Keep-a-Changelog writes
    # `- 2026-01-01`, the conventional-changelog layout writes `(2026-01-01)`. The date itself is not
    # optional in either, because an undated entry is what a reader cannot place.
    if not re.search(
        rf"^## \[{re.escape(version)}\](?:\([^\s)]+\))? "
        rf"(?:- \d{{4}}-\d{{2}}-\d{{2}}|\(\d{{4}}-\d{{2}}-\d{{2}}\))$",
        changelog,
        re.MULTILINE,
    ):
        raise ValueError("current version needs a dated changelog entry before distribution")
    if repository:
        distribution.repository_name(repository)
    # Existing updaters accept this metadata entry, but reject new archive paths.
    # Carry the full license here without changing the supported zipapp shape.
    metadata = {
        "schema": 1,
        "version": version,
        "repository": repository,
        "authors": project["authors"],
        "license": {
            "expression": project["license"],
            "text": (ROOT / "LICENSE").read_text(encoding="utf-8"),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            _write(
                archive,
                "__main__.py",
                distribution.ENTRYPOINT,
            )
            for path in sorted(SOURCE.rglob("*.py")):
                _write(archive, path.relative_to(SOURCE.parent).as_posix(), path.read_bytes())
            _write(
                archive,
                distribution.BUILD_INFO,
                (
                    json.dumps(
                        metadata,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode(),
            )
        temporary.replace(output)
        output.with_name(output.name + ".sha256").write_text(
            f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}\n",
            encoding="utf-8",
            # Without this the newline is translated per platform, so the manifest of
            # a byte-identical artifact is not itself byte-identical.
            newline="\n",
        )
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--repository", default="", help="GitHub OWNER/REPO recorded for future relkit update calls"
    )
    arguments = parser.parse_args()
    build(arguments.output.resolve(), repository=arguments.repository)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
