"""Scan a repository and report what must not be published.

The gate is a ratchet, not a verdict. A repository that adopts it usually already
carries findings, and a gate that is red on its first day is a gate somebody turns
off. So the findings present at adoption are recorded in the project's own config,
the scan fails on anything outside that record, and it also fails when a recorded
finding stops matching - otherwise the record quietly becomes fiction. The record can
only shrink.

What is scanned is what Git would publish, and by default also what is one `git add
-A` away from it: a file that is neither tracked nor ignored is not safe, it is
merely not committed yet.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from . import links, rules


@dataclass(frozen=True)
class Finding:
    path: str
    kind: str
    # What exactly was found, when the kind alone would not be actionable - which
    # link, which rule. The baseline is keyed on path and kind only, so a detail can
    # change without anyone having to re-record it.
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.path}: {self.kind}" + (f" ({self.detail})" if self.detail else "")


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


def _git(root: Path, arguments: Sequence[str], *, stdin: str | None = None):
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        input=stdin,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def scannable_paths(root: Path, *, include_candidates: bool = True) -> tuple[str, ...]:
    """What Git would publish, plus what is one `git add -A` from being published."""
    arguments = ["ls-files", "-z", "--cached"]
    if include_candidates:
        arguments += ["--others", "--exclude-standard"]
    result = _git(root, arguments)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return tuple(sorted(item for item in result.stdout.split("\0") if item))


def unignored(root: Path, required: Sequence[str]) -> list[str]:
    """Which of the paths that must be ignored are not.

    `--no-index` matters: a path already tracked is still answered against the ignore
    rules, so a surface that must never come back is verified even while it is still
    there. Without it a tracked path reports as not-ignored no matter what the rules
    say, and the check would fire on exactly the repositories mid-migration.
    """
    missing: list[str] = []
    for path in required:
        result = _git(root, ["check-ignore", "--quiet", "--no-index", "--", path])
        if result.returncode != 0:
            missing.append(path)
    return missing


def scan(
    root: Path,
    *,
    names: Sequence[str] = (),
    baseline: dict[str, Sequence[str]] | None = None,
    exclude: Sequence[str] = (),
    forbidden_suffixes: Sequence[str] = (),
    private_paths: Sequence[str] = (),
    private_files: Sequence[str] = (),
    private_suffixes: Sequence[str] = (),
    required_ignores: Sequence[str] = (),
    allowed_users: Sequence[str] = (),
    check_links: bool = True,
    include_candidates: bool = True,
    paths: Sequence[str] | None = None,
) -> Report:
    recorded = {path: set(kinds) for path, kinds in (baseline or {}).items()}
    unmatched = {path: set(kinds) for path, kinds in recorded.items()}
    report = Report()

    def record(finding: Finding) -> None:
        if finding.kind in recorded.get(finding.path, set()):
            unmatched.get(finding.path, set()).discard(finding.kind)
            report.baselined.append(finding)
        else:
            report.new.append(finding)

    candidates = (
        scannable_paths(root, include_candidates=include_candidates) if paths is None else paths
    )
    for relative in candidates:
        if any(fnmatch(relative, pattern) for pattern in exclude):
            report.excluded.append(relative)
            unmatched.pop(relative, None)
            continue
        found = rules.kinds_in_path(
            relative,
            forbidden_suffixes=forbidden_suffixes,
            private_paths=private_paths,
            private_files=private_files,
            private_suffixes=private_suffixes,
        )
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = ""  # Binary content; the path rules above still applied to it.
        except OSError:
            report.unreadable.append(relative)
            text = ""
        if text:
            found |= rules.kinds_in_text(
                text,
                relative_path=relative,
                names=names,
                allowed_users=allowed_users,
            )
        for kind in sorted(found):
            record(Finding(relative, kind))
        if text and check_links and Path(relative).suffix.lower() in links.MARKDOWN_SUFFIXES:
            for kind, detail in links.findings(text, relative, root):
                record(Finding(relative, kind, detail))

    for path in unignored(root, required_ignores):
        record(Finding(path, rules.NOT_IGNORED))

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
