"""Timestamped stage lines, so a long command can be attributed after the fact.

An agent or CI job drives `relkit` through a pipe and reads the result later. Without
times, a sixteen-minute gap between two steps cannot be assigned to anything, and a
silent stage cannot be told from a hung one. This writes one line when a stage starts,
one when it ends and, while it runs, a heartbeat at least every `HEARTBEAT` seconds:

    [2026-10-16T12:34:56+03:00] stage betterleaks: start
    [2026-10-16T12:35:26+03:00] stage betterleaks: running (30s)
    [2026-10-16T12:35:41+03:00] stage betterleaks: done (45.2s)

The sink is a text stream. It defaults to whatever `sys.stderr` is when a line is
written, so a caller that redirects stderr into a log file, as the release coordinator
does around the publication audit, collects the same lines there. Nothing here reads a
stage's result; it only observes how long a stage took and whether it raised.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, TextIO

HEARTBEAT = 30.0


def timestamp(moment: datetime | None = None) -> str:
    """Local time with its UTC offset, to the second: `2026-10-16T12:34:56+03:00`."""
    moment = moment or datetime.now().astimezone()
    return moment.astimezone().isoformat(timespec="seconds")


class Stage:
    """The handle a `with` block receives; `fail()` marks a stage that ended without raising."""

    def __init__(self) -> None:
        self.failed = False

    def fail(self) -> None:
        self.failed = True


class StageLog:
    """Writes stage lines to one sink.

    `heartbeat` is the longest silence in seconds while a stage runs; `None` or zero
    disables it. `clock` supplies monotonic seconds and `now` the wall time of a line,
    so a test controls both. Heartbeats run on one daemon thread per running stage,
    which `stage` stops and joins before it returns.
    """

    def __init__(
        self,
        sink: TextIO | None = None,
        *,
        heartbeat: float | None = HEARTBEAT,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._sink = sink
        self._heartbeat = heartbeat
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()

    def _write(self, text: str) -> None:
        stream = self._sink if self._sink is not None else sys.stderr
        line = f"[{timestamp(self._now())}] {text}\n"
        with self._lock:
            try:
                stream.write(line)
                stream.flush()
            except (OSError, ValueError):
                pass

    def call(self, name: str, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Run `function(*args, **kwargs)` as one stage and return its result."""
        with self.stage(name):
            return function(*args, **kwargs)

    @contextmanager
    def stage(self, name: str) -> Iterator[Stage]:
        started = self._clock()
        handle = Stage()
        finished = threading.Event()
        worker = None
        self._write(f"stage {name}: start")
        if self._heartbeat and self._heartbeat > 0:

            def beat() -> None:
                while not finished.wait(self._heartbeat):
                    self._write(f"stage {name}: running ({int(self._clock() - started)}s)")

            worker = threading.Thread(target=beat, name="relkit-stage-heartbeat", daemon=True)
            worker.start()
        try:
            yield handle
        except BaseException:
            handle.fail()
            raise
        finally:
            finished.set()
            if worker is not None:
                worker.join(timeout=5.0)
            outcome = "failed" if handle.failed else "done"
            self._write(f"stage {name}: {outcome} ({self._clock() - started:.1f}s)")
