"""Say what a failed project command printed, so nobody has to open a long log to find out.

A project's checks are arbitrary commands, and a run log holds all of their output.
When one fails, the refusal names the command and its exit status, then the few lines of
its output that most likely say what failed, and only then the log. The selection is
generic on purpose: it recognises the words failing tools print, never a tool or a
project.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .backend import CommandError

FAILURE_LINES = 10
LINE_WIDTH = 200
COMMAND_WIDTH = 300

# Words a failing command commonly prints, whatever the language: a failed test, an
# error type, a traceback.
_MARKER = re.compile(
    r"\b(?:FAIL|FAILED|FAILURE|ERROR|FATAL|Traceback|panic|Exception)\b"
    r"|\b\w*Error\b|\berror:|\bfailed\b|\bfailures?\b"
)
# A line that begins with one of these names the failed test or step itself.
_NAMED = re.compile(r"^\W*(?:FAIL|FAILED|ERROR)\b")
# A summary that reports nothing wrong ("0 failed") is not a failure.
_NOTHING_WRONG = re.compile(r"\b0 (?:failed|failures?|errors?)\b")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def failure_lines(output: str, *, limit: int = FAILURE_LINES) -> list[str]:
    """The lines of a failed command's output most likely to say what failed.

    Lines that begin with FAIL, FAILED or ERROR name the failure, so they win when
    there are more matches than room; other matching lines fill what is left, and the
    answer keeps the output's own order. When nothing matches, the last lines are the
    best guess, since a failing tool usually reports last.
    """
    lines = [
        _ANSI.sub("", line).rstrip()[:LINE_WIDTH] for line in output.splitlines() if line.strip()
    ]
    matching = [
        index
        for index, line in enumerate(lines)
        if _MARKER.search(line) and not _NOTHING_WRONG.search(line)
    ]
    if not matching:
        return lines[-limit:]
    named = [index for index in matching if _NAMED.match(lines[index])][:limit]
    others = [index for index in matching if index not in named][: limit - len(named)]
    return [lines[index] for index in sorted(named + others)]


class ProjectCommandError(CommandError):
    """A configured check, build or smoke command failed; the message carries the evidence."""

    def __init__(self, label: str, argv: list[str], error: CommandError, log: Path | None):
        shown = subprocess.list2cmdline(argv)
        if len(shown) > COMMAND_WIDTH:
            shown = shown[: COMMAND_WIDTH - 3] + "..."
        result = error.result
        output = "\n".join(
            value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            for value in (result.stderr, result.stdout)
        )
        message = f"{label} `{shown}` exited {result.returncode}"
        if lines := failure_lines(output):
            message += ":\n" + "\n".join(f"  {line}" for line in lines)
        if log is not None:
            message += f"\nfull output: {log}"
        super().__init__(argv[0], result, message)
