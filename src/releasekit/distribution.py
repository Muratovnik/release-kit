"""Static identity and integrity checks for release-kit zipapps; never import a candidate."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_CONTENT_BYTES = 32 * 1024 * 1024
BUILD_INFO = "releasekit/build.json"
ENTRYPOINT = b"from releasekit.cli import main\nraise SystemExit(main())\n"


class DistributionError(ValueError):
    """The candidate is not a supported, identified release-kit distribution."""


def version_tuple(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise DistributionError("updates require a stable X.Y.Z version")
    return tuple(map(int, value.split(".")))


def repository_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise DistributionError("repository must be a GitHub OWNER/REPO name")
    return value


def source_version(source: str) -> str:
    values = [
        node.value.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(values) != 1:
        raise DistributionError("distribution must declare exactly one literal __version__")
    version_tuple(values[0])
    return values[0]


@dataclass(frozen=True)
class Artifact:
    version: str
    sha256: str
    repository: str = ""


def inspect(payload: bytes) -> Artifact:
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise DistributionError("distribution exceeds the archive size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > 1024 or sum(entry.file_size for entry in entries) > MAX_CONTENT_BYTES:
                raise DistributionError("distribution exceeds the content size limit")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise DistributionError("distribution has duplicate archive entries")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (
                    entry.orig_filename != entry.filename
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in entry.filename
                    or path.as_posix() != entry.filename
                    or ":" in entry.filename
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or not (
                        entry.filename == "__main__.py"
                        or (
                            entry.filename.startswith("releasekit/")
                            and (entry.filename.endswith(".py") or entry.filename == BUILD_INFO)
                        )
                    )
                ):
                    raise DistributionError("distribution has an unsupported archive entry")
            if archive.testzip() is not None or archive.read("__main__.py") != ENTRYPOINT:
                raise DistributionError(
                    "distribution has an invalid entry point or corrupt content"
                )
            if "releasekit/cli.py" not in names:
                raise DistributionError("distribution is missing releasekit/cli.py")
            version = source_version(archive.read("releasekit/__init__.py").decode("utf-8"))
            repository = ""
            if BUILD_INFO in names:
                metadata = json.loads(archive.read(BUILD_INFO))
                if (
                    not isinstance(metadata, dict)
                    or metadata.get("schema") != 1
                    or metadata.get("version") != version
                ):
                    raise DistributionError(
                        "distribution build metadata does not match its version"
                    )
                repository = metadata.get("repository", "")
                if not isinstance(repository, str):
                    raise DistributionError("distribution repository must be a string")
                if repository:
                    repository_name(repository)
    except (
        KeyError,
        SyntaxError,
        UnicodeError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        json.JSONDecodeError,
    ) as error:
        raise DistributionError(f"invalid release-kit distribution: {error}") from error
    return Artifact(version, hashlib.sha256(payload).hexdigest(), repository)
