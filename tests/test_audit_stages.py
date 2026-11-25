"""The audit's stages are timestamped on stderr, so a slow audit can be attributed."""

from __future__ import annotations

import contextlib
import io
import json
import re
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cli_process import Repository, relkit

from releasekit import cli, config, engines, publication
from releasekit.exposure.audit import Report

STAGE = re.compile(
    r"^\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d\] stage (?P<name>\S+): "
    r"(?P<what>start|done \(\d+\.\ds\)|failed \(\d+\.\ds\)|running \(\d+s\))$"
)


class AuditStageTests(Repository):
    def stages_in(self, text):
        return [m for line in text.splitlines() if (m := STAGE.match(line))]

    def test_policy_rules_are_a_timestamped_stage_on_stderr_and_never_on_stdout(self):
        for arguments in (["audit", "--no-download"], ["audit", "--no-download", "--json"]):
            with self.subTest(arguments=arguments):
                completed = relkit(self.root, *arguments)
                self.assertEqual([], self.stages_in(completed.stdout))
                found = [
                    (m["name"], m["what"].split()[0]) for m in self.stages_in(completed.stderr)
                ]
                self.assertEqual([("policy-rules", "start"), ("policy-rules", "done")], found)

    def test_history_is_its_own_stage(self):
        completed = relkit(self.root, "audit", "--no-download", "--history")
        names = [m["name"] for m in self.stages_in(completed.stderr) if m["what"] == "start"]
        self.assertEqual(["policy-rules", "history"], names)

    def test_each_engine_is_a_stage_and_an_operational_exit_marks_it_failed(self):
        stderr = io.StringIO()
        with (
            patch.object(publication.config_module, "load", return_value=config.Config(Path.cwd())),
            patch.object(publication.audit, "scan", return_value=Report()),
            patch.object(publication.toolchain, "prepare"),
            patch.object(engines, "betterleaks", return_value=17),
            patch.object(engines, "lychee", return_value=0),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(["audit", "--json"])
        self.assertEqual(2, code)
        outcomes = {
            m["name"]: m["what"].split()[0]
            for m in self.stages_in(stderr.getvalue())
            if m["what"] != "start"
        }
        self.assertEqual(
            {
                "policy-rules": "done",
                "provision": "done",
                "snapshot": "done",
                "betterleaks": "failed",
                "lychee": "done",
            },
            outcomes,
        )

    def test_a_findings_exit_code_is_a_completed_stage_not_a_failed_one(self):
        stderr = io.StringIO()
        with (
            patch.object(publication.config_module, "load", return_value=config.Config(Path.cwd())),
            patch.object(publication.audit, "scan", return_value=Report()),
            patch.object(publication.toolchain, "prepare"),
            patch.object(engines, "betterleaks", return_value=engines.BETTERLEAKS_FINDINGS_EXIT),
            patch.object(engines, "lychee", return_value=engines.LYCHEE_FINDINGS_EXIT),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(1, cli.main(["audit", "--json"]))
        self.assertNotIn("failed (", stderr.getvalue())

    def test_the_report_names_the_elapsed_time_and_keeps_overlapping_stages_apart(self):
        """Field case: the time read off a large staged audit disagreed with the wall clock.

        The policy checks run beside the engines, so stage times added up give more
        than the audit took. The report names the audit's own elapsed time, each
        engine's time, and which stages ran at the same time as the others.
        """
        pause = 1.0

        def slow(value):
            def call(*_args, **_kwargs):
                time.sleep(pause)
                return value

            return call

        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(publication.config_module, "load", return_value=config.Config(Path.cwd())),
            patch.object(publication.audit, "scan", side_effect=slow(Report())),
            patch.object(publication.toolchain, "prepare"),
            patch.object(engines, "betterleaks", side_effect=slow(0)),
            patch.object(engines, "lychee", return_value=0),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            started = time.monotonic()
            code = cli.main(["audit", "--json"])
            wall = time.monotonic() - started
        self.assertEqual(0, code, stderr.getvalue())
        timing = json.loads(stdout.getvalue())["data"]["timing"]
        concurrent = {item["name"]: item["concurrent"] for item in timing["stages"]}
        self.assertEqual(
            {
                "provision": False,
                "snapshot": False,
                "betterleaks": False,
                "lychee": False,
                "policy-rules": True,
            },
            concurrent,
        )
        seconds = {item["name"]: item["seconds"] for item in timing["stages"]}
        elapsed = timing["elapsed_seconds"]
        # The total is the wall clock's, not the sum of stages that overlapped.
        self.assertLessEqual(elapsed, round(wall, 1) + 0.05)
        self.assertGreaterEqual(elapsed, max(seconds.values()) - 0.05)
        self.assertLess(elapsed, sum(seconds.values()))
        self.assertGreaterEqual(seconds["betterleaks"], pause - 0.05)
        [line] = [line for line in stderr.getvalue().splitlines() if " elapsed" in line]
        self.assertRegex(
            line,
            r"^relkit audit: \d+\.\ds elapsed; provision \d+\.\ds, snapshot \d+\.\ds, "
            r"betterleaks \d+\.\ds, lychee \d+\.\ds; at the same time: policy-rules \d+\.\ds$",
        )
        self.assertIn(f"{elapsed:.1f}s elapsed", line)


if __name__ == "__main__":
    unittest.main()
