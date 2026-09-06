from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_notes import VALID
from test_release import ReleaseFixture
from test_update import POLICY, UpdateFixture, artifact_bytes, sha

from releasekit import __version__, cli, config, engines, publication, storage, update
from releasekit.exposure.audit import Finding, Report
from releasekit.release import coordinator
from releasekit.result import Result


def invoke(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    value = json.loads(stdout.getvalue())
    assert code == value["exit_code"]
    assert value["schema_version"] == 1
    assert value["tool_version"] == __version__
    return code, value, stderr.getvalue()


class JsonCliTests(unittest.TestCase):
    def test_version_and_argument_errors_are_single_envelopes(self):
        code, value, _ = invoke(["--json", "--version"])
        self.assertEqual(0, code)
        self.assertEqual(["version"], value["command"])
        for args in (["--json"], ["audit", "--json", "--unknown"], ["notes", "--json"]):
            code, value, _ = invoke(args)
            self.assertEqual(2, code)
            self.assertEqual("invalid_arguments", value["errors"][0]["code"])

    def test_version_flag_never_runs_a_mutating_command(self):
        with patch.object(cli.update, "run", side_effect=AssertionError("must not execute")):
            code, value, _ = invoke(["--version", "update", "--yes", "--json"])
        self.assertEqual(0, code)
        self.assertEqual(["version"], value["command"])

    def test_notes_preserve_unicode_and_newlines_without_git(self):
        with tempfile.TemporaryDirectory(prefix="notes with spaces ") as temporary:
            root = Path(temporary)
            (root / "CHANGELOG.md").write_text(VALID, encoding="utf-8")
            args = ["notes", "v1.2.0", "--root", str(root)]
            code, value, stderr = invoke(["--json", *args])
            self.assertEqual((0, ""), (code, stderr))
            self.assertIn("café", value["data"]["notes"])
            self.assertTrue(value["data"]["notes"].endswith("\n"))
            code, exported, _ = invoke([*args, "--json", "--output", "export.md"])
            self.assertEqual(0, code)
            self.assertEqual(str(root / "export.md"), exported["data"]["output"])
            self.assertEqual(value["data"]["notes"].encode(), (root / "export.md").read_bytes())
            (root / "CHANGELOG.md").write_text("## [1.2.0]\n")
            code, failed, _ = invoke([*args, "--json", "--strict"])
            self.assertEqual(1, code)
            self.assertEqual("invalid_notes", failed["errors"][0]["code"])
            self.assertIn("line", failed["errors"][0])

    def test_exposure_reports_typed_findings_not_parsed_console_text(self):
        report = Report(new=[Finding("space name.md", "absolute-local-link", "example")])
        with patch.object(cli.audit, "scan", return_value=report):
            code, value, _ = invoke(["exposure", "--json"])
        self.assertEqual(1, code)
        self.assertEqual("space name.md", value["data"]["exposure"]["new"][0]["path"])
        self.assertEqual("check_failed", value["errors"][0]["code"])

    def test_audit_engine_exit_codes_and_policy_errors(self):
        with (
            patch.object(publication.config_module, "load", return_value=config.Config(Path.cwd())),
            patch.object(publication.audit, "scan", return_value=Report()),
            patch.object(engines, "betterleaks", return_value=17),
            patch.object(engines, "lychee", return_value=0),
        ):
            code, value, _ = invoke(["audit", "--json"])
        self.assertEqual(1, code)
        self.assertEqual({"betterleaks": 17, "lychee": 0}, value["data"]["engines"])
        with patch.object(cli.config_module, "load", side_effect=config.ConfigError("bad policy")):
            code, value, _ = invoke(["audit", "--json"])
        self.assertEqual(2, code)
        self.assertEqual("configuration_error", value["errors"][0]["code"])

    def test_native_engine_streams_do_not_contaminate_json_stdout(self):
        program = """
import sys
from pathlib import Path
from unittest.mock import patch
from releasekit import cli, config, engines, publication
from releasekit.exposure.audit import Report
def engine(*args, **kwargs):
    return engines._run([sys.executable, '-c', 'import os; os.write(1, b"native-output")'], root=Path.cwd())
with patch.object(publication.config_module, 'load', return_value=config.Config(Path.cwd())), patch.object(publication.audit, 'scan', return_value=Report()), patch.object(engines, 'betterleaks', side_effect=engine), patch.object(engines, 'lychee', return_value=0):
    raise SystemExit(cli.main(['audit', '--json']))
"""
        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, timeout=30, check=False
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("ok", json.loads(result.stdout)["status"])
        self.assertIn(b"native-output", result.stderr)

    def test_unexpected_exception_is_not_a_success_or_raw_stdout_traceback(self):
        with patch.object(cli.protection, "problem", side_effect=RuntimeError("unexpected")):
            code, value, stderr = invoke(["protect", "check", "--json"])
        self.assertEqual(2, code)
        self.assertEqual("internal_error", value["errors"][0]["code"])
        self.assertIn("Traceback", stderr)

    def test_overlay_reports_checked_mounts_and_problems(self):
        policy = SimpleNamespace(root=Path.cwd(), manifest_path=Path("manifest.toml"))
        with (
            patch.object(cli.owner, "discover", return_value=policy),
            patch.object(cli.manifest_module, "read", return_value=[object(), object()]),
            patch.object(cli.verify_module, "check", return_value=(["wrong target"], ["outside"])),
        ):
            code, value, _ = invoke(["overlay", "--json"])
        self.assertEqual(1, code)
        self.assertEqual({"verified_mounts": 1, "skipped": ["outside"]}, value["data"])
        self.assertEqual("check_failed", value["errors"][0]["code"])


class JsonUpdateTests(UpdateFixture):
    def command(self, *extra):
        return invoke(["update", "--json", "--root", str(self.root), *extra])

    def source(self):
        return ["--artifact", str(self.candidate), "--sha256", sha(self.new)]

    def test_plan_contains_exact_files_and_trust_changes_without_backup_or_execution(self):
        previous = self.guard()
        code, value, _ = self.command("--dry-run", *self.source())
        self.assertEqual(0, code)
        plan = value["data"]["plan"]
        self.assertEqual("update", plan["action"])
        self.assertEqual(
            {".github/relkit.pyz", ".git/hooks/pre-push"}, {f["path"] for f in plan["files"]}
        )
        self.assertEqual(sha(previous), plan["files"][1]["before_sha256"])
        self.assertEqual(sha(self.old), plan["guard_inputs_before"][".github/relkit.pyz"])
        self.assertEqual(sha(self.new), plan["guard_inputs_after"][".github/relkit.pyz"])
        self.assertEqual(self.old, self.projection.read_bytes())
        self.assertFalse(list((self.root / ".git").glob("relkit-update-*")))
        code, installed, _ = self.command(
            "--yes", "--plan-hash", value["data"]["plan_sha256"], *self.source()
        )
        self.assertEqual(0, code)
        self.assertEqual("installed", installed["data"]["state"])
        self.assertTrue(Path(installed["data"]["backup"]).is_relative_to(self.root))

    def test_stale_plan_and_json_without_yes_do_not_mutate_or_prompt(self):
        before = set(self.root.rglob("*"))
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            code, value, _ = self.command(*self.source())
        self.assertEqual(2, code)
        self.assertEqual("confirmation_required", value["errors"][0]["code"])
        self.assertEqual(before, set(self.root.rglob("*")))
        code, value, _ = self.command("--yes", "--plan-hash", "0" * 64, *self.source())
        self.assertEqual(2, code)
        self.assertIn("stale", value["errors"][0]["message"])
        self.assertEqual(self.old, self.projection.read_bytes())
        self.assertFalse(list((self.root / ".git").glob("relkit-update-*")))

    def test_guard_refresh_plan_and_noop_and_rollback(self):
        self.guard()
        self.policy.write_text(self.policy.read_text() + "\n# reviewed setting\n")
        code, value, _ = self.command("--refresh-guard", "--dry-run")
        self.assertEqual(0, code)
        self.assertEqual("refresh-guard", value["data"]["plan"]["action"])
        self.assertEqual(
            [".git/hooks/pre-push"], [f["path"] for f in value["data"]["plan"]["files"]]
        )
        self.assertEqual(0, self.command("--refresh-guard", "--yes")[0])
        code, value, _ = self.command("--refresh-guard", "--dry-run")
        self.assertEqual(0, code)
        self.assertEqual("unchanged", value["data"]["state"])
        self.assertEqual([], value["data"]["plan"]["files"])
        code, value, _ = self.command("--rollback", "--yes")
        self.assertEqual(0, code)
        self.assertEqual("rolled-back", value["data"]["state"])

    def test_protect_check_json(self):
        code, value, _ = invoke(["protect", "install", "--root", str(self.root), "--json"])
        self.assertEqual(0, code)
        self.assertEqual("installed", value["data"]["guard"])
        self.assertEqual(str(self.hook), value["data"]["path"])
        code, value, _ = invoke(["protect", "check", "--root", str(self.root), "--json"])
        self.assertEqual(0, code)
        self.assertEqual("valid", value["data"]["guard"])

    def test_protect_preview_stale_plan_and_unowned_partial_file(self):
        args = ["protect", "install", "--root", str(self.root), "--json"]
        code, value, _ = invoke([*args, "--dry-run"])
        self.assertEqual(0, code)
        self.assertFalse(self.hook.exists())
        digest = value["data"]["plan"]["plan_sha256"]
        self.policy.write_text(POLICY + "\n# changed after plan\n")
        self.assertEqual(2, invoke([*args, "--plan-hash", digest])[0])
        self.assertFalse(self.hook.exists())
        partial = self.hook.with_name("pre-push.relkit-partial")
        partial.write_text("not owned by this invocation")
        code, value, _ = invoke([*args, "--dry-run"])
        self.assertEqual(0, code)
        self.assertEqual(0, invoke([*args, "--plan-hash", value["data"]["plan"]["plan_sha256"]])[0])
        self.assertEqual("not owned by this invocation", partial.read_text())

    def test_protect_refuses_hardlinked_or_external_hook_targets(self):
        other = self.root / "other-hook"
        other.write_text(cli.protection.hook_content(self.root))
        os.link(other, self.hook)
        code, value, _ = invoke(["protect", "install", "--root", str(self.root), "--json"])
        self.assertEqual(2, code)
        self.assertIn("hard-linked", value["errors"][0]["message"])
        self.hook.unlink()
        with patch.object(
            cli.protection, "hook_path", return_value=self.directory / "external-hook"
        ):
            code, value, _ = invoke(["protect", "install", "--root", str(self.root), "--json"])
        self.assertEqual(2, code)
        self.assertIn("inside", value["errors"][0]["message"])
        self.assertFalse((self.directory / "external-hook").exists())

    def test_rollback_preview_hash_binding_and_backup_tamper(self):
        self.guard()
        self.assertEqual(0, self.command("--yes", *self.source())[0])
        receipt = (self.root / ".git" / update.RECEIPT).read_bytes()
        before = self.projection.read_bytes()
        code, value, _ = self.command("--rollback", "--dry-run")
        self.assertEqual(0, code, value)
        digest = value["data"]["plan_sha256"]
        self.assertEqual("rollback", value["data"]["plan"]["action"])
        self.assertEqual(receipt, (self.root / ".git" / update.RECEIPT).read_bytes())
        self.assertEqual(before, self.projection.read_bytes())
        self.assertEqual(2, self.command("--rollback", "--yes", "--plan-hash", "0" * 64)[0])
        self.assertEqual(before, self.projection.read_bytes())
        self.assertEqual(0, self.command("--rollback", "--yes", "--plan-hash", digest)[0])
        self.assertEqual(self.old, self.projection.read_bytes())
        backup = self.root / ".git" / self.receipt()["backup"] / "relkit.pyz"
        backup.write_bytes(b"changed")
        self.assertEqual(2, self.command("--rollback", "--dry-run")[0])

    def test_failed_update_reports_rollback_not_a_pristine_refusal(self):
        self.new = artifact_bytes("0.6.0", audit_exit=1)
        self.candidate.write_bytes(self.new)
        code, value, _ = self.command("--yes", *self.source())
        self.assertEqual(2, code)
        self.assertEqual("rolled-back", value["data"]["state"])
        self.assertTrue(Path(value["data"]["backup"]).is_dir())
        self.assertEqual(self.old, self.projection.read_bytes())


class JsonReleaseTests(ReleaseFixture):
    def command(self, action, *extra):
        with (
            patch.object(coordinator, "Runner", return_value=self.runner),
            patch.object(coordinator, "GitHub", return_value=self.github),
        ):
            return invoke(["release", action, "v1.0.0", "--json", "--root", str(self.root), *extra])

    def test_plan_run_status_envelopes_and_status_never_calls_remote(self):
        code, value, _ = self.command("plan")
        self.assertEqual(0, code)
        self.assertEqual(self.sha, value["data"]["plan"]["sha"])
        digest = value["data"]["plan"]["plan_sha256"]
        code, value, _ = self.command("run", "--publish", "--plan-hash", digest)
        self.assertEqual(0, code, value)
        self.assertEqual("current-run", value["data"]["release"]["observation"])
        self.assertEqual("passed", value["data"]["release"]["verification"])
        path = coordinator._state_path(self.root, "v1.0.0")
        before = {
            p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.parent.iterdir()
            if p.is_file()
        }
        (self.root / "unrelated.txt").write_text("new local work")
        with patch.object(self.github, "api", side_effect=AssertionError("status must be offline")):
            code, value, _ = self.command("status")
        self.assertEqual(0, code, value)
        self.assertEqual("local-receipt", value["data"]["release"]["observation"])
        self.assertEqual(digest, value["data"]["release"]["plan"]["plan_sha256"])
        self.assertEqual(self.sha, value["data"]["release"]["plan"]["sha"])
        self.assertEqual(
            before,
            {
                p: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in path.parent.iterdir()
                if p.is_file()
            },
        )
        self.assertFalse((self.root / ".git/relkit/release.lock").exists())

    def test_pending_and_failed_runs_keep_publication_separate(self):
        self.github.draft = True
        code, value, _ = self.command("run", "--publish")
        self.assertEqual(3, code, value)
        self.assertEqual("pending", value["status"])
        self.assertEqual("draft", value["data"]["release"]["publication"])
        self.assertEqual(["relkit", "release", "resume", "v1.0.0"], value["next_action"][:4])
        self.github.draft = False
        self.github.signature_valid = False
        code, value, _ = self.command("resume", "--publish")
        self.assertEqual(1, code, value)
        self.assertEqual("published", value["data"]["release"]["publication"])
        self.assertEqual("failed", value["data"]["release"]["verification"])
        code, value, _ = self.command("status")
        self.assertEqual(0, code)
        self.assertEqual("failed", value["data"]["release"]["verification"])

    def test_status_missing_malformed_oversized_and_foreign_receipts_fail_closed(self):
        self.assertEqual(2, self.command("status")[0])
        self.assertFalse((self.root / ".git/relkit").exists())
        result = Result()
        self.assertEqual(0, self.invoke(result=result)[0])
        path = coordinator._state_path(self.root, "v1.0.0")
        original = path.read_bytes()
        for payload in (b"{", b"{}", b"[]", b" " * (2 * 1024 * 1024 + 1)):
            path.write_bytes(payload)
            self.assertEqual(2, self.command("status")[0])
            self.assertEqual(payload, path.read_bytes())
        path.write_bytes(original)
        saved = json.loads(original)
        saved["plan"]["root"] = str(self.root.parent)
        saved["plan_sha256"] = coordinator.fingerprint(saved["plan"])
        storage.atomic_json(path, saved)
        self.assertEqual(2, self.command("status")[0])

    def test_older_receipt_can_be_read_but_not_resumed_and_stays_unchanged(self):
        self.assertEqual(0, self.invoke()[0])
        path = coordinator._state_path(self.root, "v1.0.0")
        saved = self.receipt()
        saved["plan"]["tool_version"] = "0.7.1"
        saved["plan_sha256"] = coordinator.fingerprint(saved["plan"])
        storage.atomic_json(path, saved)
        before = path.read_bytes()
        code, value, _ = self.command("status")
        self.assertEqual(0, code)
        self.assertFalse(value["data"]["release"]["resume_version_matches"])
        self.assertEqual(2, self.command("resume", "--publish")[0])
        self.assertEqual(before, path.read_bytes())

    def test_abandon_envelope_records_the_outcome_and_refuses_publication_flags(self):
        self.github.ci_result = "failure"
        self.github.no_release = True
        self.assertEqual(1, self.command("run", "--publish")[0])
        subprocess.run(
            ["git", "tag", "-d", "v1.0.0"], cwd=self.server, check=True, capture_output=True
        )
        self.runner.git("tag", "-d", "v1.0.0")
        code, value, _ = self.command("abandon", "--reason", "cancelled", "--publish")
        self.assertEqual(2, code, value)
        self.assertEqual("release_error", value["errors"][0]["code"])
        code, value, _ = self.command("abandon", "--reason", "cancelled")
        self.assertEqual(0, code, value)
        self.assertEqual(["release", "abandon"], value["command"])
        self.assertEqual("abandoned", value["data"]["release"]["outcome"]["status"])
        self.assertEqual("cancelled", value["data"]["release"]["outcome"]["reason"])
        self.assertEqual("local-receipt", value["data"]["release"]["observation"])
        self.assertIsNone(value["next_action"])
        self.assertEqual(2, self.command("resume", "--publish")[0])
        self.github.ci_result = "success"
        self.github.no_release = False
        code, value, _ = self.command("run", "--publish")
        self.assertEqual(0, code, value)
        self.assertTrue(Path(value["data"]["archived_receipt"]).is_dir())
        self.assertIsNone(value["data"]["release"]["outcome"])

    def test_status_rejects_linked_receipt_and_publication_flags(self):
        self.assertEqual(2, self.command("status", "--publish")[0])
        self.assertEqual(0, self.invoke()[0])
        path = coordinator._state_path(self.root, "v1.0.0")
        other = path.with_name("linked-state.json")
        os.link(path, other)
        self.addCleanup(other.unlink)
        self.assertEqual(2, self.command("status")[0])
