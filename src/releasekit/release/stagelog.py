"""Stage lines in the release logs, so the minutes of a run can be attributed afterwards.

A release spends most of its time in a few long steps (the project's checks, the audits,
a download of a few hundred megabytes, a pre-push hook) whose output goes to the owned
log. Without times in that log a gap between two commands belongs to nothing. This wraps
`releasekit.stages.StageLog` so each coordinator stage writes `start`, a heartbeat while
it runs and `done` or `failed` with its duration into the log the runner is writing.

The stage names are documented in docs/release-coordinator.md. They are labels for a
reader, not identifiers: `state.json` keeps its own, older stage record.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from .. import stages, storage

# Longest silence in a log while a stage runs. A module value so a test can shorten it.
HEARTBEAT = stages.HEARTBEAT


class _Appender:
    """A text sink that appends to the logs of the moment and holds no handle.

    `Runner.call` appends to the same file in short-lived binary opens, and the audit
    redirects its own output into it, so a long-lived handle here would only be one more
    writer to keep consistent. Logging is observation: a log that cannot be written is
    skipped, never a reason to stop a release.
    """

    def __init__(self, runner) -> None:
        self._runner = runner

    def _targets(self) -> list[Path]:
        found: list[Path] = []
        for path in (getattr(self._runner, "log", None), getattr(self._runner, "run_log", None)):
            if path is not None and Path(path) not in found:
                found.append(Path(path))
        return found

    def write(self, text: str) -> int:
        for path in self._targets():
            try:
                with storage.checked(path).open("ab+") as stream:
                    # A command's captured output need not end in a newline, and a stage
                    # line glued to it would be unreadable; start the line on its own.
                    stream.seek(0, 2)
                    end = stream.tell()
                    if end:
                        stream.seek(end - 1)
                        if stream.read(1) != b"\n":
                            stream.write(b"\n")
                    stream.write(text.encode("utf-8"))
            except (OSError, ValueError, storage.StorageError):
                continue
        return len(text)

    def flush(self) -> None:
        return None


def stage(runner, name: str | None):
    """Context manager that logs one stage of `runner`; a no-op without a log or a name."""
    if not name or (
        getattr(runner, "log", None) is None and getattr(runner, "run_log", None) is None
    ):
        return contextlib.nullcontext(stages.Stage())
    return stages.StageLog(_Appender(runner), heartbeat=HEARTBEAT).stage(name)
