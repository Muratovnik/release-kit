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

import re
import struct
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from . import rules

PNG_METADATA = "png-metadata"
PNG_METADATA_CHUNKS = frozenset({b"eXIf", b"iTXt", b"tEXt", b"zTXt"})
HISTORY_REFS = ("HEAD", "--branches", "--remotes", "--tags")


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


def _git_bytes(root: Path, arguments: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
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


def worktree_paths(root: Path, *, include_candidates: bool = True) -> tuple[str, ...]:
    """Files present after the next `git add -A`, excluding tracked deletions."""
    return tuple(
        relative
        for relative in scannable_paths(root, include_candidates=include_candidates)
        if (root / relative).is_file() or (root / relative).is_symlink()
    )


def worktree_changes(root: Path) -> tuple[str, ...]:
    """Tracked or untracked changes that make a history verdict non-reproducible."""
    result = _git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git status failed")
    return tuple(item for item in result.stdout.split("\0") if item)


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


def _png_metadata(payload: bytes) -> set[str]:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return set()
    offset = 8
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        chunk_type = payload[offset + 4 : offset + 8]
        offset += 12 + length
        if offset > len(payload):
            return {PNG_METADATA}
        if chunk_type in PNG_METADATA_CHUNKS:
            return {PNG_METADATA}
        if chunk_type == b"IEND":
            break
    return set()


def _payload(root: Path, relative: str, *, staged: bool, tracked: set[str]) -> bytes:
    if staged and relative in tracked:
        result = _git_bytes(root, ["show", f":{relative}"])
        if result.returncode != 0:
            raise OSError(result.stderr.decode("utf-8", errors="replace").strip())
        return result.stdout
    return (root / relative).read_bytes()


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
    forbid_png_metadata: bool = False,
    include_candidates: bool = True,
    staged: bool = False,
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

    candidates = paths
    if candidates is None:
        inventory = scannable_paths if staged else worktree_paths
        candidates = inventory(root, include_candidates=include_candidates)
    tracked_result = _git(root, ["ls-files", "-z", "--cached"])
    if tracked_result.returncode != 0:
        raise RuntimeError(tracked_result.stderr.strip() or "git ls-files failed")
    tracked = {item for item in tracked_result.stdout.split("\0") if item}
    for relative in candidates:
        if any(fnmatch(relative, pattern) for pattern in exclude):
            report.excluded.append(relative)
            unmatched.pop(relative, None)
            continue
        found = rules.kinds_in_path(
            relative,
            names=names,
            forbidden_suffixes=forbidden_suffixes,
            private_paths=private_paths,
            private_files=private_files,
            private_suffixes=private_suffixes,
        )
        try:
            payload = _payload(root, relative, staged=staged, tracked=tracked)
            if forbid_png_metadata and Path(relative).suffix.lower() == ".png":
                found |= _png_metadata(payload)
            text = payload.decode("utf-8")
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


def history_failures(
    root: Path,
    *,
    names: Sequence[str] = (),
    private_paths: Sequence[str] = (),
    private_files: Sequence[str] = (),
    private_suffixes: Sequence[str] = (),
    forbidden_suffixes: Sequence[str] = (),
    allowed_users: Sequence[str] = (),
    allowed_identities: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> list[str]:
    """Rules that must hold for every reachable commit before publication."""
    failures: list[str] = []
    names_result = _git(root, ["log", *HISTORY_REFS, "--name-only", "--format="])
    if names_result.returncode != 0:
        return [names_result.stderr.strip() or "Git history path inventory failed"]
    history_paths = {line.strip() for line in names_result.stdout.splitlines() if line.strip()}
    for relative in sorted(history_paths):
        if any(fnmatch(relative, pattern) for pattern in exclude):
            continue
        for kind in sorted(
            rules.kinds_in_path(
                relative,
                names=names,
                forbidden_suffixes=forbidden_suffixes,
                private_paths=private_paths,
                private_files=private_files,
                private_suffixes=private_suffixes,
            )
        ):
            failures.append(f"history {relative}: {kind}")

    if allowed_identities:
        allowed = set(allowed_identities)
        identities = _git(root, ["log", *HISTORY_REFS, "--format=%an <%ae>%x1f%cn <%ce>"])
        if identities.returncode != 0:
            failures.append(identities.stderr.strip() or "Git history identity inventory failed")
        else:
            for record in identities.stdout.splitlines():
                author, separator, committer = record.partition("\x1f")
                if not separator or author not in allowed or committer not in allowed:
                    failures.append(f"history identity is not allowed: {record}")

    messages = _git(root, ["log", *HISTORY_REFS, "--format=%H%x1f%B%x1e"])
    if messages.returncode != 0:
        failures.append(messages.stderr.strip() or "Git history message inventory failed")
    else:
        for record in messages.stdout.split("\x1e"):
            commit, separator, message = record.strip().partition("\x1f")
            if not separator:
                continue
            message_kinds = rules.kinds_in_text(
                message, names=names, allowed_users=allowed_users
            ) & {rules.DECLARED_NAME, rules.HOME_DIRECTORY}
            for kind in sorted(message_kinds):
                failures.append(f"history {commit[:12]}: commit-message: {kind}")

    revisions = _git(root, ["rev-list", *HISTORY_REFS])
    if revisions.returncode != 0:
        failures.append(revisions.stderr.strip() or "Git revision inventory failed")
        return failures
    commits = [commit for commit in revisions.stdout.splitlines() if commit]
    patterns = [r"[A-Za-z]:[\\/]+(Users|Documents and Settings)[\\/]+", r"/(Users|home)/"]
    patterns.extend(re.escape(name) for name in names)
    expression = "(" + "|".join(patterns) + ")"
    seen: set[str] = set()
    for offset in range(0, len(commits), 24):
        batch = commits[offset : offset + 24]
        result = _git(root, ["grep", "-I", "-i", "-n", "-E", expression, *batch, "--"])
        if result.returncode not in {0, 1}:
            failures.append(result.stderr.strip() or "Git history content scan failed")
            break
        for line in result.stdout.splitlines():
            fields = line.split(":", maxsplit=3)
            if len(fields) != 4:
                continue
            commit, relative, line_number, content = fields
            if any(fnmatch(relative, pattern) for pattern in exclude):
                continue
            for kind in sorted(
                rules.kinds_in_text(
                    content,
                    relative_path=relative,
                    names=names,
                    allowed_users=allowed_users,
                )
            ):
                finding = f"history {commit[:12]}:{relative}:{line_number}: {kind}"
                if finding not in seen:
                    seen.add(finding)
                    failures.append(finding)
    return failures
