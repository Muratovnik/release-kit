"""Run the optional SDK suite explicitly; empty discovery is not a passing gate."""

from __future__ import annotations

import importlib.metadata
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_suite(directory: Path, *, stream=None) -> int:
    """Use unittest's loader, but require at least one executed test."""
    suite = unittest.TestLoader().discover(str(directory), pattern="test_*.py")
    if not suite.countTestCases():
        raise ValueError(f"no MCP tests discovered in {directory}")
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if result.testsRun == len(result.skipped):
        raise ValueError("all discovered MCP tests were skipped")
    return 0 if result.wasSuccessful() else 1


def require_sdk(root: Path) -> str:
    """Read the existing runtime declaration rather than introduce another SDK pin."""
    project = tomllib.loads(
        (root / "plugins/release-kit/pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    pins = [
        item.removeprefix("mcp==")
        for item in project["dependencies"]
        if item.startswith("mcp==")
    ]
    if len(pins) != 1:
        raise ValueError("plugin runtime must declare exactly one pinned MCP SDK")
    observed = importlib.metadata.version("mcp")
    if observed != pins[0]:
        raise ValueError(f"MCP SDK mismatch: runtime declares {pins[0]}, installed {observed}")
    return observed


def main() -> int:
    try:
        version = require_sdk(ROOT)
        source = str(ROOT / "src")
        sys.path.insert(0, source)
        os.environ["PYTHONPATH"] = source
        scratch = str(Path(tempfile.gettempdir()).resolve())
        tempfile.tempdir = scratch
        os.environ.update(TMPDIR=scratch, TEMP=scratch, TMP=scratch)
        print(f"check-mcp: SDK {version}; explicit tests/mcp discovery", flush=True)
        return run_suite(ROOT / "tests/mcp")
    except (OSError, ValueError, ImportError, importlib.metadata.PackageNotFoundError) as error:
        print(f"check-mcp: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
