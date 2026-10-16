"""A failed preparation keeps only what it does not own, and says which files those are."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from test_release_candidate import CandidateFixture
from test_release_local import LocalFixture

from releasekit import processes
from releasekit.release import coordinator
from releasekit.release.backend import ReleaseError
from releasekit.result import Result

LEAVES_OUTPUT_AND_FAILS = (
    "import os\n"
    "from pathlib import Path\n"
    "Path(os.environ['TEMP'], 'evidence.txt').write_text('why it failed')\n"
    "raise SystemExit(1)\n"
)


def change_check(fixture: CandidateFixture, body: str) -> None:
    """Replace the project's check and keep the CI candidate describing the new commit."""
    (fixture.root / "check.py").write_text(body, encoding="utf-8")
    fixture.commit()
    fixture.run_data["head_sha"] = fixture.sha
    fixture.record["sha"] = fixture.sha


def candidate_dirs(root: Path) -> list[Path]:
    return sorted(root.glob(".git/relkit/tmp/candidate-*"))


class ActionsCandidateCleanupTests(CandidateFixture):
    def failing_check(self, body: str) -> None:
        change_check(self, body)

    def test_a_failed_check_that_wrote_nothing_leaves_no_workspace(self):
        self.failing_check("raise SystemExit(1)\n")
        code, output = self.prepare()
        self.assertNotEqual(0, code)
        self.assertIn("project command 1/1", output, "the failure came from the check")
        self.assertEqual([], candidate_dirs(self.root), output)
        self.assertNotIn("retained unowned", output)

    def test_a_refused_candidate_manifest_leaves_nothing_behind(self):
        self.record["version"] = "2.0.0"
        code, output = self.prepare()
        self.assertNotEqual(0, code)
        self.assertIn("candidate manifest differs", output)
        self.assertEqual([], candidate_dirs(self.root), output)

    def test_output_a_project_command_left_behind_is_kept_and_named(self):
        self.failing_check(LEAVES_OUTPUT_AND_FAILS)
        result = Result()
        with patch.object(coordinator, "_audit"):
            code, output = self.invoke("prepare", publish=False, ci_run=35, result=result)
        self.assertNotEqual(0, code)
        (kept,) = candidate_dirs(self.root)
        self.assertEqual("why it failed", (kept / "project-temp" / "evidence.txt").read_text())
        self.assertIn("retained unowned or changed temporary files", output)
        self.assertIn("project-temp/evidence.txt", output)
        self.assertEqual(["project-temp/evidence.txt"], result.data["candidate"]["retained"])
        saved = json.loads(Path(result.data["candidate"]["receipt"]).read_text())
        self.assertEqual(["project-temp/evidence.txt"], saved["retained"])
        self.assertFalse((kept / "runtime").exists(), "the tool's own scratch is removed")

    def test_an_unrelated_file_in_the_workspace_is_never_swept(self):
        def foreign(runner, value, workspace, no_download):
            (workspace.path / "foreign.txt").write_text("not ours", encoding="utf-8")
            raise ReleaseError("check failed")

        with patch.object(coordinator, "_local_checks", foreign):
            code, output = self.prepare()
        self.assertNotEqual(0, code)
        (kept,) = candidate_dirs(self.root)
        self.assertEqual("not ours", (kept / "foreign.txt").read_text())
        self.assertIn("foreign.txt", output)
        self.assertFalse((kept / "project-temp").exists())

    def test_a_workspace_is_never_cleaned_after_unconfirmed_termination(self):
        def stuck(runner, value, workspace, no_download):
            (workspace.path / "project-temp" / "child-output.txt").write_text("alive?")
            raise processes.CleanupError("probe")

        with patch.object(coordinator, "_local_checks", stuck):
            code, output = self.prepare()
        self.assertEqual(2, code, output)
        (kept,) = candidate_dirs(self.root)
        self.assertTrue((kept / "project-temp" / "child-output.txt").is_file())
        self.assertTrue((kept / "runtime").is_dir())


class LocalCandidateCleanupTests(LocalFixture):
    def test_a_failed_local_check_that_wrote_nothing_leaves_no_workspace(self):
        (self.root / "check.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
        self.commit()
        with patch.object(coordinator, "_audit"):
            code, output = self.invoke("prepare", publish=False)
        self.assertNotEqual(0, code)
        self.assertEqual([], candidate_dirs(self.root), output)

    def test_local_output_a_project_command_left_behind_is_kept_and_named(self):
        (self.root / "check.py").write_text(LEAVES_OUTPUT_AND_FAILS, encoding="utf-8")
        self.commit()
        result = Result()
        with patch.object(coordinator, "_audit"):
            code, output = self.invoke("prepare", publish=False, result=result)
        self.assertNotEqual(0, code)
        (kept,) = candidate_dirs(self.root)
        self.assertTrue((kept / "project-temp" / "evidence.txt").is_file())
        self.assertIn("project-temp/evidence.txt", output)
        self.assertEqual(["project-temp/evidence.txt"], result.data["candidate"]["retained"])
