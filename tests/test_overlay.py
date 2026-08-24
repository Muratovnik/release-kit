"""The half nobody had: the links are made once, then quietly stop being true."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from releasekit.overlay import manifest, verify

MANIFEST = """- defaults:
    link:
      relink: true
      relative: true

# The surfaces a client insists on finding inside the public root.
- link:
    ../public/.someclient: local/.someclient
    ../public/NOTES.local.md: local/NOTES.local.md
"""


class ParseTests(unittest.TestCase):
    def _manifest(self, text: str) -> Path:
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        path = Path(handle.name) / "install.conf.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_it_reads_the_link_block_and_nothing_else(self) -> None:
        mounts = manifest.read(self._manifest(MANIFEST))

        self.assertEqual(
            [
                ("../public/.someclient", "local/.someclient"),
                ("../public/NOTES.local.md", "local/NOTES.local.md"),
            ],
            [(mount.link, mount.target) for mount in mounts],
        )

    def test_defaults_options_are_not_mistaken_for_mounts(self) -> None:
        self.assertEqual(2, len(manifest.read(self._manifest(MANIFEST))))

    def test_a_manifest_without_links_is_refused(self) -> None:
        with self.assertRaises(manifest.ManifestError):
            manifest.read(self._manifest("- defaults:\n    link:\n      relink: true\n"))

    def test_an_unsupported_form_is_refused_rather_than_skipped(self) -> None:
        """Skipping it would leave that mount unverified while reporting success."""
        text = "- link:\n    ../public/.x:\n      path: local/.x\n      create: true\n"
        with self.assertRaises(manifest.ManifestError) as caught:
            manifest.read(self._manifest(text))

        self.assertIn("never verified", str(caught.exception))

    def test_a_missing_manifest_is_refused(self) -> None:
        with self.assertRaises(manifest.ManifestError):
            manifest.read(Path("nowhere") / "install.conf.yaml")


class Overlay:
    """A public repository, a private repository, and a real link between them."""

    def __init__(self, ignore: str = "/.someclient\n/NOTES.local.md\n") -> None:
        self.handle = tempfile.TemporaryDirectory()
        base = Path(self.handle.name)
        self.public = base / "public"
        self.private = base / "private"
        (self.public / "docs").mkdir(parents=True)
        (self.private / "local" / ".someclient").mkdir(parents=True)
        (self.public / "README.md").write_text("x\n", encoding="utf-8")
        (self.public / ".gitignore").write_text(ignore, encoding="utf-8")
        (self.private / "local" / ".someclient" / "settings.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (self.private / "install.conf.yaml").write_text(
            "- link:\n    ../public/.someclient: local/.someclient\n", encoding="utf-8"
        )
        for root in (self.public, self.private):
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)

    def link(self) -> None:
        target = self.private / "local" / ".someclient"
        (self.public / ".someclient").symlink_to(target, target_is_directory=True)

    def mounts(self) -> tuple[manifest.Mount, ...]:
        return manifest.read(self.private / "install.conf.yaml")

    def check(self):
        return verify.check(self.mounts(), public_root=self.public, private_root=self.private)


def _overlay(**kwargs) -> Overlay:
    try:
        overlay = Overlay(**kwargs)
        overlay.link()
    except OSError as error:  # pragma: no cover - depends on the host's privileges
        raise unittest.SkipTest(f"this host cannot create symlinks: {error}") from error
    return overlay


class VerifyTests(unittest.TestCase):
    def test_a_correct_overlay_passes(self) -> None:
        overlay = _overlay()
        self.addCleanup(overlay.handle.cleanup)

        problems, skipped = overlay.check()

        self.assertEqual([], [str(problem) for problem in problems])
        self.assertEqual([], skipped)

    def test_a_missing_link_is_reported(self) -> None:
        """`git clean` in the public checkout removes them and nothing else says so."""
        overlay = _overlay()
        self.addCleanup(overlay.handle.cleanup)
        (overlay.public / ".someclient").unlink()

        problems, _ = overlay.check()

        self.assertEqual([verify.MISSING], [problem.kind for problem in problems])

    def test_a_copy_where_the_link_was_is_reported(self) -> None:
        """Two copies of one file is the failure the arrangement exists to remove."""
        overlay = _overlay()
        self.addCleanup(overlay.handle.cleanup)
        (overlay.public / ".someclient").unlink()
        (overlay.public / ".someclient").mkdir()

        problems, _ = overlay.check()

        self.assertIn(verify.NOT_A_LINK, [problem.kind for problem in problems])

    def test_a_mount_the_public_repository_would_commit_is_reported(self) -> None:
        overlay = _overlay(ignore="# nothing ignored\n")
        self.addCleanup(overlay.handle.cleanup)

        problems, _ = overlay.check()

        self.assertIn(verify.NOT_IGNORED, [problem.kind for problem in problems])

    def test_a_publicly_tracked_mount_is_reported_even_when_ignored_now(self) -> None:
        overlay = _overlay()
        self.addCleanup(overlay.handle.cleanup)
        subprocess.run(
            ["git", "add", "-f", ".someclient"],
            cwd=overlay.public,
            check=True,
            capture_output=True,
        )

        problems, _ = overlay.check()

        self.assertIn(verify.TRACKED_PUBLICLY, [problem.kind for problem in problems])

    def test_a_target_the_private_repository_never_committed_is_reported(self) -> None:
        """It works here and is simply absent on the next workstation."""
        overlay = _overlay()
        self.addCleanup(overlay.handle.cleanup)
        subprocess.run(
            ["git", "rm", "-r", "--cached", "-q", "local/.someclient"],
            cwd=overlay.private,
            check=True,
            capture_output=True,
        )

        problems, _ = overlay.check()

        self.assertIn(verify.TARGET_NOT_TRACKED, [problem.kind for problem in problems])

    def test_a_link_to_the_wrong_tracked_private_target_is_reported(self) -> None:
        overlay = _overlay()
        self.addCleanup(overlay.handle.cleanup)
        wrong = overlay.private / "local" / "wrong"
        wrong.mkdir()
        (overlay.public / ".someclient").unlink()
        (overlay.public / ".someclient").symlink_to(wrong, target_is_directory=True)

        problems, _ = overlay.check()

        self.assertIn(verify.WRONG_TARGET, [problem.kind for problem in problems])

    def test_a_mount_landing_elsewhere_is_named_rather_than_silently_passed(self) -> None:
        overlay = _overlay()
        self.addCleanup(overlay.handle.cleanup)
        elsewhere = (manifest.Mount(link="../somewhere-else/.x", target="local/.someclient"),)

        problems, skipped = verify.check(
            elsewhere, public_root=overlay.public, private_root=overlay.private
        )

        self.assertEqual([], problems)
        self.assertEqual(["../somewhere-else/.x"], skipped)


if __name__ == "__main__":
    unittest.main()
