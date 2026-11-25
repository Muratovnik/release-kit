"""Run this repository's declared verification gates as one command.

The release coordinator executes configured checks as argv arrays without a shell,
so a gate that needs `src` on the import path needs a runner rather than an exported
variable. This is that runner, and it runs exactly the list AGENTS.md declares. The
publication audits are not here: the coordinator runs the worktree and history audits
itself. Native CI provisions pinned engines for its explicit acceptance tests.

Where a machine-wide heavy-process gate is installed, the test suite goes through it:
the suite starts a process per core, each building real repositories, and two such runs
beside other heavy work fail tests that pass alone. The gate's `heavy-gate.json` on
PATH names how to start it; the suite then runs under the gate's `run` with the kind
`.heavy-gate.toml` declares, and the worker count K the gate grants reaches
`tools/parallel_tests.py` as `--jobs`. A run that finds no room exits 75 with the
gate's reason, at once in an agent's session, where `HEAVY_GATE_WAIT=1` waits instead.
Without the gate, on any system, the suite runs directly with the runner's default.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import parallel_tests

from releasekit import processes

GATE_TIMEOUT_SECONDS = 3600
TESTS = "tools/parallel_tests.py"
GATES = (
    # Same discovery, same tests, spread over processes; see tools/parallel_tests.py.
    [TESTS],
    ["-m", "ruff", "check", "src", "tests", "tools"],
    ["-m", "ruff", "format", "--check", "src", "tests", "tools"],
)
#: The file beside the gate's entry points that names how to start it without a shell.
HEAVY_GATE_ENTRY = "heavy-gate.json"
HEAVY_GATE_PROTOCOL = 1
#: The kind `.heavy-gate.toml` declares for the test suite.
HEAVY_GATE_KIND = "tests"
#: The gate's exit code for a run it found no room for; the runner never exits with it.
HEAVY_GATE_NO_ROOM = 75


def temporary_directory() -> str:
    """A temp directory with no link in its path, because the suite writes service paths.

    `storage.checked` deliberately refuses a service path whose components include a
    link, and macOS puts the standard temp directory behind `/var -> /private/var`.
    Fixtures that treat a temp directory as a project root would therefore fail there
    for a reason that has nothing to do with the code under test, which is exactly how
    a suite comes to have never run on one of its supported platforms.
    """
    return str(Path(tempfile.gettempdir()).resolve())


def heavy_gate(environment: Mapping[str, str]) -> list[str] | None:
    """The command line that starts the installed heavy-process gate, or None.

    The first `heavy-gate.json` on PATH decides. One that cannot be used is reported
    and the tests run without the gate, as the gate itself does when it fails: the
    gate decides when heavy work runs, never whether it can.
    """
    for directory in environment.get("PATH", "").split(os.pathsep):
        if not directory.strip():
            continue
        entry = Path(directory) / HEAVY_GATE_ENTRY
        if not entry.is_file():
            continue
        try:
            document = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(
                f"check: {entry} cannot be read ({error}); tests run without the gate", flush=True
            )
            return None
        argv = document.get("argv") if isinstance(document, dict) else None
        if (
            not isinstance(document, dict)
            or document.get("protocol") != HEAVY_GATE_PROTOCOL
            or not isinstance(argv, list)
            or not argv
            or not all(isinstance(word, str) and word for word in argv)
        ):
            print(
                f"check: {entry} names no protocol {HEAVY_GATE_PROTOCOL} gate command; "
                "tests run without the gate",
                flush=True,
            )
            return None
        return argv
    return None


def command(arguments: Sequence[str], environment: Mapping[str, str]) -> list[str]:
    """The command line of one gate: the test suite through the heavy-process gate if any."""
    if arguments[:1] != [TESTS] or (gate := heavy_gate(environment)) is None:
        return [sys.executable, *arguments]
    return [
        *gate,
        "run",
        "--kind",
        HEAVY_GATE_KIND,
        "--want",
        str(parallel_tests.default_jobs()),
        "--",
        sys.executable,
        *arguments,
        # The gate replaces `{k}` with the K it granted.
        "--jobs={k}",
    ]


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
        argv = command(arguments, environment)
        gated = argv != [sys.executable, *arguments]
        through = f" through the heavy-process gate, kind {HEAVY_GATE_KIND}" if gated else ""
        print(f"check: python {' '.join(arguments)}{through}", flush=True)
        # Relay cancellation so a nested cleanup failure reaches the check owner.
        completed = processes.run(
            argv,
            cwd=ROOT,
            env=environment,
            check=False,
            timeout=GATE_TIMEOUT_SECONDS,
        )
        if completed.returncode == HEAVY_GATE_NO_ROOM and gated:
            print(
                "check: the heavy-process gate found no room for the tests (its reason is "
                "above); run again later, or set HEAVY_GATE_WAIT=1 to wait for room",
                flush=True,
            )
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
