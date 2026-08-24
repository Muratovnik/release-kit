from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import engines


class EngineCommandTests(unittest.TestCase):
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
                allow_download=False,
            )

        self.assertEqual(0, result)
        command = list(invoke.call_args.args[0])
        self.assertIn("--redact", command)
        self.assertIn("--verbose", command)
        self.assertIn("git", command)
        self.assertIn("--log-opts=HEAD --branches --remotes --tags", command)
        self.assertNotIn("--pre-commit", command)
        environment = invoke.call_args.kwargs["environment"]
        self.assertIn("safe.directory", environment.values())

    def test_betterleaks_staged_scope_is_explicit(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "_run", return_value=0) as invoke,
        ):
            engines.betterleaks(
                Path("repo"),
                config=".betterleaks.toml",
                history=False,
                staged=True,
                allow_download=False,
            )

        command = list(invoke.call_args.args[0])
        self.assertIn("--pre-commit", command)
        self.assertIn("--staged", command)
        self.assertNotIn("--log-opts=HEAD --branches --remotes --tags", command)

    def test_betterleaks_worktree_scope_uses_the_directory_engine(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "scannable_paths", return_value=()),
            patch.object(engines, "_run", return_value=0) as invoke,
        ):
            engines.betterleaks(
                Path("repo"),
                config=".betterleaks.toml",
                history=False,
                staged=False,
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
                    allow_download=False,
                )

        self.assertEqual(0, result)
        self.assertEqual({".gitignore", "candidate.txt", "tracked.txt"}, observed)

    def test_lychee_is_always_offline_and_reads_a_git_owned_file_list(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("lychee")),
            patch.object(engines, "_markdown_paths", return_value=("README.md",)),
            patch.object(engines, "_run", return_value=0) as invoke,
        ):
            engines.lychee(
                Path("repo"), staged=False, include_candidates=True, allow_download=False
            )

        command = list(invoke.call_args.args[0])
        self.assertIn("--offline", command)
        self.assertEqual("README.md\n", invoke.call_args.kwargs["stdin"])
