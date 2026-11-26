"""Commits for fixture histories, made without any hook of the machine running the tests.

Field case: a workstation's global Git configuration gained a `prepare-commit-msg` hook
that refuses agent attribution, and `git commit --no-verify` does not skip that event.
Every fixture that builds an attributed commit for the audit to find then failed before
the audit ran. A fixture's history is data the test chooses, so it is written with the
plumbing commands, which run no hook at all.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path


def commit_without_hooks(
    root: Path, message: str, *, environment: Mapping[str, str] | None = None
) -> str:
    """Commit the index on top of `HEAD`, or as a root commit, and move `HEAD` to it."""
    env = {**os.environ, **(environment or {})}

    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            encoding="utf-8",
            env=env,
            check=False,
        )

    tree = git("write-tree")
    tree.check_returncode()
    parent = git("rev-parse", "--verify", "-q", "HEAD")
    parents = ["-p", parent.stdout.strip()] if parent.returncode == 0 else []
    made = subprocess.run(
        ["git", "commit-tree", tree.stdout.strip(), *parents],
        cwd=root,
        # `git commit -m` ends the message with a newline; commit-tree stores it as given.
        input=message if message.endswith("\n") else f"{message}\n",
        capture_output=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    commit = made.stdout.strip()
    git("update-ref", "HEAD", commit).check_returncode()
    return commit
