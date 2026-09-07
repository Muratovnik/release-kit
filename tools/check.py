"""Run this repository's declared verification gates as one command.

The release coordinator executes configured checks as argv arrays without a shell,
so a gate that needs `src` on the import path needs a runner rather than an exported
variable. This is that runner, and it runs exactly the list AGENTS.md declares. The
publication audits are not here: the coordinator runs the worktree and history audits
itself, and CI provisions no engine downloads.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATES = (
    ["-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
    ["-m", "ruff", "check", "src", "tests", "tools"],
    ["-m", "ruff", "format", "--check", "src", "tests", "tools"],
)


def temporary_directory() -> str:
    """A temp directory with no link in its path, because the suite writes service paths.

    `storage.checked` deliberately refuses a service path whose components include a
    link, and macOS puts the standard temp directory behind `/var -> /private/var`.
    Fixtures that treat a temp directory as a project root would therefore fail there
    for a reason that has nothing to do with the code under test, which is exactly how
    a suite comes to have never run on one of its supported platforms.
    """
    return str(Path(tempfile.gettempdir()).resolve())


def main() -> int:
    scratch = temporary_directory()
    if scratch != tempfile.gettempdir():
        print(f"check: temp directory canonicalized to {scratch}", flush=True)
    # An exact import path and temp directory, not inherited ones: a gate must not
    # depend on the operator's profile to decide what it imports or where it writes.
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "TMPDIR": scratch,
        "TEMP": scratch,
        "TMP": scratch,
    }
    for arguments in GATES:
        print(f"check: python {' '.join(arguments)}", flush=True)
        completed = subprocess.run(
            [sys.executable, *arguments], cwd=ROOT, env=environment, check=False
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
