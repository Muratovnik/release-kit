"""Show that a long step is still running, without changing anything that is recorded.

A release spends minutes inside single commands whose output goes to the release log,
so the terminal says nothing while the work happens and an operator cannot tell a slow
check from a hung one. This says which step is running and how long it has been.

Three rules keep it from becoming another thing to distrust. It writes to the process's
original stderr, so a step that redirects its output into the log is unaffected and the
log stays byte-comparable evidence. It writes nothing at all unless that stderr is a
terminal, so CI output and captured runs keep their exact present shape. And it reports
elapsed time rather than a percentage: how long a check will take is not known, and a
progress bar that invents a fraction is a claim this tool has no basis to make.
"""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager

INTERVAL = 15.0
WIDTH = 78


def _terminal():
    """The real stderr, which a redirected step cannot capture."""
    stream = sys.__stderr__
    try:
        return stream if stream is not None and stream.isatty() else None
    except (AttributeError, ValueError):
        return None


def _write(stream, text: str) -> None:
    try:
        stream.write(text)
        stream.flush()
    except (OSError, ValueError):
        pass


@contextmanager
def step(label: str, *, interval: float = INTERVAL):
    stream = _terminal()
    if stream is None:
        yield
        return
    started = time.monotonic()
    finished = threading.Event()

    def tick() -> None:
        while not finished.wait(interval):
            elapsed = int(time.monotonic() - started)
            _write(stream, f"\r{f'  {label} … {elapsed}s'[:WIDTH]:<{WIDTH}}")

    worker = threading.Thread(target=tick, name="relkit-progress", daemon=True)
    worker.start()
    try:
        yield
    finally:
        finished.set()
        worker.join(timeout=1.0)
        _write(stream, "\r" + " " * WIDTH + "\r")


def stage(position: int, total: int, name: str) -> str:
    """The stage line itself is ordinary output: it belongs in a captured log."""
    return f"relkit release: [{position}/{total}] {name}"
