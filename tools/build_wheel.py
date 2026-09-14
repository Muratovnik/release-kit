"""Build a deterministic wheel so the published artifact can be installed directly.

The zipapp stays the portable form for a repository to pin. This is the form a person
installs on a machine, which is what `uv tool install <url>` and `pipx install <url>`
consume. It is written here rather than delegated to a build backend for the reasons
the zipapp is: the release build needs no network, no build isolation and no floating
tool version, and the bytes must be a function of the commit rather than of the host
that produced them. A pure-Python distribution with no runtime dependencies is a zip
with a declared layout, so building it directly costs less than pinning a backend.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import tomllib
import zipfile
from pathlib import Path

from build_zipapp import ROOT, SOURCE, _write, distribution

TAG = "py3-none-any"
PACKAGES = ("releasekit", "releasekit_mcp")


def _record_digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return f"sha256={encoded.decode('ascii')}"


def metadata(project: dict, version: str) -> bytes:
    """Core metadata 2.4, which is what a license expression requires."""
    author = project["authors"][0]
    lines = [
        "Metadata-Version: 2.4",
        f"Name: {project['name']}",
        f"Version: {version}",
        f"Summary: {project['description']}",
        f"Author-email: {author['name']} <{author['email']}>",
        f"License-Expression: {project['license']}",
        "License-File: LICENSE",
        f"Requires-Python: {project['requires-python']}",
    ]
    for extra, requirements in sorted(project.get("optional-dependencies", {}).items()):
        lines.append(f"Provides-Extra: {extra}")
        for requirement in requirements:
            lines.append(f'Requires-Dist: {requirement}; extra == "{extra}"')
    lines += ["Description-Content-Type: text/markdown", ""]
    readme = (ROOT / project["readme"]).read_text(encoding="utf-8")
    return ("\n".join(lines) + "\n" + readme).encode("utf-8")


def entry_points(project: dict) -> bytes:
    lines = ["[console_scripts]"]
    lines += [f"{name} = {target}" for name, target in sorted(project["scripts"].items())]
    return ("\n".join(lines) + "\n").encode("utf-8")


def wheel_metadata() -> bytes:
    return (
        f"Wheel-Version: 1.0\nGenerator: release-kit\nRoot-Is-Purelib: true\nTag: {TAG}\n"
    ).encode()


def members(project: dict, version: str, dist_info: str) -> list[tuple[str, bytes]]:
    found: list[tuple[str, bytes]] = []
    for package in PACKAGES:
        directory = SOURCE.parent / package
        for path in sorted(directory.rglob("*.py")):
            found.append((path.relative_to(SOURCE.parent).as_posix(), path.read_bytes()))
    found.append((f"{dist_info}/METADATA", metadata(project, version)))
    found.append((f"{dist_info}/WHEEL", wheel_metadata()))
    found.append((f"{dist_info}/entry_points.txt", entry_points(project)))
    found.append((f"{dist_info}/licenses/LICENSE", (ROOT / "LICENSE").read_bytes()))
    return found


def record(entries: list[tuple[str, bytes]], dist_info: str) -> bytes:
    stream = io.StringIO(newline="")
    # The RECORD is read as CSV, so it is written as CSV with the newline the format
    # declares rather than the one this host would choose.
    writer = csv.writer(stream, lineterminator="\n")
    for name, payload in entries:
        writer.writerow([name, _record_digest(payload), len(payload)])
    writer.writerow([f"{dist_info}/RECORD", "", ""])
    return stream.getvalue().encode("utf-8")


def build(output: Path) -> Path:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = distribution.source_version((SOURCE / "__init__.py").read_text(encoding="utf-8"))
    if "version" in project or "version" not in project.get("dynamic", ()):
        raise ValueError("package metadata must take its version from the runtime declaration")
    normalized = project["name"].replace("-", "_")
    dist_info = f"{normalized}-{version}.dist-info"
    entries = members(project, version, dist_info)
    target = output / f"{normalized}-{version}-{TAG}.whl"
    output.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".partial")
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            for name, payload in entries:
                _write(archive, name, payload)
            _write(archive, f"{dist_info}/RECORD", record(entries, dist_info))
        temporary.replace(target)
        target.with_name(target.name + ".sha256").write_text(
            f"{hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}\n",
            encoding="utf-8",
            newline="\n",
        )
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    print(build(parser.parse_args().output.resolve()).name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
