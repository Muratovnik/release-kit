"""Check that the private surfaces linked into a public checkout are still right.

Creating the links is dotbot's job and stays there. This is the half nobody had:
the links are created once and then quietly stop being true, and every way they do
looks like success until much later.

Four ways, each seen in practice:

* `git clean` in the public checkout removes them. The directory is simply absent,
  the client falls back to its defaults, and nothing says so.
* Something writes a copy where the link was. Now there are two of the file and they
  begin to drift - the exact failure the arrangement exists to remove.
* A surface is added to the manifest but not to the public ignore rules, so the next
  `git add -A` commits what was meant to stay out.
* The link is made but its target was never committed in the private repository, so
  a second workstation clones and the file is not there.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .manifest import Mount

MISSING = "missing"
NOT_A_LINK = "not-a-link"
OUTSIDE_PRIVATE_ROOT = "outside-private-root"
WRONG_TARGET = "wrong-target"
NOT_IGNORED = "not-ignored"
TRACKED_PUBLICLY = "tracked-publicly"
TARGET_NOT_TRACKED = "target-not-tracked"


@dataclass(frozen=True)
class Problem:
    mount: str
    kind: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.mount}: {self.kind}" + (f" ({self.detail})" if self.detail else "")


def _git(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _is_link(path: Path) -> bool:
    """A symlink, or a Windows junction, which is a reparse point Python may not call one."""
    if path.is_symlink():
        return True
    if hasattr(path, "is_junction") and path.is_junction():  # Python 3.12+
        return True
    # Last resort for the reparse points neither of the above admits to: the resolved
    # name differs from the name itself.
    return path.exists() and os.path.realpath(path) != str(path)


def check(
    mounts: tuple[Mount, ...],
    *,
    public_root: Path,
    private_root: Path,
) -> tuple[list[Problem], list[str]]:
    """Return (problems, skipped): what is wrong, and mounts that land elsewhere."""
    problems: list[Problem] = []
    skipped: list[str] = []
    resolved_public = public_root.resolve()
    resolved_private = private_root.resolve()

    for mount in mounts:
        link = mount.link_path(private_root)
        try:
            relative = link.resolve().relative_to(resolved_public)
        except (ValueError, OSError):
            try:
                relative = link.relative_to(resolved_public)
            except ValueError:
                # The manifest links this somewhere other than the repository being
                # checked. Not a defect, but it is not verified here either, and
                # silence about that would read as coverage.
                skipped.append(mount.link)
                continue
        name = relative.as_posix()

        if not link.exists() and not link.is_symlink():
            problems.append(Problem(name, MISSING, "re-run the mount script"))
            continue
        if not _is_link(link):
            problems.append(Problem(name, NOT_A_LINK, "a copy, not a link"))
        else:
            destination = Path(os.path.realpath(link))
            if resolved_private not in destination.parents:
                problems.append(Problem(name, OUTSIDE_PRIVATE_ROOT, str(destination)))
            expected = mount.target_path(private_root).resolve()
            if destination.resolve() != expected:
                problems.append(
                    Problem(name, WRONG_TARGET, f"expected {expected}, got {destination}")
                )

        if _git(public_root, ["check-ignore", "--quiet", "--no-index", "--", name]).returncode:
            problems.append(Problem(name, NOT_IGNORED, "the public repository would commit it"))
        if not _git(public_root, ["ls-files", "--error-unmatch", "--", name]).returncode:
            problems.append(Problem(name, TRACKED_PUBLICLY, "the public repository tracks it"))

        target = _git(private_root, ["ls-files", "--error-unmatch", "--", mount.target])
        if target.returncode:
            problems.append(Problem(name, TARGET_NOT_TRACKED, mount.target))

    return problems, skipped
