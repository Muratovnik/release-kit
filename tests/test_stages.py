from __future__ import annotations

import contextlib
import io
import re
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from releasekit import stages

LINE = re.compile(
    r"^\[(?P<time>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d)\] stage (?P<name>\S+): (?P<what>.+)$"
)


def lines(sink: io.StringIO) -> list[str]:
    return sink.getvalue().splitlines()


def wait_for(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return False


class Clock:
    """Monotonic seconds that only move when a test says so."""

    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class StageLogTests(unittest.TestCase):
    def test_start_and_done_lines_carry_an_offset_timestamp_and_the_duration(self):
        sink, clock = io.StringIO(), Clock()
        moment = datetime(2026, 10, 16, 12, 34, 56, 789000, tzinfo=timezone(timedelta(hours=3)))
        log = stages.StageLog(sink, heartbeat=None, clock=clock, now=lambda: moment)
        with log.stage("policy-rules"):
            clock.value += 2.34
        start, done = lines(sink)
        self.assertEqual("[2026-10-16T12:34:56+03:00] stage policy-rules: start", start)
        self.assertEqual("[2026-10-16T12:34:56+03:00] stage policy-rules: done (2.3s)", done)

    def test_default_timestamps_are_local_time_with_an_offset_to_the_second(self):
        sink = io.StringIO()
        with stages.StageLog(sink, heartbeat=None).stage("x"):
            pass
        for line in lines(sink):
            match = LINE.match(line)
            self.assertIsNotNone(match, line)
            parsed = datetime.fromisoformat(match["time"])
            self.assertIsNotNone(parsed.utcoffset())
            self.assertEqual(0, parsed.microsecond)

    def test_an_exception_marks_the_stage_failed_and_still_propagates(self):
        sink, clock = io.StringIO(), Clock()
        log = stages.StageLog(sink, heartbeat=None, clock=clock)
        with self.assertRaisesRegex(RuntimeError, "boom"), log.stage("lychee"):
            clock.value += 1
            raise RuntimeError("boom")
        self.assertTrue(lines(sink)[-1].endswith("stage lychee: failed (1.0s)"))

    def test_a_stage_that_ends_without_raising_can_still_be_marked_failed(self):
        sink = io.StringIO()
        with stages.StageLog(sink, heartbeat=None).stage("betterleaks") as stage:
            stage.fail()
        self.assertRegex(lines(sink)[-1], r"stage betterleaks: failed \(\d+\.\ds\)$")

    def test_call_runs_the_function_as_a_stage_and_returns_its_value(self):
        sink = io.StringIO()
        log = stages.StageLog(sink, heartbeat=None)
        self.assertEqual(7, log.call("history", lambda a, b=0: a + b, 3, b=4))
        self.assertEqual(["start", "done"], [LINE.match(x)["what"].split()[0] for x in lines(sink)])

    def test_default_sink_is_the_stderr_in_force_when_a_line_is_written(self):
        log = stages.StageLog(heartbeat=None)
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured), log.stage("overlay"):
            pass
        self.assertEqual(2, len(lines(captured)))

    def test_a_closed_sink_never_breaks_the_stage(self):
        sink = io.StringIO()
        sink.close()
        with stages.StageLog(sink, heartbeat=None).stage("x"):
            pass


class HeartbeatTests(unittest.TestCase):
    def heartbeat_threads(self):
        return [t for t in threading.enumerate() if t.name == "relkit-stage-heartbeat"]

    def test_a_long_stage_reports_that_it_is_still_running(self):
        sink, clock = io.StringIO(), Clock()
        log = stages.StageLog(sink, heartbeat=0.01, clock=clock)
        with log.stage("betterleaks"):
            clock.value += 61
            # The heartbeat reads the injected clock, so the elapsed time is exact.
            self.assertTrue(wait_for(lambda: "running (61s)" in sink.getvalue()))
            beats = [x for x in lines(sink) if "running" in x]
            self.assertTrue(beats)
            self.assertTrue(all(LINE.match(x)["name"] == "betterleaks" for x in beats))
        self.assertTrue(lines(sink)[-1].endswith("done (61.0s)"))

    def test_the_heartbeat_thread_is_stopped_when_the_stage_ends_normally_or_not(self):
        log = stages.StageLog(io.StringIO(), heartbeat=0.005)
        with log.stage("a"):
            self.assertEqual(1, len(self.heartbeat_threads()))
        self.assertEqual([], self.heartbeat_threads())
        with contextlib.suppress(ValueError), log.stage("b"):
            raise ValueError
        self.assertEqual([], self.heartbeat_threads())

    def test_nothing_is_written_after_the_stage_ends(self):
        sink = io.StringIO()
        with stages.StageLog(sink, heartbeat=0.005).stage("a"):
            time.sleep(0.03)
        settled = sink.getvalue()
        time.sleep(0.05)
        self.assertEqual(settled, sink.getvalue())

    def test_default_interval_is_at_most_thirty_seconds(self):
        self.assertLessEqual(stages.HEARTBEAT, 30.0)

    def test_a_short_stage_below_the_interval_writes_no_heartbeat(self):
        sink = io.StringIO()
        with stages.StageLog(sink, heartbeat=30.0).stage("quick"):
            pass
        self.assertEqual(2, len(lines(sink)))
        self.assertEqual([], self.heartbeat_threads())


if __name__ == "__main__":
    unittest.main()
