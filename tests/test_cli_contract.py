"""Every `relkit` command ends with one summary line, for the agents that read it through pipes.

Where the line lands and which exit code it names are properties of a real process
(buffering, descriptors), so the load-bearing checks here run `relkit` as a subprocess
and read its actual streams. `tests/test_json_stdout.py` owns the `--json` stdout rule.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from cli_process import SUMMARY, Repository, last_line, relkit

from releasekit import cli


class SummaryLineTests(Repository):
    def check_line(self, completed, command, status):
        line = last_line(completed.stdout)
        match = SUMMARY.match(line)
        self.assertIsNotNone(match, line)
        self.assertEqual(command, match["command"])
        self.assertEqual(status, match["status"])
        # The code in the line is the code the process really returned.
        self.assertEqual(completed.returncode, int(match["code"]))

    def test_audit_pass_ends_with_the_summary_on_the_last_stdout_line(self):
        completed = relkit(self.root, "audit", "--no-download")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.check_line(completed, "audit", "pass")
        self.assertIn("relkit audit: passed", completed.stdout)

    def test_audit_findings_report_fail_with_the_real_exit_code(self):
        self.leak()
        completed = relkit(self.root, "audit", "--no-download")
        self.assertEqual(1, completed.returncode)
        self.check_line(completed, "audit", "fail")
        # The findings are on stderr; the line is still what `| tail -n 1` sees.
        self.assertIn("notes.md", completed.stderr)

    def test_the_line_is_last_even_when_both_streams_share_one_pipe(self):
        self.leak()
        completed = relkit(self.root, "audit", "--no-download", merge_stderr=True)
        self.check_line(completed, "audit", "fail")

    def test_usage_errors_end_with_the_summary_and_exit_two(self):
        for arguments, command in (
            (["bogus"], "usage"),
            (["release", "bogus"], "release"),
            ([], "usage"),
            (["audit", "--unknown"], "audit"),
        ):
            with self.subTest(arguments=arguments):
                completed = relkit(self.root, *arguments)
                self.assertEqual(2, completed.returncode)
                self.check_line(completed, command, "error")
                self.assertIn("relkit: error:", completed.stderr)

    def test_json_keeps_stdout_to_the_envelope_and_puts_the_line_on_stderr(self):
        self.leak()
        completed = relkit(self.root, "audit", "--json", "--no-download")
        envelope = json.loads(completed.stdout)
        self.assertEqual(1, envelope["exit_code"])
        match = SUMMARY.match(last_line(completed.stderr))
        self.assertIsNotNone(match, completed.stderr)
        self.assertEqual(
            ("audit", "fail", 1), (match["command"], match["status"], int(match["code"]))
        )

    def test_json_usage_errors_still_get_the_line_on_stderr(self):
        completed = relkit(self.root, "release", "bogus", "--json")
        self.assertEqual(2, json.loads(completed.stdout)["exit_code"])
        match = SUMMARY.match(last_line(completed.stderr))
        self.assertIsNotNone(match, completed.stderr)
        self.assertEqual(("release", "error", 2), (match["command"], match["status"], 2))

    def test_version_has_a_line_too(self):
        completed = relkit(self.root, "--version")
        self.check_line(completed, "version", "pass")
        self.assertIn("release-kit", completed.stdout)

    def test_notes_on_stdout_stay_byte_exact_and_report_on_stderr(self):
        completed = relkit(self.root, "notes", "v1.2.0")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertNotIn("relkit notes:", completed.stdout)
        self.assertTrue(completed.stdout.startswith("## [1.2.0]"), completed.stdout)
        self.assertEqual("- a change", last_line(completed.stdout)[:10])
        match = SUMMARY.match(last_line(completed.stderr))
        self.assertIsNotNone(match, completed.stderr)
        self.assertEqual("pass", match["status"])
        missing = relkit(self.root, "notes", "v9.9.9")
        self.assertEqual("", missing.stdout)
        self.assertEqual("fail", SUMMARY.match(last_line(missing.stderr))["status"])

    def test_notes_written_to_a_file_report_on_stdout_like_every_other_command(self):
        completed = relkit(self.root, "notes", "v1.2.0", "--output", "out.md")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.check_line(completed, "notes", "pass")
        self.assertTrue((self.root / "out.md").is_file())


class StatusWordTests(unittest.TestCase):
    """The set is closed: one word per exit code, `error` for a bug or bad usage."""

    def run_with(self, handler, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(cli, "_overlay", handler),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(["overlay", *argv])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_each_exit_code_has_its_own_word(self):
        for code, word in ((0, "pass"), (1, "fail"), (2, "refused"), (3, "pending")):
            with self.subTest(code=code):
                returned, stdout, _ = self.run_with(lambda arguments, code=code: code)
                self.assertEqual(code, returned)
                match = SUMMARY.match(last_line(stdout))
                self.assertEqual(
                    ("overlay", word, str(code)), (match["command"], match["status"], match["code"])
                )

    def test_an_unexpected_exception_is_an_error_with_the_exit_code_actually_returned(self):
        def broken(arguments):
            raise RuntimeError("unexpected")

        code, stdout, stderr = self.run_with(broken)
        self.assertEqual(1, code)
        self.assertEqual("error", SUMMARY.match(last_line(stdout))["status"])
        self.assertEqual("1", SUMMARY.match(last_line(stdout))["code"])
        self.assertIn("Traceback", stderr)
        code, stdout, stderr = self.run_with(broken, "--json")
        self.assertEqual(2, code)
        self.assertEqual("2", SUMMARY.match(last_line(stderr))["code"])
        self.assertEqual("error", SUMMARY.match(last_line(stderr))["status"])
        self.assertEqual("internal_error", json.loads(stdout)["errors"][0]["code"])

    def test_an_interrupted_json_command_is_pending_like_its_exit_code(self):
        def interrupted(arguments):
            raise KeyboardInterrupt

        code, stdout, stderr = self.run_with(interrupted, "--json")
        self.assertEqual(3, code)
        self.assertEqual("pending", SUMMARY.match(last_line(stderr))["status"])
        self.assertEqual(3, json.loads(stdout)["exit_code"])


if __name__ == "__main__":
    unittest.main()
