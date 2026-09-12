"""Exercise the shipped starter policy with the actual candidate CLI and scanners."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fixture_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "RELKIT_"))
        and key not in ("PYTHONPATH", "PYTHONHOME", "GH_TOKEN", "GITHUB_TOKEN")
    }
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return environment


def git(root: Path, environment: dict[str, str], *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    ).stdout


def create_project(root: Path, artifact: Path, *, examples: Path | None = None):
    """Create only a new disposable root; never adopt an existing directory."""
    root.mkdir()
    environment = fixture_environment()
    git(root, environment, "init", "--template=", "-q")
    git(root, environment, "config", "user.name", "Example Maintainer")
    git(root, environment, "config", "user.email", "maintainer@example.invalid")
    git(root, environment, "config", "commit.gpgsign", "false")
    git(root, environment, "config", "core.hooksPath", ".git/hooks")
    source = examples or ROOT / "examples/audit"
    for name in ("relkit.toml", ".betterleaks.toml", ".gitignore"):
        shutil.copyfile(source / name, root / name)
    # Match the documented cold install before the first audit/engine download.
    (root / ".cache").mkdir()
    (root / ".github").mkdir()
    shutil.copyfile(artifact, root / ".github/relkit.pyz")
    (root / "README.md").write_text(
        "# Example project\n\nA synthetic audit fixture.\n", encoding="utf-8"
    )
    git(
        root,
        environment,
        "add",
        "--",
        "relkit.toml",
        ".betterleaks.toml",
        ".gitignore",
        ".github/relkit.pyz",
        "README.md",
    )
    git(root, environment, "commit", "-qm", "test: create synthetic adopter")
    return environment


def invoke(root: Path, environment: dict[str, str], expected: int, *arguments: str):
    result = subprocess.run(
        [sys.executable, str(root / ".github/relkit.pyz"), *arguments, "--json"],
        cwd=root,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    if result.returncode != expected:
        raise RuntimeError(
            f"candidate {' '.join(arguments)}: expected exit {expected}, got {result.returncode}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    response = json.loads(result.stdout)
    if response.get("schema_version") != 1 or response.get("exit_code") != expected:
        raise RuntimeError("candidate returned a mismatched CLI envelope")
    return response


def smoke(root: Path, artifact: Path) -> None:
    environment = create_project(root, artifact)
    original_head = git(root, environment, "rev-parse", "HEAD")
    passed = invoke(root, environment, 0, "audit")
    if passed["data"]["engines"] != {"betterleaks": 0, "lychee": 0}:
        raise RuntimeError("onboarding did not execute both real scanners")

    readme = root / "README.md"
    original = readme.read_bytes()
    readme.write_text("See [missing documentation](missing-document.md).\n", encoding="utf-8")
    failed = invoke(root, environment, 1, "audit")
    if not failed["data"]["engines"]["lychee"]:
        raise RuntimeError("real link scanner did not reject the broken starter-project link")

    git(root, environment, "add", "--", "README.md")
    readme.write_bytes(original)
    failed = invoke(root, environment, 1, "audit", "--staged")
    if not failed["data"]["engines"]["lychee"]:
        raise RuntimeError("staged audit used the repaired worktree instead of the broken index")
    git(root, environment, "add", "--", "README.md")
    invoke(root, environment, 0, "audit", "--history")

    if git(root, environment, "status", "--porcelain"):
        raise RuntimeError("onboarding left unexpected tracked/unignored changes")
    if git(root, environment, "rev-parse", "HEAD") != original_head:
        raise RuntimeError("audit changed the fixture commit")
    if git(root, environment, "tag", "--list") or (root / ".git/hooks/pre-push").exists():
        raise RuntimeError("audit installed a hook or created a tag")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument(
        "--work-dir", type=Path, required=True, help="New disposable project directory"
    )
    arguments = parser.parse_args()
    try:
        smoke(arguments.work_dir.absolute(), arguments.artifact.absolute())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"onboarding-smoke: {error}", file=sys.stderr)
        return 1
    print("onboarding-smoke: real worktree/staged/history checks passed; no hook or tag created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
