from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="owned storage ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "project"
        self.root.mkdir()
        (self.root / ".git").mkdir()

    def test_temporary_files_stay_in_ignored_project_metadata(self):
        with storage.temporary(self.root, "test-") as workspace:
            self.assertTrue(workspace.path.is_relative_to(self.root / ".git"))
            (workspace.path / "known.txt").write_text("owned")
            workspace.remember()
        self.assertFalse(workspace.path.exists())

    def test_unknown_or_changed_files_are_never_swept(self):
        workspace = storage.Workspace(self.root)
        known = workspace.path / "known.txt"
        known.write_text("original")
        workspace.remember()
        known.write_text("new user data")
        foreign = workspace.path / "foreign.txt"
        foreign.write_text("unowned")
        self.assertFalse(workspace.cleanup())
        self.assertEqual("new user data", known.read_text())
        self.assertEqual("unowned", foreign.read_text())

    def test_specific_file_inventory_does_not_adopt_other_files(self):
        workspace = storage.Workspace(self.root)
        first = workspace.path / "first"
        first.write_text("first")
        second = workspace.path / "second"
        second.write_text("second")
        workspace.remember(first)
        self.assertFalse(workspace.cleanup())
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())

    def test_same_bytes_do_not_authorize_deleting_a_replacement_file(self):
        workspace = storage.Workspace(self.root)
        path = workspace.path / "owned"
        path.write_text("same bytes")
        workspace.remember(path)
        replacement = workspace.path / "replacement"
        replacement.write_text("same bytes")
        replacement.replace(path)
        self.assertFalse(workspace.cleanup())
        self.assertEqual("same bytes", path.read_text())

    def test_a_briefly_refused_receipt_rename_is_retried(self):
        # Observed on this repository's own release: the rename raised WinError 5
        # after publication had already happened, so the receipt was never written.
        original, refusals = Path.replace, []

        def refuse_twice(self, target):
            if len(refusals) < 2:
                refusals.append(target)
                raise PermissionError(5, "access is denied")
            return original(self, target)

        receipt = self.root / ".git" / "relkit" / "state.json"
        with patch.object(Path, "replace", refuse_twice), patch.object(time, "sleep"):
            storage.atomic_json(receipt, {"published": True})
        self.assertEqual(2, len(refusals))
        self.assertEqual('{\n  "published": true\n}\n', receipt.read_text(encoding="utf-8"))

    def test_a_persistently_refused_receipt_rename_still_fails(self):
        def refuse(self, target):
            raise PermissionError(5, "access is denied")

        receipt = self.root / ".git" / "relkit" / "state.json"
        with (
            patch.object(Path, "replace", refuse),
            patch.object(time, "sleep"),
            self.assertRaises(PermissionError),
        ):
            storage.atomic_json(receipt, {"published": True})
        self.assertFalse(receipt.exists())

    def test_external_cache_write_requires_exact_approved_path(self):
        cache = self.root.parent / "external cache"
        target = cache / "tool" / "file"
        with patch.dict(
            os.environ, {"RELKIT_CACHE_DIR": str(cache), "RELKIT_APPROVED_EXTERNAL_CACHE": ""}
        ):
            with self.assertRaises(storage.StorageError):
                storage.cache_write_path(self.root, target)
            with (
                patch.dict(os.environ, {"RELKIT_APPROVED_EXTERNAL_CACHE": str(cache.parent)}),
                self.assertRaises(storage.StorageError),
            ):
                storage.cache_write_path(self.root, target)
            with patch.dict(os.environ, {"RELKIT_APPROVED_EXTERNAL_CACHE": str(cache)}):
                self.assertEqual(target, storage.cache_write_path(self.root, target))
        self.assertFalse(cache.exists())

    def test_local_cache_must_be_ignored_before_any_provisioning_write(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        cache = self.root / ".cache/release-kit"
        target = cache / "tool"
        with patch.dict(os.environ, {"RELKIT_CACHE_DIR": str(cache)}):
            with self.assertRaises(storage.StorageError):
                storage.cache_write_path(self.root, target)
            (self.root / ".gitignore").write_text(".cache/\n")
            self.assertEqual(target, storage.cache_write_path(self.root, target))
        self.assertFalse(cache.exists())

    def test_link_or_junction_cannot_redirect_service_storage(self):
        other = self.root.parent / "other"
        other.mkdir()
        marker = other / "user.txt"
        marker.write_text("preserved")
        link = self.root / ".git/relkit"
        try:
            link.symlink_to(other, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("symlinks unavailable")
            # A junction needs no symlink privilege and exercises the Windows case.
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(other)],
                capture_output=True,
                check=False,
            )
            if result.returncode:
                self.skipTest("junctions unavailable")
        try:
            with self.assertRaises(storage.StorageError):
                storage.Workspace(self.root)
            self.assertEqual("preserved", marker.read_text())
        finally:
            if link.is_symlink():
                link.unlink()
            else:
                link.rmdir()

    def test_hardlinked_receipt_cannot_overwrite_other_files(self):
        original = self.root / "original"
        original.write_text("user data")
        alias = self.root / ".git/state.json"
        os.link(original, alias)
        with self.assertRaises(storage.StorageError):
            storage.atomic_json(alias, {"new": True})
        self.assertEqual("user data", original.read_text())
