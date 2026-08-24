"""Build a deterministic, dependency-free release-kit projection."""

from __future__ import annotations

import argparse
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "releasekit"
TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _write(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, payload)


def build(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            _write(
                archive,
                "__main__.py",
                b"from releasekit.cli import main\nraise SystemExit(main())\n",
            )
            for path in sorted(SOURCE.rglob("*.py")):
                _write(archive, path.relative_to(SOURCE.parent).as_posix(), path.read_bytes())
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    build(arguments.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
