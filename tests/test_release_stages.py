"""The release logs say how long each stage took, so a gap in a run can be attributed."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from test_release import ReleaseFixture
from test_release_local import LocalFixture

from releasekit.release import coordinator, stagelog

LINE = re.compile(
    r"^\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d\] stage (?P<name>[a-z-]+): "
    r"(?P<event>start|running \(\d+s\)|done \(\d+\.\ds\)|failed \(\d+\.\ds\))$"
)


def stage_events(log: Path) -> list[tuple[str, str]]:
    """Every (stage, start|running|done|failed) line of a log, in order."""
    events = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if (match := LINE.match(line)) is not None:
            events.append((match["name"], match["event"].split(" ")[0]))
    return events


def finished(events: list[tuple[str, str]]) -> dict[str, list[str]]:
    outcomes: dict[str, list[str]] = {}
    for name, event in events:
        if event in {"done", "failed"}:
            outcomes.setdefault(name, []).append(event)
    return outcomes


class RunLogStageTests(ReleaseFixture):
    def run_log(self) -> Path:
        return coordinator._state_path(self.root, "v1.0.0").parent / "run.log"

    def test_every_stage_of_a_hosted_run_writes_start_and_done_with_a_duration(self):
        with patch.object(coordinator, "_audit"):
            code, output = self.invoke()
        self.assertEqual(0, code, output)
        events = stage_events(self.run_log())
        self.assertTrue(events)
        starts = [name for name, event in events if event == "start"]
        # The order is the order of the run, not alphabetical: this is what a reader
        # follows to find where a gap went.
        self.assertEqual(
            [
                "preflight",
                "local-checks",
                "worktree-audit",
                "history-audit",
                "preflight",
                "annotated-tag",
                "push",
                "ci",
                "publication-verification",
                "asset-download",
                "signatures",
                "source-snapshot",
                "application-smoke",
                "cleanup",
            ],
            starts,
        )
        outcomes = finished(events)
        for name in set(starts):
            with self.subTest(stage=name):
                self.assertEqual(["done"] * starts.count(name), outcomes[name])

    def test_a_failing_stage_is_logged_as_failed_with_its_duration(self):
        (self.root / "check.py").write_text("raise SystemExit(3)\n")
        self.commit()
        with patch.object(coordinator, "_audit"):
            code, output = self.invoke()
        self.assertNotEqual(0, code, output)
        events = stage_events(self.run_log())
        self.assertIn(("local-checks", "failed"), events)
        self.assertNotIn(("local-checks", "done"), events)
        self.assertNotIn("worktree-audit", {name for name, _ in events})

    def test_a_long_stage_writes_heartbeats_while_it_runs(self):
        (self.root / "check.py").write_text("import time\ntime.sleep(0.6)\n")
        self.commit()
        with patch.object(coordinator, "_audit"), patch.object(stagelog, "HEARTBEAT", 0.1):
            code, output = self.invoke()
        self.assertEqual(0, code, output)
        beats = [
            event
            for name, event in stage_events(self.run_log())
            if name == "local-checks" and event == "running"
        ]
        self.assertGreaterEqual(len(beats), 1)

    def test_lines_are_the_documented_timestamped_shape(self):
        with patch.object(coordinator, "_audit"):
            self.assertEqual(0, self.invoke()[0])
        lines = [
            line
            for line in self.run_log().read_text(encoding="utf-8").splitlines()
            if " stage " in line
        ]
        self.assertTrue(lines)
        self.assertTrue(all(LINE.match(line) for line in lines), lines)


class LocalPreparationStageTests(LocalFixture):
    def attempt_log(self) -> Path:
        return next(path for path in (self.root / ".git/relkit/candidates/v1.0.0").glob("*.log"))

    def test_local_preparation_logs_its_build_and_smoke_stages(self):
        self.prepare()
        events = stage_events(self.attempt_log())
        starts = [name for name, event in events if event == "start"]
        self.assertEqual(
            [
                "local-checks",
                "worktree-audit",
                "history-audit",
                "source-snapshot",
                "build",
                "candidate-smoke",
                "preflight",
            ],
            starts,
        )
        self.assertEqual({name: ["done"] for name in set(starts)}, finished(events))

    def test_publication_of_a_local_candidate_logs_publish_and_verification(self):
        value = self.prepare()
        code, output = self.publish(plan_hash=coordinator.fingerprint(value))
        self.assertEqual(0, code, output)
        log = coordinator._state_path(self.root, "v1.0.0").parent / "run.log"
        names = {name for name, event in stage_events(log) if event == "done"}
        self.assertTrue({"publish", "publication-verification", "application-smoke"} <= names)
