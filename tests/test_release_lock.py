"""`relkit release unlock` removes a stale lock only after proving the owner is gone."""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from test_json_cli import invoke as invoke_cli
from test_release import ReleaseFixture

from releasekit import processes, storage
from releasekit.release import coordinator, lockfile
from releasekit.release.backend import Pending
from releasekit.result import Result


def dead_pid() -> int:
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    return finished.pid


class LockFixture(ReleaseFixture):
    def setUp(self):
        super().setUp()
        self.lock = storage.service_root(self.root) / "release.lock"

    def write_lock(self, **fields) -> dict:
        record = {
            "pid": dead_pid(),
            "tag": "v1.0.0",
            "root": str(self.root),
            "host": socket.gethostname(),
            **fields,
        }
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        self.lock.write_text(json.dumps(record), encoding="utf-8")
        return record

    def unlock(self, **kwargs):
        result = Result()
        code, output = self.invoke("unlock", publish=False, result=result, **kwargs)
        return code, output, result

    def touch_after_lock(self, path: Path, seconds: float = 2.0) -> None:
        stamp = self.lock.stat().st_mtime + seconds
        os.utime(path, (stamp, stamp))


class UnlockProofTests(LockFixture):
    def test_a_lock_with_a_dead_owner_is_removed_and_a_release_can_then_run(self):
        self.write_lock()
        code, output, result = self.unlock()
        self.assertEqual(0, code, output)
        self.assertFalse(self.lock.exists())
        self.assertTrue(result.data["unlock"]["removed"])
        self.assertIn("is not running", output)
        with patch.object(coordinator, "_audit"):
            code, output = self.invoke()
        self.assertEqual(0, code, output)

    def test_a_lock_whose_owner_is_running_is_refused_and_the_owner_is_left_alone(self):
        owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(owner.wait)
        self.addCleanup(owner.kill)
        self.write_lock(pid=owner.pid)
        before = self.lock.read_bytes()
        code, output, _ = self.unlock()
        self.assertEqual(2, code, output)
        self.assertIn(str(self.lock), output)
        self.assertIn(f"PID {owner.pid} is running", output)
        self.assertEqual(before, self.lock.read_bytes())
        self.assertIsNone(owner.poll(), "unlock never signals a process")

    def test_this_process_as_owner_is_refused(self):
        self.write_lock(pid=os.getpid())
        code, output, _ = self.unlock()
        self.assertEqual(2, code, output)
        self.assertTrue(self.lock.exists())

    def test_liveness_that_cannot_be_checked_is_not_proof(self):
        self.write_lock()
        with patch.object(lockfile, "process_alive", return_value=None):
            code, output, _ = self.unlock()
        self.assertEqual(2, code, output)
        self.assertIn("could not be checked", output)
        self.assertTrue(self.lock.exists())

    def test_an_unreadable_or_malformed_owner_record_is_refused_with_the_path(self):
        for content in ("other run", "{}", json.dumps({"pid": "x", "tag": "v1.0.0", "root": "r"})):
            with self.subTest(content=content):
                self.lock.parent.mkdir(parents=True, exist_ok=True)
                self.lock.write_text(content, encoding="utf-8")
                code, output, _ = self.unlock()
                self.assertEqual(2, code, output)
                self.assertIn(str(self.lock), output)
                self.assertIn("owner record is unreadable or malformed", output)
                self.assertEqual(content, self.lock.read_text(encoding="utf-8"))

    def test_a_lock_taken_for_another_root_tag_or_host_is_refused(self):
        cases = {
            "another root": ({"root": str(self.root.parent)}, "not for"),
            "another tag": ({"tag": "v0.9.0"}, "held for v0.9.0"),
            "another host": ({"host": "some-other-host.invalid"}, "some-other-host.invalid"),
        }
        for label, (fields, expected) in cases.items():
            with self.subTest(label):
                self.write_lock(**fields)
                code, output, _ = self.unlock()
                self.assertEqual(2, code, output)
                self.assertIn(expected, output)
                self.assertTrue(self.lock.exists())

    def test_omitting_the_version_unlocks_whatever_tag_holds_the_lock(self):
        self.write_lock(tag="v0.9.0")
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            code = coordinator.run(self.root, "unlock", "", runner=self.runner, github=self.github)
        self.assertEqual(0, code, stream.getvalue())
        self.assertFalse(self.lock.exists())

    def test_no_lock_is_reported_without_error(self):
        code, output, result = self.unlock()
        self.assertEqual(0, code, output)
        self.assertFalse(result.data["unlock"]["removed"])
        self.assertIn("no release lock exists", output)

    def test_an_older_lock_without_a_host_is_judged_on_the_evidence_it_has(self):
        record = self.write_lock()
        del record["host"]
        self.lock.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(0, self.unlock()[0])
        self.assertFalse(self.lock.exists())

    def test_a_lock_that_changes_while_it_is_checked_is_not_removed(self):
        self.write_lock()
        real = lockfile.process_alive

        def alive_then_swap(pid):
            answer = real(pid)
            self.write_lock(tag="v1.0.0", pid=os.getpid())
            return answer

        with patch.object(lockfile, "process_alive", alive_then_swap):
            code, output, _ = self.unlock()
        self.assertEqual(2, code, output)
        self.assertIn("changed while it was being checked", output)
        self.assertTrue(self.lock.exists())

    def test_a_lock_a_real_run_takes_records_its_owner_identity(self):
        record = json.loads(lockfile.acquire(self.root, "v1.0.0").read_text())
        self.assertEqual(os.getpid(), record["pid"])
        self.assertEqual(socket.gethostname(), record["host"])
        self.assertEqual(str(self.root), record["root"])
        self.assertIn("at", record)
        with self.assertRaisesRegex(Exception, "relkit release unlock"):
            lockfile.acquire(self.root, "v1.0.0")

    def test_unlock_accepts_no_publication_flags(self):
        self.write_lock()
        for kwargs in ({"plan_hash": "0" * 64}, {"no_download": True}, {"reason": "x"}):
            with self.subTest(kwargs=kwargs):
                code, output, _ = self.unlock(**kwargs)
                self.assertEqual(2, code, output)
                self.assertIn("accepts only an optional VERSION", output)
        self.assertEqual(2, self.invoke("unlock", publish=True)[0])
        self.assertTrue(self.lock.exists())


class UnlockEvidenceTests(LockFixture):
    def retain_lock_after_unconfirmed_cleanup(self):
        def fail(runner, github, state, path, workspace, no_download, checked=False):
            with patch.object(processes, "run", side_effect=processes.CleanupError("probe")):
                runner.call(["controlled-cleanup-probe"])

        with patch.object(coordinator, "_prepare", side_effect=fail):
            code, output = self.invoke()
        self.assertEqual(1, code, output)
        self.assertTrue(self.lock.exists())
        record = json.loads(self.lock.read_text())
        stamp = self.lock.stat()
        # The test process owns that lock; model the owner having died, keeping the
        # receipt and lock times the way a real crash would.
        self.write_lock(pid=dead_pid())
        os.utime(self.lock, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        return record

    def test_unconfirmed_cleanup_is_never_proven_by_a_stopped_pid(self):
        self.retain_lock_after_unconfirmed_cleanup()
        receipt = coordinator._state_path(self.root, "v1.0.0")
        code, output, _ = self.unlock()
        self.assertEqual(2, code, output)
        self.assertIn(str(receipt), output)
        self.assertIn("could not be confirmed stopped", output)
        self.assertTrue(self.lock.exists())

    def test_an_unconfirmed_receipt_older_than_the_lock_is_not_this_locks_evidence(self):
        self.retain_lock_after_unconfirmed_cleanup()
        receipt = coordinator._state_path(self.root, "v1.0.0")
        self.touch_after_lock(receipt, -100)
        code, output, _ = self.unlock()
        self.assertEqual(0, code, output)
        self.assertFalse(self.lock.exists())

    def test_a_candidate_attempt_with_unconfirmed_cleanup_blocks_removal(self):
        attempts = storage.service_root(self.root) / "candidates" / "v1.0.0"
        attempts.mkdir(parents=True)
        self.write_lock()
        attempt = attempts / ("a" * 32 + ".json")
        attempt.write_text(json.dumps({"status": "cleanup-unconfirmed"}), encoding="utf-8")
        self.touch_after_lock(attempt)
        code, output, _ = self.unlock()
        self.assertEqual(2, code, output)
        self.assertIn(str(attempt), output)
        self.assertTrue(self.lock.exists())

    def test_a_receipt_that_cannot_be_read_is_not_proof(self):
        self.write_lock()
        receipt = coordinator._state_path(self.root, "v1.0.0")
        receipt.parent.mkdir(parents=True)
        receipt.write_text("{not json", encoding="utf-8")
        self.touch_after_lock(receipt)
        code, output, _ = self.unlock()
        self.assertEqual(2, code, output)
        self.assertIn("cannot be read", output)
        self.assertIn(str(receipt), output)

    def crash_mid_stage(self):
        with patch.object(coordinator, "_prepare", side_effect=Pending("interrupted probe")):
            self.assertEqual(3, self.invoke()[0])
        self.write_lock()
        receipt = coordinator._state_path(self.root, "v1.0.0")
        saved = json.loads(receipt.read_text())
        saved["stage"] = "local-checks"
        saved["stages"] = {"local-checks": "running"}
        receipt.write_text(json.dumps(saved), encoding="utf-8")
        self.touch_after_lock(receipt)
        return receipt

    def test_a_dead_owner_left_mid_command_is_refused_where_commands_can_outlive_it(self):
        receipt = self.crash_mid_stage()
        with patch.object(lockfile, "jobs_end_with_owner", return_value=False):
            code, output, _ = self.unlock()
        self.assertEqual(2, code, output)
        self.assertIn(str(receipt), output)
        self.assertIn("mid-stage (local-checks)", output)
        self.assertTrue(self.lock.exists())

    def test_a_dead_owner_left_mid_command_is_removed_where_its_job_ends_with_it(self):
        self.crash_mid_stage()
        with patch.object(lockfile, "jobs_end_with_owner", return_value=True):
            code, output, _ = self.unlock()
        self.assertEqual(0, code, output)
        self.assertFalse(self.lock.exists())

    def test_the_platform_rule_matches_the_job_object_the_owner_runs_commands_in(self):
        self.assertEqual(sys.platform == "win32", lockfile.jobs_end_with_owner())


class UnlockEnvelopeTests(LockFixture):
    def test_the_json_envelope_reports_the_removal_and_the_refusal(self):
        self.write_lock()
        code, value, _ = invoke_cli(["--json", "release", "unlock", "--root", str(self.root)])
        self.assertEqual(0, code, value)
        self.assertEqual(["release", "unlock"], value["command"])
        self.assertTrue(value["data"]["unlock"]["removed"])
        self.assertEqual(str(self.lock), value["data"]["unlock"]["lock"])
        self.write_lock(pid=os.getpid())
        code, value, _ = invoke_cli(["--json", "release", "unlock", "--root", str(self.root)])
        self.assertEqual(2, code, value)
        self.assertEqual("release_error", value["errors"][0]["code"])
        self.assertIn(str(self.lock), value["errors"][0]["message"])
