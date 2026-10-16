from __future__ import annotations

import contextlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from test_release import ReleaseFixture

from releasekit import cli
from releasekit.release import coordinator
from releasekit.release.backend import CommandError
from releasekit.release.failure import FAILURE_LINES, ProjectCommandError, failure_lines

UNITTEST_OUTPUT = """\
..F.
======================================================================
FAIL: test_widget_total (suite.test_widget.WidgetTests.test_widget_total)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "suite/test_widget.py", line 12, in test_widget_total
    self.assertEqual(3, widget.total())
AssertionError: 3 != 4

----------------------------------------------------------------------
Ran 4 tests in 0.002s

FAILED (failures=1)
"""

PYTEST_OUTPUT = """\
============================= test session starts =============================
collected 3 items

tests/test_widget.py .F.                                                 [100%]

================================== FAILURES ===================================
________________________________ test_total ___________________________________

    def test_total():
>       assert total() == 4
E       assert 3 == 4

tests/test_widget.py:9: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_widget.py::test_total - assert 3 == 4
========================= 1 failed, 2 passed in 0.03s =========================
"""


class FailureLinesTests(unittest.TestCase):
    def test_unittest_output_names_the_failing_test_and_the_assertion(self):
        lines = failure_lines(UNITTEST_OUTPUT)
        self.assertIn(
            "FAIL: test_widget_total (suite.test_widget.WidgetTests.test_widget_total)", lines
        )
        self.assertIn("AssertionError: 3 != 4", lines)
        self.assertIn("FAILED (failures=1)", lines)
        self.assertNotIn("Ran 4 tests in 0.002s", lines)

    def test_pytest_output_names_the_failing_test(self):
        lines = failure_lines(PYTEST_OUTPUT)
        self.assertIn("FAILED tests/test_widget.py::test_total - assert 3 == 4", lines)

    def test_named_failures_win_when_there_are_more_matches_than_room(self):
        noise = "".join(f"  File x.py, line {i}, raised ValueError\n" for i in range(30))
        named = "".join(f"FAIL: test_{i} (suite.Case.test_{i})\n" for i in range(3))
        lines = failure_lines(noise + named)
        self.assertEqual(FAILURE_LINES, len(lines))
        self.assertEqual(3, sum(line.startswith("FAIL: test_") for line in lines))

    def test_the_answer_is_capped_and_keeps_the_output_order(self):
        output = "".join(f"error: step {i} broke\n" for i in range(50))
        lines = failure_lines(output)
        self.assertEqual([f"error: step {i} broke" for i in range(FAILURE_LINES)], lines)

    def test_without_a_marker_the_tail_is_the_fallback(self):
        output = "".join(f"line {i}\n" for i in range(30))
        self.assertEqual([f"line {i}" for i in range(20, 30)], failure_lines(output))

    def test_a_clean_summary_and_a_passing_test_name_are_not_failures(self):
        output = (
            "test_error_handling ... ok\n"
            "TestErrorHandling PASSED\n"
            "summary: 12 passed, 0 failed, 0 errors\n"
            "done\n"
        )
        # Nothing matches, so the fallback applies and every line is shown as the tail.
        self.assertEqual(output.splitlines(), failure_lines(output))

    def test_color_codes_and_very_long_lines_are_tamed(self):
        lines = failure_lines("\x1b[31mFAIL: red test\x1b[0m\nerror: " + "x" * 500 + "\n")
        self.assertEqual("FAIL: red test", lines[0])
        self.assertLessEqual(len(lines[1]), 200)

    def test_empty_output_has_no_lines(self):
        self.assertEqual([], failure_lines(""))
        self.assertEqual([], failure_lines("\n  \n"))


class ProjectCommandErrorTests(unittest.TestCase):
    def error(self, **streams):
        result = subprocess.CompletedProcess(["x"], 3, **streams)
        return ProjectCommandError(
            "project command 2/4",
            ["{python}", "tools/check.py", "--fast"],
            CommandError("x", result),
            None,
        )

    def test_it_is_still_a_command_error_that_keeps_the_process_result(self):
        error = self.error(stdout=b"", stderr=b"FAIL: t\n")
        self.assertIsInstance(error, CommandError)
        self.assertEqual(3, error.result.returncode)

    def test_it_names_the_configured_command_exit_code_and_evidence_before_the_log(self):
        result = subprocess.CompletedProcess(["x"], 1, stdout=b"", stderr=UNITTEST_OUTPUT.encode())
        message = str(
            ProjectCommandError(
                "project command 2/4",
                ["{python}", "tools/check.py", "--fast"],
                CommandError("x", result),
                Path("run.log"),
            )
        )
        positions = [
            message.index("project command 2/4 `{python} tools/check.py --fast` exited 1"),
            message.index("FAIL: test_widget_total"),
            message.index("full output: run.log"),
        ]
        self.assertEqual(sorted(positions), positions)

    def test_both_streams_are_searched_and_undecodable_bytes_do_not_break_it(self):
        message = str(self.error(stdout=b"FAILED tests/a.py::t\n", stderr=b"\xff\xfe noise\n"))
        self.assertIn("FAILED tests/a.py::t", message)

    def test_a_very_long_command_is_shortened(self):
        result = subprocess.CompletedProcess(["x"], 1, stdout=b"", stderr=b"")
        message = str(
            ProjectCommandError("c", ["tool", "a" * 1000], CommandError("x", result), None)
        )
        self.assertLess(len(message), 400)


class FailedProjectCheckTests(ReleaseFixture):
    """A check that fails in a real run says what failed without opening the log."""

    def fail_check(self):
        (self.root / "check.py").write_text(
            "import sys\n"
            "print('..F.')\n"
            "print('FAIL: test_widget_total (suite.test_widget.WidgetTests.test_widget_total)',"
            " file=sys.stderr)\n"
            "print('AssertionError: 3 != 4', file=sys.stderr)\n"
            "sys.exit(1)\n"
        )
        self.commit()

    def test_the_refusal_names_the_command_the_exit_code_and_the_failed_test_before_the_log(self):
        self.fail_check()
        with patch.object(coordinator, "_audit"):
            code, output = self.invoke()
        self.assertEqual(1, code, output)
        message = self.receipt()["error"]
        order = [
            message.index("project command 1/1 `{python} check.py` exited 1"),
            message.index(
                "FAIL: test_widget_total (suite.test_widget.WidgetTests.test_widget_total)"
            ),
            message.index("AssertionError: 3 != 4"),
            message.index("full output: "),
        ]
        self.assertEqual(sorted(order), order)
        log = message.split("full output: ", 1)[1].splitlines()[0]
        self.assertTrue(log.endswith("run.log"), log)
        self.assertIn("exit=1", Path(log).read_text(encoding="utf-8"))
        # The same text reaches the operator and the structured result.
        self.assertIn("FAIL: test_widget_total", output)

    def test_a_failed_release_audit_names_its_findings_before_the_log(self):
        def failing(root, **kwargs):
            kwargs["result"].error("check_failed", "notes.md: home-directory (detail)")
            kwargs["result"].error("check_failed", "guide.md: absolute-local-link")
            return 1

        with patch.object(coordinator.publication, "run", side_effect=failing):
            code, _ = self.invoke()
        self.assertEqual(1, code)
        message = self.receipt()["error"]
        self.assertTrue(message.startswith("worktree publication audit failed (exit 1)\n"), message)
        self.assertLess(message.index("notes.md: home-directory"), message.index("full output: "))
        self.assertLess(
            message.index("guide.md: absolute-local-link"), message.index("full output: ")
        )

    def test_json_carries_the_same_evidence_in_the_error_message(self):
        self.fail_check()
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(coordinator, "_audit"),
            patch.object(coordinator, "Runner", return_value=self.runner),
            patch.object(coordinator, "GitHub", return_value=self.github),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(
                ["release", "run", "v1.0.0", "--publish", "--json", "--root", str(self.root)]
            )
        value = json.loads(stdout.getvalue())
        self.assertEqual(1, code)
        message = value["errors"][0]["message"]
        self.assertIn("exited 1", message)
        self.assertIn("FAIL: test_widget_total", message)


if __name__ == "__main__":
    unittest.main()
