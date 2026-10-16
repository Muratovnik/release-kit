"""The audit's stages are timestamped on stderr, so a slow audit can be attributed."""

from __future__ import annotations

import contextlib
import io
import re
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


if __name__ == "__main__":
    unittest.main()
