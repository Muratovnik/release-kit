"""Progress is for a person watching; it must not reach anything that is recorded."""

from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import patch

from releasekit import progress
from releasekit.release import coordinator


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class ProgressTests(unittest.TestCase):
    def test_nothing_is_written_when_the_terminal_is_not_one(self):
        # CI output and captured runs keep their exact present shape.
        plain = io.StringIO()
        with patch.object(sys, "__stderr__", plain), progress.step("checking", interval=0.01):
            pass
        self.assertEqual("", plain.getvalue())

    def test_a_redirected_step_cannot_capture_the_indicator(self):
        # The audit redirects stdout and stderr into the release log, which stays
        # byte-comparable evidence only if the indicator never lands in it.
        terminal, log = Terminal(), io.StringIO()
        with (
            patch.object(sys, "__stderr__", terminal),
            patch.object(sys, "stderr", log),
            patch.object(sys, "stdout", log),
            progress.step("history audit", interval=0.01),
        ):
            import time

            time.sleep(0.05)
        self.assertEqual("", log.getvalue())
        self.assertIn("history audit", terminal.getvalue())

    def test_the_line_is_cleared_when_the_step_ends(self):
        terminal = Terminal()
        with patch.object(sys, "__stderr__", terminal), progress.step("x", interval=0.01):
            import time

            time.sleep(0.05)
        self.assertTrue(terminal.getvalue().endswith("\r" + " " * progress.WIDTH + "\r"))

    def test_a_failing_terminal_does_not_fail_the_release(self):
        class Broken(Terminal):
            def write(self, _text):
                raise OSError("device disappeared")

        with patch.object(sys, "__stderr__", Broken()), progress.step("x", interval=0.01):
            import time

            time.sleep(0.05)


class StageCountingTests(unittest.TestCase):
    def state(self, publisher: str) -> dict:
        return {"plan": {"settings": {"publisher": publisher, "workflow": ""}}}

    def test_a_local_publisher_never_counts_the_stage_it_cannot_reach(self):
        order = coordinator._stages_of(self.state("github"))
        self.assertNotIn("ci", order)
        self.assertEqual("local-checks", order[0])
        self.assertEqual("application-smoke", order[-1])

    def test_every_recorded_stage_name_has_a_place_in_the_order(self):
        # A stage that is recorded but unknown here would be numbered out of nothing.
        recorded = {
            "local-checks",
            "worktree-audit",
            "history-audit",
            "annotated-tag",
            "push",
            "ci",
            "publication-verification",
            "application-smoke",
        }
        self.assertEqual(recorded, set(coordinator.STAGE_ORDER))

    def test_the_line_names_the_position_and_the_total(self):
        self.assertEqual(
            "relkit release: [3/7] history-audit", progress.stage(3, 7, "history-audit")
        )


if __name__ == "__main__":
    unittest.main()
