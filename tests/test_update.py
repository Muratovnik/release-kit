from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from releasekit import __version__, distribution, protection, update

ROOT = Path(__file__).resolve().parents[1]
POLICY = "[exposure]\ncheck_secrets = false\ncheck_links = false\n"


def archive_bytes(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            entry = zipfile.ZipInfo()
            entry.filename = name
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, payload)
    return stream.getvalue()


def artifact_bytes(version: str, *, repository: str = "", audit_exit: int = 0) -> bytes:
    return archive_bytes(
        {
            "__main__.py": distribution.ENTRYPOINT,
            "releasekit/__init__.py": f'__version__ = "{version}"\n'.encode(),
            "releasekit/cli.py": (
                "import sys\n"
                "def main():\n"
                f"    print('release-kit {version} (test distribution)')\n"
                f"    return 0 if '--version' in sys.argv else {audit_exit}\n"
            ).encode(),
            distribution.BUILD_INFO: json.dumps(
                {"schema": 1, "version": version, "repository": repository}
            ).encode(),
        }
    )


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class DistributionTests(unittest.TestCase):
    def test_builder_requires_matching_metadata_and_dated_changelog(self) -> None:
        builder = runpy.run_path(str(ROOT / "tools/build_zipapp.py"))["build"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src/releasekit"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text('__version__ = "0.6.0"\n')
            (root / "pyproject.toml").write_text('[project]\nversion = "0.5.0"\n')
            (root / "CHANGELOG.md").write_text("## [Unreleased]\n")
            output = root / "relkit.pyz"
            with patch.dict(builder.__globals__, {"ROOT": root, "SOURCE": source}):
                with self.assertRaisesRegex(ValueError, "versions differ"):
                    builder(output)
                (root / "pyproject.toml").write_text('[project]\nversion = "0.6.0"\n')
                with self.assertRaisesRegex(ValueError, "dated changelog"):
                    builder(output)
                self.assertFalse(output.exists())

    def test_builder_accepts_the_linked_heading_the_coordinator_requires(self) -> None:
        # The coordinator requires the released heading to compare the actual previous
        # tag to this one. A builder that took only an unlinked heading meant no
        # project could satisfy both, including this one.
        builder = runpy.run_path(str(ROOT / "tools/build_zipapp.py"))["build"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src/releasekit"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text('__version__ = "0.6.0"\n')
            (source / "cli.py").write_text("def main():\n    return 0\n")
            (root / "pyproject.toml").write_text(
                '[project]\nversion = "0.6.0"\nauthors = []\nlicense = "MIT"\n'
            )
            (root / "LICENSE").write_text("license text\n")
            (root / "CHANGELOG.md").write_text(
                "## [0.6.0](https://example.invalid/o/r/compare/v0.5.0...v0.6.0) - 2026-01-01\n"
            )
            output = root / "relkit.pyz"

            with patch.dict(builder.__globals__, {"ROOT": root, "SOURCE": source}):
                builder(output)

            self.assertEqual("0.6.0", distribution.inspect(output.read_bytes()).version)

    def test_the_projection_does_not_describe_the_host_that_built_it(self) -> None:
        # The published 0.18.0 could not be reproduced on Windows: `version made by`
        # came from sys.platform while external_attr already carried Unix mode bits,
        # and the digest manifest picked up the platform newline.
        builder = runpy.run_path(str(ROOT / "tools/build_zipapp.py"))["build"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "src/releasekit"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text('__version__ = "0.6.0"\n')
            (source / "cli.py").write_text("def main():\n    return 0\n")
            (root / "pyproject.toml").write_text(
                '[project]\nversion = "0.6.0"\nauthors = []\nlicense = "MIT"\n'
            )
            (root / "LICENSE").write_text("license text\n")
            (root / "CHANGELOG.md").write_text("## [0.6.0] - 2026-01-01\n")
            output = root / "relkit.pyz"

            with patch.dict(builder.__globals__, {"ROOT": root, "SOURCE": source}):
                builder(output)

            with zipfile.ZipFile(output) as archive:
                hosts = {item.create_system for item in archive.infolist()}
                modes = {item.external_attr >> 16 for item in archive.infolist()}
            self.assertEqual({3}, hosts, "Unix mode bits need the Unix host they mean")
            self.assertEqual({0o100644}, modes)
            manifest = output.with_name(output.name + ".sha256").read_bytes()
            self.assertNotIn(b"\r", manifest)
            self.assertEqual(f"{sha(output.read_bytes())}  relkit.pyz\n".encode(), manifest)

    def test_release_inputs_must_be_the_exact_committed_bytes(self) -> None:
        # A CRLF worktree copy of an LF blob builds a different artifact while Git may
        # report nothing: whether `status` notices depends on its stat cache, and in
        # this repository four such files were reported clean. The release build
        # therefore compares raw bytes rather than trusting either status or the clean
        # filter, both of which call these two contents equal.
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            diverged = runpy.run_path(str(ROOT / "tools/build_release.py"))["diverged"]
        finally:
            sys.path.remove(str(ROOT / "tools"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "project"
            root.mkdir()
            for arguments in (
                ["init", "-q"],
                ["config", "user.name", "Example Maintainer"],
                ["config", "user.email", "maintainer@example.invalid"],
            ):
                subprocess.run(["git", *arguments], cwd=root, check=True)
            (root / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
            (root / "sample.py").write_bytes(b"print(1)\n")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "chore: sample"], cwd=root, check=True)

            self.assertEqual([], diverged(root))

            (root / "sample.py").write_bytes(b"print(1)\r\n")

            self.assertEqual(["sample.py"], diverged(root))
            self.assertEqual(
                "print(1)\n",
                subprocess.run(
                    ["git", "show", "HEAD:sample.py"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout,
                "the committed bytes are unchanged; only the worktree copy diverged",
            )
            self.assertEqual([], diverged(root.parent / "outside-any-repository"))

    def test_version_is_inspected_without_executing_source(self) -> None:
        payload = archive_bytes(
            {
                "__main__.py": distribution.ENTRYPOINT,
                "releasekit/__init__.py": b'__version__ = "0.5.0"\nraise RuntimeError("not imported")',
                "releasekit/cli.py": b"raise RuntimeError('not imported')",
            }
        )
        self.assertEqual(
            distribution.Artifact("0.5.0", sha(payload)), distribution.inspect(payload)
        )

    def test_only_stable_versions_and_repository_names_are_accepted(self) -> None:
        for version in ("v1.0.0", "01.0.0", "1.2", "1.2.3-rc.1", "1.2.3+build"):
            with self.subTest(version=version), self.assertRaises(distribution.DistributionError):
                distribution.version_tuple(version)
        for repository in ("../repo", "https://github.com/example/repo", "-o/repo", "o/r/extra"):
            with (
                self.subTest(repository=repository),
                self.assertRaises(distribution.DistributionError),
            ):
                distribution.repository_name(repository)

    def test_invalid_archive_paths_entrypoint_and_metadata_are_refused(self) -> None:
        with zipfile.ZipFile(io.BytesIO(artifact_bytes("0.6.0"))) as archive:
            original = {name: archive.read(name) for name in archive.namelist()}
        variants = [
            {**original, name: b""}
            for name in (
                "../escape.py",
                "/absolute.py",
                "releasekit/../escape.py",
                "releasekit\\bad.py",
                "other.py",
            )
        ]
        variants.extend(
            [
                {**original, "__main__.py": b"print('unexpected')"},
                {**original, distribution.BUILD_INFO: b'{"schema":1,"version":"9.0.0"}'},
                {
                    **original,
                    "releasekit/__init__.py": b'__version__ = "0.6.0"\n__version__ = "0.7.0"',
                },
            ]
        )
        for entries in variants:
            with (
                self.subTest(names=list(entries)),
                self.assertRaises(distribution.DistributionError),
            ):
                distribution.inspect(archive_bytes(entries))

    def test_duplicate_entries_and_archive_budgets_are_refused(self) -> None:
        stream = io.BytesIO(artifact_bytes("0.6.0"))
        with warnings.catch_warnings(), zipfile.ZipFile(stream, "a") as archive:
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr("releasekit/cli.py", b"duplicate")
        with self.assertRaisesRegex(distribution.DistributionError, "duplicate"):
            distribution.inspect(stream.getvalue())
        for constant in ("MAX_ARCHIVE_BYTES", "MAX_CONTENT_BYTES"):
            with (
                patch.object(distribution, constant, 1),
                self.assertRaises(distribution.DistributionError),
            ):
                distribution.inspect(artifact_bytes("0.6.0"))


class UpdateFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "consumer"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "core.hooksPath", str(self.root / ".git/hooks"))
        self.git("config", "user.name", "Example Maintainer")
        self.git("config", "user.email", "maintainer@example.invalid")
        # Git's background maintenance writes and removes its own lock after
        # ordinary commands, which races a fixture that asserts an exact tree. The
        # product must never turn this off in a user's repository, so the fixture
        # turns it off in its own.
        self.git("config", "maintenance.auto", "false")
        self.git("config", "gc.auto", "0")
        (self.root / ".github").mkdir()
        self.projection = self.root / update.PROJECTION
        self.old = artifact_bytes("0.5.0")
        self.projection.write_bytes(self.old)
        self.policy = self.root / "relkit.toml"
        self.policy.write_text(POLICY, encoding="utf-8")
        self.git("add", ".github/relkit.pyz", "relkit.toml")
        self.git("commit", "-qm", "chore: adopt publication gate")
        self.hook = self.root / ".git/hooks/pre-push"
        self.candidate = self.directory / "candidate.pyz"
        self.new = artifact_bytes("0.6.0")
        self.candidate.write_bytes(self.new)

    def git(self, *arguments: str) -> str:
        return update._run(["git", *arguments], self.root)

    def invoke(self, **options: object) -> tuple[int, str]:
        arguments = {"yes": True, **options}
        if not any(
            arguments.get(key)
            for key in ("rollback", "refresh_guard", "repository", "prune_backups")
        ):
            arguments = {
                "artifact_path": self.candidate,
                "sha256": sha(self.candidate.read_bytes()),
                **arguments,
            }
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = update.run(self.root, **arguments)
        return code, output.getvalue()

    def guard(self) -> bytes:
        protection.install(self.root)
        return self.hook.read_bytes()

    def receipt(self) -> dict:
        return json.loads((self.root / ".git" / update.RECEIPT).read_text())


class UpdateTests(UpdateFixture):
    def test_runtime_version_mismatch_is_refused_before_install(self) -> None:
        with zipfile.ZipFile(io.BytesIO(self.new)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries["releasekit/cli.py"] = entries["releasekit/cli.py"].replace(b"0.6.0", b"0.9.0")
        self.candidate.write_bytes(archive_bytes(entries))
        code, output = self.invoke()
        self.assertEqual(2, code)
        self.assertIn("runtime version", output)
        self.assertEqual(self.old, self.projection.read_bytes())

    def test_linked_worktree_and_old_guard_ownership_are_refused(self) -> None:
        linked = self.directory / "linked"
        self.git("worktree", "add", "--detach", str(linked))
        with self.assertRaisesRegex(update.UpdateError, "linked worktrees"):
            update._repository(linked)
        self.projection.write_bytes(artifact_bytes("0.4.1"))
        self.guard()
        code, output = self.invoke()
        self.assertEqual(2, code)
        self.assertIn("side-by-side migration", output)

    def test_candidate_execution_failure_and_invalid_source_flags_leave_files_unchanged(
        self,
    ) -> None:
        for options in (
            {
                "repository": "example/tool",
                "artifact_path": self.candidate,
                "sha256": sha(self.new),
            },
            {"artifact_path": None, "sha256": sha(self.new)},
        ):
            self.assertEqual(2, self.invoke(**options)[0])
        original_run = update._run

        def unavailable_runtime(command, root, **kwargs):
            if command[0] == sys.executable:
                raise update.UpdateError("runtime unavailable")
            return original_run(command, root, **kwargs)

        with patch.object(update, "_run", side_effect=unavailable_runtime):
            self.assertIn("runtime unavailable", self.invoke()[1])
        self.assertEqual(self.old, self.projection.read_bytes())

    def test_refresh_needs_a_guard_and_does_not_accept_source_selection(self) -> None:
        self.assertEqual(2, self.invoke(refresh_guard=True)[0])
        self.guard()
        self.assertEqual(2, self.invoke(refresh_guard=True, artifact_path=self.candidate)[0])
        self.assertIn("already matches", self.invoke(refresh_guard=True)[1])

    def test_update_refreshes_owned_guard_and_leaves_policy_index_refs_unchanged(self) -> None:
        old_hook = self.guard()
        index = (self.root / ".git/index").read_bytes()
        head = self.git("rev-parse", "HEAD")
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertEqual(self.new, self.projection.read_bytes())
        self.assertEqual(POLICY, self.policy.read_text())
        self.assertEqual(index, (self.root / ".git/index").read_bytes())
        self.assertEqual(head, self.git("rev-parse", "HEAD"))
        self.assertIsNone(protection.problem(self.root))
        receipt = self.receipt()
        backup = self.root / ".git" / receipt["backup"]
        self.assertEqual(self.old, (backup / "relkit.pyz").read_bytes())
        self.assertEqual(old_hook, (backup / "pre-push").read_bytes())
        self.assertEqual("installed", receipt["state"])
        self.assertIn(sha(self.old), output)
        self.assertIn(sha(self.new), output)

    def test_update_does_not_install_a_new_guard(self) -> None:
        self.assertEqual(0, self.invoke()[0])
        self.assertFalse(self.hook.exists())

    def test_local_digest_is_mandatory_and_must_match_before_execution(self) -> None:
        for digest in ("", "f" * 64, "malformed"):
            with self.subTest(digest=digest):
                code, output = self.invoke(sha256=digest)
                self.assertEqual(2, code, output)
                self.assertEqual(self.old, self.projection.read_bytes())
                self.assertFalse((self.root / ".git" / update.RECEIPT).exists())

    def test_downgrade_and_reused_version_are_refused(self) -> None:
        for version in ("0.4.0", "0.5.0"):
            self.candidate.write_bytes(artifact_bytes(version, repository="example/tool"))
            code, output = self.invoke()
            self.assertEqual(2, code)
            self.assertIn("same version", output)
            self.assertEqual(self.old, self.projection.read_bytes())

    def test_repeat_with_identical_bytes_is_noop_even_before_commit(self) -> None:
        self.assertEqual(0, self.invoke()[0])
        receipt = self.receipt()
        code, output = self.invoke()
        self.assertEqual(0, code, output)
        self.assertIn("already current", output)
        self.assertEqual(receipt, self.receipt())

    def test_dirty_checkout_is_not_stashed_or_overwritten(self) -> None:
        self.policy.write_text(POLICY + "# local change\n")
        code, output = self.invoke()
        self.assertEqual(2, code)
        self.assertIn("clean checkout", output)
        self.assertEqual(self.old, self.projection.read_bytes())
        self.assertIn("local change", self.policy.read_text())

    def test_dry_run_and_cancel_do_not_execute_candidate_or_write_backup(self) -> None:
        original_run = update._run
        for options in ({"dry_run": True}, {"yes": False}):

            def no_candidate(command, root, **kwargs):
                self.assertEqual("git", command[0], "candidate executed without confirmation")
                return original_run(command, root, **kwargs)

            with (
                patch.object(update, "_run", side_effect=no_candidate),
                patch.object(sys.stdin, "isatty", return_value=False),
            ):
                code, output = self.invoke(**options)
            self.assertEqual(0 if options.get("dry_run") else 2, code, output)
            self.assertEqual(self.old, self.projection.read_bytes())
            self.assertFalse(list((self.root / ".git").glob("relkit-update-*")))
        with (
            patch.object(sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="n"),
        ):
            self.assertIn("cancelled", self.invoke(yes=False)[1])

    def test_failed_audit_restores_artifact_and_guard(self) -> None:
        old_hook = self.guard()
        self.candidate.write_bytes(artifact_bytes("0.6.0", audit_exit=1))
        code, output = self.invoke()
        self.assertEqual(2, code)
        self.assertIn("previous artifact/guard restored", output)
        self.assertEqual(self.old, self.projection.read_bytes())
        self.assertEqual(old_hook, self.hook.read_bytes())
        self.assertEqual("rolled-back", self.receipt()["state"])

    def test_explicit_rollback_restores_both_files(self) -> None:
        old_hook = self.guard()
        self.assertEqual(0, self.invoke()[0])
        code, output = self.invoke(rollback=True)
        self.assertEqual(0, code, output)
        self.assertEqual(self.old, self.projection.read_bytes())
        self.assertEqual(old_hook, self.hook.read_bytes())
        self.assertIsNone(protection.problem(self.root))

    def test_rollback_accepts_the_guard_the_receipt_says_it_installed(self) -> None:
        # Field case: 0.14.0 added the release workflow to the guarded inputs, so the
        # guard recomputed at rollback time no longer matched the installed one and
        # `update --rollback` refused to undo the very release that changed the list.
        old_hook = self.guard()
        self.assertEqual(0, self.invoke()[0])
        receipt = self.receipt()
        self.assertEqual(
            sha(self.hook.read_bytes()),
            receipt["new_guard_sha256"],
            "the receipt pins what it wrote",
        )
        with patch.object(
            protection,
            "hook_content",
            side_effect=lambda root, **_: protection._hook_v1("{'extra': %r}" % ("0" * 64)),
        ):
            code, output = self.invoke(rollback=True)

        self.assertEqual(0, code, output)
        self.assertEqual(self.old, self.projection.read_bytes())
        self.assertEqual(old_hook, self.hook.read_bytes())

    def test_gh_scratch_is_adopted_so_no_temporary_directory_survives(self) -> None:
        # The workspace confines XDG_* for children, so gh writes its device id
        # beside the download; every update, a dry run included, left that behind.
        payload = artifact_bytes("0.6.0", repository="example/release-kit")
        metadata = {
            "tag_name": "v0.6.0",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-09-01T00:00:00Z",
            "assets": [
                {
                    "name": "relkit.pyz",
                    "state": "uploaded",
                    "size": len(payload),
                    "digest": "sha256:" + sha(payload),
                }
            ],
        }
        original_run = update._run

        def gh(command, root, **kwargs):
            if command[0] != "gh":
                return original_run(command, root, **kwargs)
            scratch = Path(kwargs["environment"]["TMPDIR"]) / update.GH_STATE
            scratch.mkdir(exist_ok=True)
            (scratch / "device-id").write_text("fixture\n", encoding="utf-8")
            if command[1] == "api":
                return json.dumps(metadata)
            Path(command[-1]).write_bytes(payload)
            return ""

        with patch.object(update, "_run", side_effect=gh):
            code, output = self.invoke(repository="example/release-kit")

        self.assertEqual(0, code, output)
        self.assertNotIn("retained unowned or changed", output)
        self.assertEqual([], list((self.root / ".git/relkit/tmp").glob("download-*")))

    def test_prune_removes_only_backups_no_receipt_can_restore(self) -> None:
        self.assertEqual(0, self.invoke()[0])
        current = self.root / ".git" / self.receipt()["backup"]
        superseded = Path(tempfile.mkdtemp(prefix=update.BACKUP_PREFIX, dir=self.root / ".git"))
        (superseded / "relkit.pyz").write_bytes(b"an earlier projection")
        foreign = Path(tempfile.mkdtemp(prefix=update.BACKUP_PREFIX, dir=self.root / ".git"))
        (foreign / "notes.txt").write_text("somebody else's file", encoding="utf-8")

        code, output = self.invoke(prune_backups=True)

        self.assertEqual(0, code, output)
        self.assertTrue(current.is_dir(), "the receipt names the one restorable backup")
        self.assertFalse(superseded.exists())
        self.assertTrue(foreign.is_dir(), "unknown contents are reported, never swept")
        self.assertIn("not a plain update backup, retained", output)

    def test_prune_previews_refuses_a_source_and_waits_for_a_live_backup(self) -> None:
        self.assertEqual(0, self.invoke()[0])
        superseded = Path(tempfile.mkdtemp(prefix=update.BACKUP_PREFIX, dir=self.root / ".git"))
        (superseded / "relkit.pyz").write_bytes(b"an earlier projection")

        self.assertIn("would remove", self.invoke(prune_backups=True, dry_run=True)[1])
        self.assertTrue(superseded.is_dir())
        self.assertEqual(2, self.invoke(prune_backups=True, rollback=True)[0])

        receipt = self.receipt()
        receipt["state"] = "pending"
        update._save_receipt(self.root / ".git" / update.RECEIPT, receipt)
        code, output = self.invoke(prune_backups=True)
        self.assertEqual(2, code)
        self.assertIn("still needs its backup", output)
        self.assertTrue(superseded.is_dir())

    def test_a_completed_update_reports_backups_no_receipt_can_restore(self) -> None:
        stale = Path(tempfile.mkdtemp(prefix=update.BACKUP_PREFIX, dir=self.root / ".git"))
        (stale / "relkit.pyz").write_bytes(b"an earlier projection")

        self.assertIn("superseded backup(s) no receipt can restore", self.invoke()[1])

    def test_rollback_refuses_later_edits(self) -> None:
        self.guard()
        self.assertEqual(0, self.invoke()[0])
        for target, extra in (
            (self.policy, b"# changed"),
            (self.projection, b"changed"),
            (self.hook, b"# changed"),
        ):
            original = target.read_bytes()
            target.write_bytes(original + extra)
            code, output = self.invoke(rollback=True)
            self.assertEqual(2, code, output)
            self.assertEqual(original + extra, target.read_bytes())
            target.write_bytes(original)

    def test_pending_receipt_requires_explicit_recovery_and_bad_backup_is_refused(self) -> None:
        self.assertEqual(0, self.invoke()[0])
        receipt = self.receipt()
        receipt["state"] = "pending"
        update._save_receipt(self.root / ".git" / update.RECEIPT, receipt)
        self.assertIn("interrupted", self.invoke()[1])
        backup = self.root / ".git" / receipt["backup"] / "relkit.pyz"
        backup.write_bytes(artifact_bytes("0.1.0"))
        self.assertIn("backup has changed", self.invoke(rollback=True)[1])
        backup.write_bytes(self.old)
        self.assertEqual(0, self.invoke(rollback=True)[0])

    def test_existing_lock_and_malformed_receipt_fail_closed(self) -> None:
        lock = self.root / ".git/relkit-update.lock"
        lock.write_text("123")
        self.assertIn("lock exists", self.invoke()[1])
        self.assertEqual("123", lock.read_text())
        lock.unlink()
        (self.root / ".git" / update.RECEIPT).write_text("[]")
        self.assertIn("invalid update receipt", self.invoke()[1])

    def test_unmanaged_and_modified_managed_guards_are_not_replaced(self) -> None:
        for content in (b"#!/bin/sh\necho custom\n", self.guard() + b"# owner addition\n"):
            self.hook.write_bytes(content)
            for options in ({}, {"refresh_guard": True}):
                code, output = self.invoke(**options)
                self.assertEqual(2, code, output)
                self.assertEqual(content, self.hook.read_bytes())

    def test_external_dispatcher_is_verified_but_not_written(self) -> None:
        self.guard()
        external = self.directory / "external-hooks"
        external.mkdir()
        dispatcher = external / "pre-push"
        content = f"#!/bin/sh\n{protection.COMPATIBLE_DISPATCHER_MARKER}\n".encode()
        dispatcher.write_bytes(content)
        dispatcher.chmod(0o755)
        self.git("config", "core.hooksPath", str(external))
        self.assertEqual(0, self.invoke()[0])
        self.assertEqual(content, dispatcher.read_bytes())

    def test_incompatible_external_dispatcher_prevents_update(self) -> None:
        old_hook = self.guard()
        external = self.directory / "custom-hooks"
        external.mkdir()
        dispatcher = external / "pre-push"
        content = b"#!/bin/sh\necho custom\n"
        dispatcher.write_bytes(content)
        self.git("config", "core.hooksPath", str(external))
        code, output = self.invoke()
        self.assertEqual(2, code)
        self.assertIn("not a compatible", output)
        self.assertEqual(content, dispatcher.read_bytes())
        self.assertEqual(old_hook, self.hook.read_bytes())
        self.assertEqual(self.old, self.projection.read_bytes())

    def test_external_git_environment_and_projection_symlink_are_refused(self) -> None:
        with patch.dict(os.environ, {"GIT_DIR": str(self.root / ".git")}):
            self.assertIn("environment overrides", self.invoke()[1])
        self.projection.unlink()
        try:
            self.projection.symlink_to(self.candidate)
        except OSError:
            self.skipTest("symlink permission unavailable")
        self.assertIn("aliased", self.invoke()[1])
        self.assertEqual(self.new, self.candidate.read_bytes())

    def test_refresh_guard_handles_preupdated_binary_and_config_with_concrete_diagnostics(
        self,
    ) -> None:
        old_hook = self.guard()
        self.projection.write_bytes(self.new)
        self.policy.write_text(POLICY + "# reviewed policy change\n")
        before = {path: path.read_bytes() for path in (self.projection, self.policy)}
        diagnostic = protection.problem(self.root)
        self.assertIn(update.PROJECTION, diagnostic)
        self.assertIn("relkit.toml", diagnostic)
        self.assertIn(sha(self.old), diagnostic)
        self.assertIn(sha(self.new), diagnostic)
        self.assertIn("--refresh-guard", diagnostic)
        code, output = self.invoke(refresh_guard=True, dry_run=True)
        self.assertEqual(0, code, output)
        self.assertIn("only the owned guard", output)
        self.assertEqual(old_hook, self.hook.read_bytes())
        code, output = self.invoke(refresh_guard=True)
        self.assertEqual(0, code, output)
        self.assertIsNone(protection.problem(self.root))
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(0, self.invoke(rollback=True)[0])
        self.assertEqual(old_hook, self.hook.read_bytes())
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_refresh_guard_adopts_a_template_it_used_to_only_report(self) -> None:
        # Field case on 0.16.0: the pins were current and the guard was an older
        # template, so the run printed that exact template transition, exited 0 and
        # wrote nothing. Only `protect install` could migrate it.
        current = self.guard()
        pins = protection.recorded_digests(self.root)
        self.hook.write_bytes(protection._hook_v1(repr(pins)).encode())
        older = self.hook.read_bytes()
        self.assertNotEqual(current, older)
        self.assertIsNone(protection.problem(self.root), "an older template is not drift")

        code, output = self.invoke(refresh_guard=True, dry_run=True)

        self.assertEqual(0, code, output)
        self.assertIn("older template", output)
        self.assertNotIn("already matches", output)
        self.assertEqual(older, self.hook.read_bytes())

        code, output = self.invoke(refresh_guard=True)

        self.assertEqual(0, code, output)
        self.assertEqual(current, self.hook.read_bytes())
        self.assertFalse(protection.outdated_template(self.root))
        backup = self.root / ".git" / self.receipt()["backup"]
        self.assertEqual(older, (backup / "pre-push").read_bytes())
        self.assertEqual(0, self.invoke(rollback=True)[0])
        self.assertEqual(older, self.hook.read_bytes())

    def test_a_noop_update_names_the_adopting_command_instead_of_a_hook_change(self) -> None:
        self.guard()
        pins = protection.recorded_digests(self.root)
        self.hook.write_bytes(protection._hook_v1(repr(pins)).encode())
        older = self.hook.read_bytes()
        unchanged = self.directory / "same.pyz"
        unchanged.write_bytes(self.old)
        result = update.Result()

        code, output = self.invoke(artifact_path=unchanged, sha256=sha(self.old), result=result)

        self.assertEqual(0, code, output)
        self.assertIn("already current", output)
        self.assertIn("--refresh-guard", output)
        self.assertNotIn("guard sha256:", output)
        self.assertEqual([], result.data["plan"]["files"])
        self.assertEqual(older, self.hook.read_bytes())

    def test_normal_update_does_not_silently_approve_existing_guard_drift(self) -> None:
        self.guard()
        self.policy.write_text(POLICY + "# new policy\n")
        code, output = self.invoke()
        self.assertEqual(2, code)
        self.assertIn("existing guard must be valid", output)

    def test_guard_only_audit_failure_leaves_hook_and_projection_untouched(self) -> None:
        old_hook = self.guard()
        current = artifact_bytes("0.6.0")
        self.projection.write_bytes(current)
        self.policy.write_text(POLICY + 'private_paths = ["private.txt"]\n')
        (self.root / "private.txt").write_text("private fixture")
        self.git("add", "private.txt")
        self.assertEqual(2, self.invoke(refresh_guard=True)[0])
        self.assertEqual(old_hook, self.hook.read_bytes())
        self.assertEqual(current, self.projection.read_bytes())

    def test_concurrent_policy_change_is_not_overwritten_or_repinned(self) -> None:
        old_hook = self.guard()
        original_run = update._run

        def change_during_audit(command, root, **kwargs):
            if "audit" in command:
                self.policy.write_text(POLICY + "# concurrent change\n")
            return original_run(command, root, **kwargs)

        with patch.object(update, "_run", side_effect=change_during_audit):
            code, output = self.invoke()
        self.assertEqual(2, code)
        self.assertIn("automatic rollback could not complete", output)
        self.assertIn("recovery backup", output)
        self.assertEqual(old_hook, self.hook.read_bytes())
        self.assertIn("concurrent change", self.policy.read_text())
        self.assertEqual("pending", self.receipt()["state"])

    def test_concurrent_projection_change_is_never_trusted_by_refreshed_guard(self) -> None:
        old_hook = self.guard()
        original_run = update._run
        changed = artifact_bytes("9.0.0")

        def change_during_audit(command, root, **kwargs):
            if "audit" in command:
                self.projection.write_bytes(changed)
            return original_run(command, root, **kwargs)

        with patch.object(update, "_run", side_effect=change_during_audit):
            code, output = self.invoke()
        self.assertEqual(2, code)
        self.assertIn("projection changed during validation", output)
        self.assertEqual(old_hook, self.hook.read_bytes())
        self.assertEqual(changed, self.projection.read_bytes())


class GitHubSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = artifact_bytes("0.6.0", repository="example/release-kit")
        self.metadata = {
            "tag_name": "v0.6.0",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-09-01T00:00:00Z",
            "assets": [
                {
                    "name": "relkit.pyz",
                    "state": "uploaded",
                    "size": len(self.payload),
                    "digest": "sha256:" + sha(self.payload),
                }
            ],
        }

    def download(self, *, release: str = "") -> tuple[bytes, distribution.Artifact]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def gh(command, cwd, **kwargs):
                self.assertEqual("gh", command[0])
                if command[1] == "api":
                    self.assertIn("github.com", command)
                    self.assertIn(
                        "repos/example/release-kit/releases/"
                        + ("tags/v0.6.0" if release else "latest"),
                        command,
                    )
                    return json.dumps(self.metadata)
                self.assertIn("github.com/example/release-kit", command)
                self.assertEqual("v0.6.0", command[3])
                Path(command[-1]).write_bytes(self.payload)
                return ""

            with patch.object(update, "_run", side_effect=gh):
                return update._github(root, "example/release-kit", release, root)

    def test_private_release_uses_authenticated_gh_and_verifies_identity(self) -> None:
        for release in ("", "v0.6.0"):
            payload, identity = self.download(release=release)
            self.assertEqual(self.payload, payload)
            self.assertEqual("example/release-kit", identity.repository)

    def test_unpublished_and_unverified_assets_are_refused(self) -> None:
        original = json.loads(json.dumps(self.metadata))
        variants = [
            {**original, "draft": True},
            {**original, "prerelease": True},
            {**original, "published_at": None},
            {**original, "assets": []},
            {**original, "assets": original["assets"] * 2},
        ]
        for changes in (
            {"digest": None},
            {"digest": "sha256:" + "f" * 64},
            {"size": 1},
            {"state": "new"},
        ):
            variants.append({**original, "assets": [{**original["assets"][0], **changes}]})
        for metadata in variants:
            self.metadata = metadata
            with self.subTest(metadata=metadata), self.assertRaises(update.UpdateError):
                self.download()

    def test_release_tag_and_embedded_repository_must_match(self) -> None:
        for version, repository in (("0.7.0", "example/release-kit"), ("0.6.0", "other/tool")):
            self.payload = artifact_bytes(version, repository=repository)
            self.metadata["assets"][0].update(
                size=len(self.payload), digest="sha256:" + sha(self.payload)
            )
            with self.assertRaisesRegex(update.UpdateError, "version/repository"):
                self.download()


class ZipappIntegrationTests(UpdateFixture):
    def test_real_zipapp_bootstrap_self_update_and_rollback(self) -> None:
        built = self.directory / "relkit.pyz"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/build_zipapp.py"),
                str(built),
                "--repository",
                "example/release-kit",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = built.read_bytes()
        identity = distribution.inspect(payload)
        self.assertEqual(__version__, identity.version)
        self.assertEqual("example/release-kit", identity.repository)
        self.assertEqual(
            f"{sha(payload)}  relkit.pyz\n", built.with_suffix(".pyz.sha256").read_text()
        )
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/build_zipapp.py"),
                str(built),
                "--repository",
                "example/release-kit",
            ],
            cwd=ROOT,
            check=True,
        )
        self.assertEqual(payload, built.read_bytes(), "build must be deterministic")
        self.guard()
        result = update._run(
            [
                sys.executable,
                str(built),
                "update",
                "--root",
                str(self.root),
                "--artifact",
                str(built),
                "--sha256",
                sha(payload),
                "--yes",
                "--no-download",
            ],
            self.root,
        )
        self.assertIn(f"installed {__version__}", result)
        self.assertIsNone(protection.problem(self.root))
        self.git("add", update.PROJECTION)
        self.git("commit", "-qm", "chore: update publication gate")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        major, minor, patch_version = distribution.version_tuple(__version__)
        next_version = f"{major}.{minor}.{patch_version + 1}"
        entries["releasekit/__init__.py"] = entries["releasekit/__init__.py"].replace(
            __version__.encode(), next_version.encode()
        )
        metadata = json.loads(entries[distribution.BUILD_INFO])
        metadata["version"] = next_version
        entries[distribution.BUILD_INFO] = json.dumps(metadata).encode()
        next_payload = archive_bytes(entries)
        self.candidate.write_bytes(next_payload)
        result = update._run(
            [
                sys.executable,
                str(self.projection),
                "update",
                "--artifact",
                str(self.candidate),
                "--sha256",
                sha(next_payload),
                "--yes",
                "--no-download",
            ],
            self.root,
        )
        self.assertIn(f"installed {next_version}", result)
        self.assertIsNone(protection.problem(self.root))
        result = update._run(
            [sys.executable, str(self.projection), "update", "--rollback", "--yes"], self.root
        )
        self.assertIn(f"restored {__version__}", result)
        self.assertEqual(payload, self.projection.read_bytes())
        self.assertIsNone(protection.problem(self.root))
