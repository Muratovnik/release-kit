"""Scan a repository's tracked files and report what must not be published.

The gate is a ratchet, not a verdict. A repository that adopts it usually already
carries findings, and a gate that is red on its first day is a gate somebody turns
off. So the findings present at adoption are recorded in the project's own config,
the scan fails on anything outside that record, and it also fails when a recorded
finding stops matching - otherwise the record quietly becomes fiction. The record can
only shrink.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from . import rules


@dataclass(frozen=True)
class Finding:
    path: str
    kind: str

    def __str__(self) -> str:
        return f"{self.path}: {self.kind}"


@dataclass
class Report:
    new: list[Finding] = field(default_factory=list)
    baselined: list[Finding] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        return [str(finding) for finding in self.new] + self.stale

    @property
    def ok(self) -> bool:
        return not self.failures


def tracked_paths(root: Path) -> tuple[str, ...]:
    """Paths Git would publish. Untracked files are not this gate's business."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return tuple(sorted(item for item in result.stdout.split("\0") if item))


def scan(
    root: Path,
    *,
    names: Sequence[str] = (),
    baseline: dict[str, Sequence[str]] | None = None,
    exclude: Sequence[str] = (),
    forbidden_suffixes: Sequence[str] = (),
    allowed_users: Sequence[str] = (),
    paths: Sequence[str] | None = None,
) -> Report:
    recorded = {path: set(kinds) for path, kinds in (baseline or {}).items()}
    unmatched = {path: set(kinds) for path, kinds in recorded.items()}
    report = Report()

    for relative in tracked_paths(root) if paths is None else paths:
        if any(fnmatch(relative, pattern) for pattern in exclude):
            report.excluded.append(relative)
            unmatched.pop(relative, None)
            continue
        found = rules.kinds_in_path(relative, forbidden_suffixes=forbidden_suffixes)
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Binary content: the path rules above still applied to it.
            text = ""
        except OSError:
            report.unreadable.append(relative)
            text = ""
        if text:
            found |= rules.kinds_in_text(
                text,
                relative_path=relative,
                names=names,
                allowed_users=allowed_users or rules.DEFAULT_ALLOWED_USERS,
            )
        known = recorded.get(relative, set())
        for kind in sorted(found):
            if kind in known:
                unmatched[relative].discard(kind)
                report.baselined.append(Finding(relative, kind))
            else:
                report.new.append(Finding(relative, kind))

    # A recorded finding that no longer matches was fixed, or its file was renamed or
    # untracked. Either way the record now describes something that is not there.
    for relative, kinds in unmatched.items():
        for kind in sorted(kinds):
            if kind == rules.DECLARED_NAME and not names:
                # Nothing was declared, so this kind could not be evaluated at all.
                # Silence here is what lets a clone without the name list run the
                # structural rules instead of failing on a record it cannot check.
                continue
            report.stale.append(
                f"{relative}: no longer carries '{kind}'; remove it from the baseline"
            )
    return report
