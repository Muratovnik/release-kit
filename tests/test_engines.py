from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import engines


class EngineCommandTests(unittest.TestCase):
    def test_staged_secret_policy_and_its_relative_files_come_from_the_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            policy = root / "security.toml"
            policy.write_text('[extend]\npath = "rules.toml"\n')
            (root / "rules.toml").write_text("# reviewed indexed rules\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            policy.write_text("# unrelated worktree policy\n")
            (root / "rules.toml").write_text("# unrelated worktree rules\n")

            def inspect(command, *, root: Path, **kwargs):
                selected = Path(command[command.index("--config") + 1])
                self.assertEqual('[extend]\npath = "rules.toml"\n', selected.read_text())
                self.assertEqual("# reviewed indexed rules\n", (root / "rules.toml").read_text())
                return 0

            with (
                patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
                patch.object(engines, "_run", side_effect=inspect),
            ):
                self.assertEqual(
                    0,
                    engines.betterleaks(
                        root,
                        config="security.toml",
                        history=False,
                        staged=True,
                        include_candidates=False,
                        allow_download=False,
                    ),
                )
            self.assertEqual("# unrelated worktree policy\n", policy.read_text())

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="engine command test ")
        self.addCleanup(temporary.cleanup)
        original = engines.storage.service_root
        storage_patch = patch.object(
            engines.storage,
            "service_root",
            side_effect=lambda root: (
                Path(temporary.name) / "local-storage" if root == Path("repo") else original(root)
            ),
        )
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

    def test_betterleaks_history_uses_the_repository_and_project_config(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "_run", return_value=0) as invoke,
        ):
            result = engines.betterleaks(
                Path("repo"),
                config=".betterleaks.toml",
                history=True,
                staged=False,
                include_candidates=False,
                allow_download=False,
            )

        self.assertEqual(0, result)
        command = list(invoke.call_args.args[0])
        self.assertIn("--redact", command)
        self.assertIn("--verbose", command)
        self.assertIn("git", command)
        self.assertIn(f"--log-opts={engines.HISTORY_LOG_OPTS}", command)
        self.assertNotIn("--pre-commit", command)
        environment = invoke.call_args.kwargs["environment"]
        self.assertIn("safe.directory", environment.values())
        self.assertEqual("1", environment["GIT_NO_REPLACE_OBJECTS"])

    def test_betterleaks_staged_scope_is_explicit(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "_checkout_index"),
            patch.object(engines, "_run", return_value=0) as invoke,
        ):
            engines.betterleaks(
                Path("repo"),
                config=".betterleaks.toml",
                history=False,
                staged=True,
                include_candidates=False,
                allow_download=False,
            )

        command = list(invoke.call_args.args[0])
        self.assertIn("--pre-commit", command)
        self.assertIn("--staged", command)
        self.assertNotIn(f"--log-opts={engines.HISTORY_LOG_OPTS}", command)

    def test_betterleaks_worktree_scope_uses_the_directory_engine(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "_materialize_worktree"),
            patch.object(engines, "_run", return_value=0) as invoke,
        ):
            engines.betterleaks(
                Path("repo"),
                config=".betterleaks.toml",
                history=False,
                staged=False,
                include_candidates=True,
                allow_download=False,
            )

        command = list(invoke.call_args.args[0])
        self.assertIn("dir", command)
        self.assertNotIn("git", command)
        self.assertNotIn(str(Path("repo")), command[command.index("dir") + 1 :])

    def test_betterleaks_worktree_snapshot_matches_the_publication_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_text("ignored.txt\n.cache/\n", encoding="utf-8")
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=root, check=True)
            (root / "candidate.txt").write_text("candidate\n", encoding="utf-8")
            (root / "ignored.txt").write_text("private\n", encoding="utf-8")
            cache = root / ".cache"
            cache.mkdir()
            (cache / "engine.bin").write_bytes(b"private")
            observed: set[str] = set()

            def inspect(command, **_kwargs):
                snapshot = Path(command[command.index("dir") + 1])
                observed.update(
                    path.relative_to(snapshot).as_posix()
                    for path in snapshot.rglob("*")
                    if path.is_file()
                )
                return 0

            with (
                patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
                patch.object(engines, "_run", side_effect=inspect),
            ):
                result = engines.betterleaks(
                    root,
                    config=".betterleaks.toml",
                    history=False,
                    staged=False,
                    include_candidates=True,
                    allow_download=False,
                )

        self.assertEqual(0, result)
        self.assertEqual({".gitignore", "candidate.txt", "tracked.txt"}, observed)

    def test_worktree_snapshot_keeps_sparse_index_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sparse.toml").write_text("secret from index\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "sparse.toml"], cwd=root, check=True)
            subprocess.run(
                ["git", "update-index", "--skip-worktree", "sparse.toml"],
                cwd=root,
                check=True,
            )
            (root / "sparse.toml").unlink()
            observed = ""

            def inspect(command, **_kwargs):
                nonlocal observed
                snapshot = Path(command[command.index("dir") + 1])
                observed = (snapshot / "sparse.toml").read_text(encoding="utf-8")
                return 0

            with (
                patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
                patch.object(engines, "_run", side_effect=inspect),
            ):
                result = engines.betterleaks(
                    root,
                    config=".betterleaks.toml",
                    history=False,
                    staged=False,
                    include_candidates=True,
                    allow_download=False,
                )

        self.assertEqual(0, result)
        self.assertEqual("secret from index\n", observed)

    def test_betterleaks_respects_a_tracked_only_worktree_boundary(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "_materialize_worktree") as materialize,
            patch.object(engines, "_run", return_value=0),
        ):
            engines.betterleaks(
                Path("repo"),
                config=".betterleaks.toml",
                history=False,
                staged=False,
                include_candidates=False,
                allow_download=False,
            )

        self.assertEqual(Path("repo"), materialize.call_args.args[0])
        self.assertFalse(materialize.call_args.kwargs["include_candidates"])

    def test_lychee_is_always_offline_and_reads_a_git_owned_file_list(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("lychee")),
            patch.object(engines, "_markdown_paths", return_value=("README.md",)),
            patch.object(engines, "_materialize_worktree"),
            patch.object(engines, "_run", return_value=0) as invoke,
        ):
            engines.lychee(
                Path("repo"), staged=False, include_candidates=True, allow_download=False
            )

        command = list(invoke.call_args.args[0])
        self.assertIn("--offline", command)
        self.assertEqual("README.md\n", invoke.call_args.kwargs["stdin"])

    def test_lychee_excludes_worktree_deletions_but_keeps_the_index_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("[local](missing.md)\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            (root / "README.md").unlink()

            self.assertEqual(
                (),
                engines._markdown_paths(root, include_candidates=True, staged=False),
            )
            self.assertEqual(
                ("README.md",),
                engines._markdown_paths(root, include_candidates=False, staged=True),
            )

    def test_index_snapshot_does_not_follow_a_tracked_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "core.symlinks", "true"], cwd=root, check=True)
            target = root / ".git/private.txt"
            target.write_text("must not be read")
            link = root / "link.md"
            try:
                os.symlink(target, link)
            except OSError:
                self.skipTest("symlinks unavailable")
            subprocess.run(["git", "add", "link.md"], cwd=root, check=True)
            with engines.storage.temporary(root, "test-index-") as workspace:
                engines._checkout_index(root, workspace.path)
                copied = workspace.path / "link.md"
                self.assertFalse(copied.is_symlink())
                expected = subprocess.run(
                    ["git", "cat-file", "blob", ":link.md"],
                    cwd=root,
                    capture_output=True,
                    check=True,
                ).stdout
                self.assertEqual(expected, copied.read_bytes())
                workspace.remember()
            self.assertEqual("must not be read", target.read_text())
