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

import io
import os
import posixpath
import re
import stat
import struct
import subprocess
import zipfile
from collections import Counter
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from . import hosted_ci, rules

PNG_METADATA = "png-metadata"
PNG_METADATA_CHUNKS = frozenset({b"eXIf", b"iTXt", b"tEXt", b"zTXt"})
PROVENANCE_MISSING = "provenance-missing"
PROVENANCE_CONFLICT = "provenance-conflict"
MACHINE_DERIVED = "machine-derived"
EXTERNAL_CONTENT = "external-content"
EXTERNAL_REPOSITORY = "external-repository"
ARCHIVE_PATH = "archive-path"
ARCHIVE_LIMIT = "archive-limit"
ARCHIVE_SUFFIXES = frozenset(
    {
        ".aab",
        ".apk",
        ".docx",
        ".epub",
        ".jar",
        ".nupkg",
        ".odf",
        ".odp",
        ".ods",
        ".odt",
        ".pptx",
        ".pyz",
        ".vsix",
        ".whl",
        ".xlsx",
        ".xpi",
        ".zip",
    }
)
UNINSPECTED_ARCHIVE_ENDINGS = (
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tar.bz2",
    ".tar.gz",
    ".tar.xz",
    ".tbz2",
    ".tgz",
    ".txz",
    ".xz",
    ".zst",
    ".tar.zst",
    ".cab",
)
GIT_BUNDLE_ENDINGS = (".bundle", ".gitbundle")
UNINSPECTED_ARCHIVE_SIGNATURES = (
    b"\x1f\x8b",  # gzip
    b"BZh",  # bzip2
    b"\xfd7zXZ\x00",  # xz
    b"\x28\xb5\x2f\xfd",  # zstd
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"MSCF",  # Microsoft Cabinet
)
ASCII_TEXT_RUN = re.compile(rb"[\t\n\r\x20-\x7e]{4,}")
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_ENTRY_SIZE = 16 * 1024 * 1024
MAX_ARCHIVE_TOTAL_SIZE = 256 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 3
HISTORY_REFS = (
    "HEAD",
    "--branches",
    "--remotes",
    "--tags",
    "--glob=refs/pull/*",
    "--glob=refs/merge-requests/*",
    "--glob=refs/changes/*",
    "--glob=refs/notes/*",
)
PUBLIC_REF_PREFIXES = (
    "refs/heads",
    "refs/remotes",
    "refs/tags",
    "refs/pull",
    "refs/merge-requests",
    "refs/changes",
    "refs/notes",
    "refs/replace",
)
OWNER_PRIVACY_KINDS = frozenset(
    {
        rules.DECLARED_NAME,
        rules.OWNER_WORKFLOW,
        rules.PERSONAL_DATA,
        rules.MACHINE_OBSERVATION,
    }
)
UNSUPPRESSIBLE_KINDS = OWNER_PRIVACY_KINDS | {
    ARCHIVE_LIMIT,
    EXTERNAL_CONTENT,
    EXTERNAL_REPOSITORY,
}


def _decode_text(payload: bytes) -> str:
    """Decode portable text encodings without treating arbitrary binary as prose."""
    encodings: tuple[str, ...]
    if payload.startswith(b"\xef\xbb\xbf"):
        encodings = ("utf-8-sig",)
    elif payload.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encodings = ("utf-32",)
    elif payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    else:
        try:
            utf8 = payload.decode("utf-8")
        except UnicodeDecodeError:
            utf8 = ""
        ascii_text = "\n".join(
            match.group().decode("ascii") for match in ASCII_TEXT_RUN.finditer(payload)
        )
        suspicious_controls = any(
            ord(character) < 32 and character not in "\t\n\r" for character in utf8
        )
        if utf8 and not suspicious_controls:
            return utf8
        if len(payload) < 4 or len(payload) % 2:
            return utf8 or ascii_text
        lanes = (payload[0::2], payload[1::2])
        repetitive_lane = any(
            Counter(lane).most_common(1)[0][1] / len(lane) >= 0.30 for lane in lanes if lane
        )
        if not repetitive_lane:
            return utf8 or ascii_text
        candidates: list[str] = []
        candidate_encodings = ["utf-16-le", "utf-16-be"]
        if len(payload) % 4 == 0:
            candidate_encodings = ["utf-32-le", "utf-32-be", *candidate_encodings]
        for encoding in candidate_encodings:
            try:
                candidate = payload.decode(encoding)
            except UnicodeDecodeError:
                continue
            if candidate:
                candidates.append(candidate)
        if ascii_text:
            candidates.append(ascii_text)
        return "\n".join(candidates) or utf8
    try:
        return payload.decode(encodings[0])
    except UnicodeDecodeError:
        return ""


def _batch_objects(
    root: Path,
    object_ids: Sequence[str],
    *,
    expected_type: str,
) -> list[tuple[str, bytes]]:
    if not object_ids:
        return []
    try:
        environment = os.environ.copy()
        environment["GIT_NO_REPLACE_OBJECTS"] = "1"
        process = subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=root,
            input=("\n".join(object_ids) + "\n").encode(),
            check=False,
            capture_output=True,
            timeout=120,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Git blob reader timed out") from error
    except OSError as error:
        raise RuntimeError(f"Git blob reader could not run: {error}") from error
    if process.returncode != 0:
        raise RuntimeError(
            process.stderr.decode("utf-8", errors="replace").strip()
            or "git cat-file --batch failed"
        )
    answer: list[tuple[str, bytes]] = []
    offset = 0
    for _expected in object_ids:
        line_end = process.stdout.find(b"\n", offset)
        if line_end < 0:
            raise RuntimeError("git cat-file returned a truncated header")
        header = process.stdout[offset:line_end].decode("ascii", errors="replace")
        fields = header.rsplit(" ", maxsplit=2)
        if len(fields) != 3 or fields[1] != expected_type:
            raise RuntimeError(f"git cat-file returned an unexpected header: {header}")
        object_id, _kind, raw_size = fields
        size = int(raw_size)
        start = line_end + 1
        end = start + size
        if end >= len(process.stdout):
            raise RuntimeError("git cat-file returned a truncated blob")
        answer.append((object_id, process.stdout[start:end]))
        offset = end + 1
    return answer


def _batch_blobs(root: Path, object_ids: Sequence[str]) -> list[tuple[str, bytes]]:
    return _batch_objects(root, object_ids, expected_type="blob")


HISTORY_BATCH_OBJECTS = 128
HISTORY_BATCH_BYTES = 64 * 1024 * 1024


def _history_batches(sized: Sequence[tuple[str, int]]) -> Iterator[list[str]]:
    """Read history in bounded pieces.

    A fixed object count says nothing about memory: 128 versions of a vendored
    binary is gigabytes held at once, and the batch then times out on every run
    with no way for the repository to get past it. Bound the bytes instead; a
    single blob over the budget is still read, alone.
    """
    batch: list[str] = []
    total = 0
    for object_id, size in sized:
        if batch and (len(batch) >= HISTORY_BATCH_OBJECTS or total + size > HISTORY_BATCH_BYTES):
            yield batch
            batch, total = [], 0
        batch.append(object_id)
        total += size
    if batch:
        yield batch


def _changed_blob_paths(
    root: Path,
    exclude: Sequence[str],
    *,
    symlinks: set[tuple[str, str]] | None = None,
    gitlinks: set[tuple[str, str]] | None = None,
) -> dict[str, set[str]]:
    """Map every historical blob version to every path where a change introduced it."""
    result = _git_bytes(
        root,
        [
            "log",
            *HISTORY_REFS,
            "-m",
            "--root",
            "--raw",
            "--no-abbrev",
            "--no-renames",
            "--format=tformat:",
            "-z",
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace").strip()
            or "Git history blob/path inventory failed"
        )
    paths: dict[str, set[str]] = {}
    records = result.stdout.split(b"\0")
    index = 0
    while index + 1 < len(records):
        metadata = records[index].lstrip(b"\r\n")
        if not metadata.startswith(b":"):
            index += 1
            continue
        fields = metadata[1:].split(maxsplit=4)
        if len(fields) != 5:
            raise RuntimeError("Git history blob/path inventory returned malformed metadata")
        object_id = fields[3].decode("ascii", errors="strict")
        relative = records[index + 1].decode("utf-8", errors="surrogateescape")
        if set(object_id) != {"0"} and not any(fnmatch(relative, pattern) for pattern in exclude):
            paths.setdefault(object_id, set()).add(relative)
            if symlinks is not None and fields[1] == b"120000":
                symlinks.add((object_id, relative))
            if gitlinks is not None and fields[1] == b"160000":
                gitlinks.add((object_id, relative))
        index += 2
    return paths


def _blob_history_failures(
    root: Path,
    *,
    names: Sequence[str],
    owner_workflows: Sequence[str],
    private_patterns: Sequence[rules.PrivatePattern],
    forbidden_suffixes: Sequence[str],
    private_paths: Sequence[str],
    private_files: Sequence[str],
    private_suffixes: Sequence[str],
    allowed_users: Sequence[str],
    forbid_ai_attribution: bool,
    forbid_internal_planning: bool,
    forbid_machine_observations: bool,
    providers: dict[str, Sequence[str]],
    inspect_archives: bool,
    exclude: Sequence[str],
    forbid_png_metadata: bool = False,
) -> list[str]:
    wrapped_names = tuple(name for name in names if len(name.split()) > 1)
    wrapped_workflows = tuple(name for name in owner_workflows if len(name.split()) > 1)
    try:
        symlink_blobs: set[tuple[str, str]] = set()
        gitlinks: set[tuple[str, str]] = set()
        paths = _changed_blob_paths(
            root,
            (),
            symlinks=symlink_blobs,
            gitlinks=gitlinks,
        )
    except (RuntimeError, UnicodeError) as error:
        return [str(error)]
    # Tags may point directly to a tree or blob rather than to a commit. Preserve the
    # object inventory as a fallback for those objects; commit history uses the exact
    # change-derived mapping above so one blob copied to two paths is checked twice.
    inventory = _git(root, ["rev-list", "--objects", *HISTORY_REFS])
    if inventory.returncode != 0:
        return [inventory.stderr.strip() or "Git history object inventory failed"]
    for record in inventory.stdout.splitlines():
        object_id, separator, relative = record.partition(" ")
        if separator and object_id not in paths:
            paths.setdefault(object_id, set()).add(relative)
    if not paths:
        return []
    checks = _git(
        root,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        stdin="\n".join(paths) + "\n",
    )
    if checks.returncode != 0:
        return [checks.stderr.strip() or "Git history object type inventory failed"]
    sized_blobs: list[tuple[str, int]] = []
    for record in checks.stdout.splitlines():
        fields = record.split(" ")
        if len(fields) == 3 and fields[1] == "blob" and fields[2].isdigit():
            sized_blobs.append((fields[0], int(fields[2])))
    failures: list[str] = []
    failures.extend(
        f"history {relative}: {EXTERNAL_REPOSITORY} (Git submodule content is not audited)"
        for _object_id, relative in sorted(gitlinks)
    )
    semantic_kinds = {
        rules.HOME_DIRECTORY,
        rules.ESCAPES_REPOSITORY,
        rules.DECLARED_NAME,
        rules.OWNER_WORKFLOW,
        rules.PERSONAL_DATA,
        rules.INTERNAL_PLANNING,
        rules.AI_ATTRIBUTION,
        rules.MACHINE_OBSERVATION,
        rules.PROVIDER_SURFACE,
    }
    for batch in _history_batches(sized_blobs):
        try:
            blobs = _batch_blobs(root, batch)
        except (RuntimeError, ValueError) as error:
            return [str(error)]
        for object_id, payload in blobs:
            for relative in sorted(paths[object_id]):
                details: dict[str, str] = {}
                text = _decode_text(payload)
                excluded = any(fnmatch(relative, pattern) for pattern in exclude)
                if (
                    not excluded
                    and (object_id, relative) in symlink_blobs
                    and _symlink_target_escapes(relative, payload)
                ):
                    details[rules.ESCAPES_REPOSITORY] = "Git symlink target leaves the repository"
                details.update(_external_payload_details(relative, payload))
                if not excluded and forbid_png_metadata and Path(relative).suffix.lower() == ".png":
                    details.update(_png_metadata(payload))
                if excluded:
                    if text:
                        details.update(
                            _owner_text_details(
                                text,
                                relative_path=relative,
                                names=names,
                                owner_workflows=owner_workflows,
                                private_patterns=private_patterns,
                            )
                        )
                    if inspect_archives:
                        details.update(
                            {
                                kind: detail
                                for kind, detail in _archive_details(
                                    relative,
                                    payload,
                                    names=names,
                                    owner_workflows=owner_workflows,
                                    private_patterns=private_patterns,
                                    forbidden_suffixes=(),
                                    private_paths=(),
                                    private_files=(),
                                    private_suffixes=(),
                                    allowed_users=(),
                                    forbid_ai_attribution=False,
                                    forbid_internal_planning=False,
                                    forbid_machine_observations=False,
                                    providers={},
                                ).items()
                                if kind in UNSUPPRESSIBLE_KINDS
                            }
                        )
                else:
                    if text and rules.contains_wrapped_declared_name(text, wrapped_names):
                        details[rules.DECLARED_NAME] = "wrapped owner value"
                    if text and rules.contains_wrapped_declared_name(text, wrapped_workflows):
                        details[rules.OWNER_WORKFLOW] = "wrapped owner workflow"
                if text and not excluded:
                    semantic = rules.text_findings(
                        text,
                        relative_path=relative,
                        names=names,
                        owner_workflows=owner_workflows,
                        private_patterns=private_patterns,
                        allowed_users=allowed_users,
                        forbid_ai_attribution=forbid_ai_attribution,
                        forbid_internal_planning=forbid_internal_planning,
                        forbid_machine_observations=forbid_machine_observations,
                        providers=providers,
                    )
                    details.update(
                        (kind, detail)
                        for kind, detail in semantic.items()
                        if kind in semantic_kinds
                    )
                if inspect_archives and not excluded:
                    details.update(
                        _archive_details(
                            relative,
                            payload,
                            names=names,
                            owner_workflows=owner_workflows,
                            private_patterns=private_patterns,
                            forbidden_suffixes=forbidden_suffixes,
                            private_paths=private_paths,
                            private_files=private_files,
                            private_suffixes=private_suffixes,
                            allowed_users=allowed_users,
                            forbid_ai_attribution=forbid_ai_attribution,
                            forbid_internal_planning=forbid_internal_planning,
                            forbid_machine_observations=forbid_machine_observations,
                            providers=providers,
                            forbid_png_metadata=forbid_png_metadata,
                        )
                    )
                for kind, detail in sorted(details.items()):
                    suffix = f" ({detail})" if detail else ""
                    failures.append(f"history blob {object_id[:12]}:{relative}: {kind}{suffix}")
    return failures


def _owner_text_details(
    text: str,
    *,
    relative_path: str,
    names: Sequence[str],
    owner_workflows: Sequence[str],
    private_patterns: Sequence[rules.PrivatePattern],
) -> dict[str, str]:
    return {
        kind: detail
        for kind, detail in rules.text_findings(
            text,
            relative_path=relative_path,
            names=names,
            owner_workflows=owner_workflows,
            private_patterns=private_patterns,
        ).items()
        if kind in OWNER_PRIVACY_KINDS
    }


def _owner_payload_details(
    relative: str,
    payload: bytes,
    *,
    names: Sequence[str],
    owner_workflows: Sequence[str],
    private_patterns: Sequence[rules.PrivatePattern],
    inspect_archives: bool,
) -> dict[str, str]:
    return {
        kind: detail
        for kind, detail in _payload_details(
            relative,
            payload,
            names=names,
            owner_workflows=owner_workflows,
            private_patterns=private_patterns,
            forbidden_suffixes=(),
            private_paths=(),
            private_files=(),
            private_suffixes=(),
            allowed_users=(),
            forbid_ai_attribution=False,
            forbid_internal_planning=False,
            forbid_machine_observations=False,
            providers={},
            inspect_archives=inspect_archives,
        ).items()
        if kind in UNSUPPRESSIBLE_KINDS
    }


def _reference_metadata_failures(
    root: Path,
    *,
    names: Sequence[str],
    owner_workflows: Sequence[str],
    private_patterns: Sequence[rules.PrivatePattern],
    allowed_users: Sequence[str],
    allowed_identities: Sequence[str],
    forbid_ai_attribution: bool,
    forbid_internal_planning: bool,
    forbid_machine_observations: bool,
) -> list[str]:
    """Inspect public ref names and annotated tag objects outside commit history."""
    refs = _git(
        root,
        [
            "for-each-ref",
            "--format=%(refname)%00%(objecttype)%00%(objectname)",
            *PUBLIC_REF_PREFIXES,
        ],
    )
    if refs.returncode != 0:
        return [refs.stderr.strip() or "Git reference metadata inventory failed"]
    failures: list[str] = []
    tag_objects: list[str] = []
    for record in refs.stdout.splitlines():
        fields = record.split("\0")
        if len(fields) != 3:
            failures.append("Git reference metadata inventory returned malformed output")
            continue
        reference, object_type, object_id = fields
        kinds = (
            rules.kinds_in_text(
                reference,
                names=names,
                owner_workflows=owner_workflows,
                private_patterns=private_patterns,
            )
            & OWNER_PRIVACY_KINDS
        )
        failures.extend(f"history ref {reference}: {kind}" for kind in sorted(kinds))
        if object_type == "tag":
            tag_objects.append(object_id)

    allowed = set(allowed_identities)
    seen: set[str] = set()
    while tag_objects:
        object_id = tag_objects.pop()
        if object_id in seen:
            continue
        seen.add(object_id)
        result = _git_bytes(root, ["cat-file", "tag", object_id])
        if result.returncode != 0:
            failures.append(
                result.stderr.decode("utf-8", errors="replace").strip()
                or f"Git annotated tag {object_id[:12]} could not be read"
            )
            continue
        text = _decode_text(result.stdout)
        kinds = rules.kinds_in_text(
            text,
            names=names,
            owner_workflows=owner_workflows,
            private_patterns=private_patterns,
            allowed_users=allowed_users,
            forbid_ai_attribution=forbid_ai_attribution,
            forbid_internal_planning=forbid_internal_planning,
            forbid_machine_observations=forbid_machine_observations,
        ) & {
            rules.DECLARED_NAME,
            rules.HOME_DIRECTORY,
            rules.OWNER_WORKFLOW,
            rules.PERSONAL_DATA,
            rules.INTERNAL_PLANNING,
            rules.AI_ATTRIBUTION,
            rules.MACHINE_OBSERVATION,
        }
        failures.extend(f"history tag {object_id[:12]}: {kind}" for kind in sorted(kinds))
        tagger = next(
            (
                line.removeprefix("tagger ")
                for line in text.splitlines()
                if line.startswith("tagger ")
            ),
            "",
        )
        identity_match = re.fullmatch(r"(.+ <[^>\r\n]+>) [0-9]+ [+-][0-9]{4}", tagger)
        if allowed and (identity_match is None or identity_match.group(1) not in allowed):
            identity = identity_match.group(1) if identity_match is not None else "unavailable"
            failures.append(f"history tag identity is not allowed: {identity}")
        target_match = re.search(r"(?m)^object ([0-9a-f]+)$", text)
        if target_match:
            target = target_match.group(1)
            object_type = _git(root, ["cat-file", "-t", target])
            if object_type.returncode != 0:
                failures.append(
                    object_type.stderr.strip()
                    or f"Git annotated tag target {target[:12]} could not be read"
                )
            elif object_type.stdout.strip() == "tag":
                tag_objects.append(target)
    return failures


def _commit_metadata_failures(
    root: Path,
    commits: Sequence[str],
    *,
    names: Sequence[str],
    owner_workflows: Sequence[str],
    private_patterns: Sequence[rules.PrivatePattern],
    allowed_users: Sequence[str],
    forbid_ai_attribution: bool,
    forbid_internal_planning: bool,
    forbid_machine_observations: bool,
    attribution_exempt: Collection[str] = frozenset(),
) -> list[str]:
    """Inspect raw commit headers, including embedded merge-tag metadata."""
    failures: list[str] = []
    for offset in range(0, len(commits), 128):
        try:
            objects = _batch_objects(
                root,
                commits[offset : offset + 128],
                expected_type="commit",
            )
        except (RuntimeError, ValueError) as error:
            return [str(error)]
        for object_id, payload in objects:
            headers, _separator, _message = payload.partition(b"\n\n")
            text = headers.decode("utf-8", errors="replace")
            kinds = rules.kinds_in_text(
                text,
                names=names,
                owner_workflows=owner_workflows,
                private_patterns=private_patterns,
                allowed_users=allowed_users,
                forbid_ai_attribution=forbid_ai_attribution and object_id not in attribution_exempt,
                forbid_internal_planning=forbid_internal_planning,
                forbid_machine_observations=forbid_machine_observations,
            ) & {
                rules.DECLARED_NAME,
                rules.HOME_DIRECTORY,
                rules.OWNER_WORKFLOW,
                rules.PERSONAL_DATA,
                rules.INTERNAL_PLANNING,
                rules.AI_ATTRIBUTION,
                rules.MACHINE_OBSERVATION,
            }
            failures.extend(
                f"history {object_id[:12]}: commit-metadata: {kind}" for kind in sorted(kinds)
            )
    return failures


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
        return (
            [str(finding) for finding in self.new]
            + self.stale
            + [f"could not read {path}" for path in self.unreadable]
        )

    @property
    def ok(self) -> bool:
        return not self.failures


def _git(root: Path, arguments: Sequence[str], *, stdin: str | None = None):
    try:
        environment = os.environ.copy()
        environment["GIT_NO_REPLACE_OBJECTS"] = "1"
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            input=stdin,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Git command timed out") from error
    except OSError as error:
        raise RuntimeError(f"Git command could not run: {error}") from error


def _git_bytes(root: Path, arguments: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        environment = os.environ.copy()
        environment["GIT_NO_REPLACE_OBJECTS"] = "1"
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=120,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Git byte command timed out") from error
    except OSError as error:
        raise RuntimeError(f"Git byte command could not run: {error}") from error


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
    modes = _tracked_modes(root)
    skipped = _skip_worktree_paths(root)
    return tuple(
        relative
        for relative in scannable_paths(root, include_candidates=include_candidates)
        if (root / relative).is_file()
        or (root / relative).is_symlink()
        or (modes.get(relative) == "160000" and (root / relative).is_dir())
        or relative in skipped
    )


def worktree_changes(root: Path) -> tuple[str, ...]:
    """Porcelain status entries: tracked differences and untracked candidates.

    The caller decides which of them invalidate its verdict. A tracked difference
    means a scanned file is not the committed one; an untracked entry only adds a
    file that no commit contains, which the worktree pass scans anyway.
    """
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


def _png_metadata(payload: bytes) -> dict[str, str]:
    """The finding is the same; the reason an owner has to act on is not."""
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return {}
    offset = 8
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        chunk_type = payload[offset + 4 : offset + 8]
        offset += 12 + length
        if offset > len(payload):
            return {PNG_METADATA: "PNG chunk stream is truncated; metadata cannot be ruled out"}
        if chunk_type in PNG_METADATA_CHUNKS:
            return {PNG_METADATA: f"PNG {chunk_type.decode('ascii', 'replace')} chunk"}
        if chunk_type == b"IEND":
            break
    return {}


def _is_lfs_pointer(payload: bytes) -> bool:
    if len(payload) > 4096:
        return False
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return False
    return (
        bool(lines)
        and lines[0] == "version https://git-lfs.github.com/spec/v1"
        and any(re.fullmatch(r"oid sha256:[0-9a-f]{64}", line) for line in lines[1:])
        and any(re.fullmatch(r"size [0-9]+", line) for line in lines[1:])
    )


def _external_payload_details(relative: str, payload: bytes) -> dict[str, str]:
    lowered = relative.casefold()
    found: dict[str, str] = {}
    if lowered.endswith(GIT_BUNDLE_ENDINGS):
        found[EXTERNAL_REPOSITORY] = "embedded Git bundle history is not audited"
    if _is_lfs_pointer(payload):
        found[EXTERNAL_CONTENT] = "Git LFS object is outside the audited Git blob"
    opaque_archive = (
        lowered.endswith(UNINSPECTED_ARCHIVE_ENDINGS)
        or payload.startswith(UNINSPECTED_ARCHIVE_SIGNATURES)
        or (len(payload) >= 262 and payload[257:262] == b"ustar")
    )
    if opaque_archive and not zipfile.is_zipfile(io.BytesIO(payload)):
        found[EXTERNAL_CONTENT] = "compressed archive format is not safely inspected"
    return found


def _payload(root: Path, relative: str, *, staged: bool, tracked: set[str]) -> bytes:
    if staged and relative in tracked:
        result = _git_bytes(root, ["show", f":{relative}"])
        if result.returncode != 0:
            raise OSError(result.stderr.decode("utf-8", errors="replace").strip())
        return result.stdout
    path = root / relative
    if path.is_symlink():
        return os.readlink(path).encode("utf-8", errors="surrogateescape")
    return path.read_bytes()


def _tracked_modes(root: Path) -> dict[str, str]:
    result = _git(root, ["ls-files", "-s", "-z"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files mode inventory failed")
    modes: dict[str, str] = {}
    for record in result.stdout.split("\0"):
        metadata, separator, relative = record.partition("\t")
        if separator:
            modes[relative] = metadata.split(maxsplit=1)[0]
    return modes


def _skip_worktree_paths(root: Path) -> set[str]:
    result = _git(root, ["ls-files", "-t", "-z"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files sparse inventory failed")
    return {
        record[2:]
        for record in result.stdout.split("\0")
        if len(record) >= 3 and record.startswith("S ")
    }


def _symlink_target_escapes(relative: str, payload: bytes) -> bool:
    target = _decode_text(payload)
    if not target or "\n" in target or "\r" in target:
        return True
    normalized = target.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"(?i)^[a-z]:/", normalized):
        return True
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative), normalized))
    return resolved == ".." or resolved.startswith("../")


def _provenance_details(
    relative: str,
    *,
    required: Sequence[str],
    declarations: Mapping[str, str],
) -> dict[str, str]:
    matches = {kind for pattern, kind in declarations.items() if fnmatch(relative, pattern)}
    found: dict[str, str] = {}
    if len(matches) > 1:
        found[PROVENANCE_CONFLICT] = "path matches conflicting provenance declarations"
    if any(fnmatch(relative, pattern) for pattern in required) and not matches:
        found[PROVENANCE_MISSING] = "publication candidate has no provenance declaration"
    if "machine-derived" in matches:
        found[MACHINE_DERIVED] = "declared as derived from an owner's machine"
    return found


def _unsafe_archive_path(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"(?i)^[a-z]:/", normalized):
        return True
    resolved = posixpath.normpath(normalized)
    return resolved == ".." or resolved.startswith("../")


def _archive_symlink_escapes(entry_name: str, target: str) -> bool:
    if not target or "\n" in target or "\r" in target:
        return True
    normalized = target.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"(?i)^[a-z]:/", normalized):
        return True
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(entry_name), normalized))
    return resolved == ".." or resolved.startswith("../")


def _archive_details(
    relative: str,
    payload: bytes,
    *,
    names: Sequence[str],
    owner_workflows: Sequence[str],
    private_patterns: Sequence[rules.PrivatePattern],
    forbidden_suffixes: Sequence[str],
    private_paths: Sequence[str],
    private_files: Sequence[str],
    private_suffixes: Sequence[str],
    allowed_users: Sequence[str],
    forbid_ai_attribution: bool,
    forbid_internal_planning: bool,
    forbid_machine_observations: bool,
    providers: dict[str, Sequence[str]],
    forbid_png_metadata: bool = False,
    _depth: int = 0,
    _surface_relative: str | None = None,
    _budget: list[int] | None = None,
) -> dict[str, str]:
    declared_archive = Path(relative).suffix.lower() in ARCHIVE_SUFFIXES
    source = io.BytesIO(payload)
    if not zipfile.is_zipfile(source):
        return (
            {ARCHIVE_LIMIT: "archive suffix does not contain a readable ZIP archive"}
            if declared_archive
            else {}
        )
    if _depth > MAX_ARCHIVE_DEPTH:
        return {ARCHIVE_LIMIT: "nested archive depth exceeds the safe inspection budget"}
    surface_relative = _surface_relative or relative
    budget = _budget if _budget is not None else [MAX_ARCHIVE_TOTAL_SIZE]
    found: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            total_size = sum(entry.file_size for entry in entries)
            if len(entries) > MAX_ARCHIVE_ENTRIES or total_size > budget[0]:
                return {ARCHIVE_LIMIT: "archive exceeds the safe inspection budget"}
            budget[0] -= total_size
            for entry in entries:
                if entry.is_dir():
                    continue
                if _unsafe_archive_path(entry.filename):
                    found.setdefault(ARCHIVE_PATH, f"unsafe archive entry {entry.filename}")
                    continue
                if entry.file_size > MAX_ARCHIVE_ENTRY_SIZE:
                    found.setdefault(ARCHIVE_LIMIT, f"archive entry too large: {entry.filename}")
                    continue
                entry_name = posixpath.normpath(entry.filename.replace("\\", "/"))
                for kind in rules.kinds_in_path(
                    entry_name,
                    names=names,
                    owner_workflows=owner_workflows,
                    forbidden_suffixes=forbidden_suffixes,
                    private_paths=private_paths,
                    private_files=private_files,
                    private_suffixes=private_suffixes,
                ):
                    found.setdefault(kind, f"archive entry {entry_name}")
                entry_payload = archive.read(entry)
                if forbid_png_metadata and Path(entry_name).suffix.lower() == ".png":
                    for kind, detail in _png_metadata(entry_payload).items():
                        found.setdefault(kind, f"archive entry {entry_name}; {detail}")
                for kind, detail in _external_payload_details(entry_name, entry_payload).items():
                    found.setdefault(kind, f"archive entry {entry_name}; {detail}")
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    target = _decode_text(entry_payload)
                    if _archive_symlink_escapes(entry_name, target):
                        found.setdefault(
                            ARCHIVE_PATH,
                            f"unsafe archive symlink {entry_name}",
                        )
                if Path(entry_name).suffix.lower() in ARCHIVE_SUFFIXES or zipfile.is_zipfile(
                    io.BytesIO(entry_payload)
                ):
                    nested_relative = f"{relative}!/{entry_name}"
                    for kind, detail in _archive_details(
                        nested_relative,
                        entry_payload,
                        names=names,
                        owner_workflows=owner_workflows,
                        private_patterns=private_patterns,
                        forbidden_suffixes=forbidden_suffixes,
                        private_paths=private_paths,
                        private_files=private_files,
                        private_suffixes=private_suffixes,
                        allowed_users=allowed_users,
                        forbid_ai_attribution=forbid_ai_attribution,
                        forbid_internal_planning=forbid_internal_planning,
                        forbid_machine_observations=forbid_machine_observations,
                        providers=providers,
                        _depth=_depth + 1,
                        _surface_relative=surface_relative,
                        _budget=budget,
                        forbid_png_metadata=forbid_png_metadata,
                    ).items():
                        found.setdefault(kind, detail)
                text = _decode_text(entry_payload)
                for kind, detail in rules.text_findings(
                    text,
                    relative_path=entry_name,
                    names=names,
                    owner_workflows=owner_workflows,
                    private_patterns=private_patterns,
                    allowed_users=allowed_users,
                    forbid_ai_attribution=forbid_ai_attribution,
                    forbid_internal_planning=forbid_internal_planning,
                    forbid_machine_observations=forbid_machine_observations,
                    providers={},
                ).items():
                    suffix = f"; {detail}" if detail else ""
                    found.setdefault(kind, f"archive entry {entry_name}{suffix}")
                if detail := rules.provider_surface_finding(
                    text,
                    relative_path=surface_relative,
                    providers=providers,
                ):
                    found.setdefault(
                        rules.PROVIDER_SURFACE,
                        f"archive entry {entry_name}; {detail}",
                    )
                if detail := rules.provider_surface_finding(
                    entry_name,
                    relative_path=surface_relative,
                    providers=providers,
                ):
                    found.setdefault(
                        rules.PROVIDER_SURFACE,
                        f"archive entry {entry_name}; {detail}",
                    )
    except (OSError, RuntimeError, zipfile.BadZipFile):
        found[ARCHIVE_LIMIT] = "archive could not be inspected safely"
    return found


def _payload_details(
    relative: str,
    payload: bytes,
    *,
    names: Sequence[str],
    owner_workflows: Sequence[str],
    private_patterns: Sequence[rules.PrivatePattern],
    forbidden_suffixes: Sequence[str],
    private_paths: Sequence[str],
    private_files: Sequence[str],
    private_suffixes: Sequence[str],
    allowed_users: Sequence[str],
    forbid_ai_attribution: bool,
    forbid_internal_planning: bool,
    forbid_machine_observations: bool,
    providers: dict[str, Sequence[str]],
    inspect_archives: bool,
    forbid_png_metadata: bool = False,
) -> dict[str, str]:
    found = _external_payload_details(relative, payload)
    if forbid_png_metadata and Path(relative).suffix.lower() == ".png":
        found.update(_png_metadata(payload))
    try:
        text = _decode_text(payload)
    except UnicodeError:
        text = ""
    if text:
        found.update(
            rules.text_findings(
                text,
                relative_path=relative,
                names=names,
                owner_workflows=owner_workflows,
                private_patterns=private_patterns,
                allowed_users=allowed_users,
                forbid_ai_attribution=forbid_ai_attribution,
                forbid_internal_planning=forbid_internal_planning,
                forbid_machine_observations=forbid_machine_observations,
                providers=providers,
            )
        )
    if inspect_archives:
        found.update(
            _archive_details(
                relative,
                payload,
                names=names,
                owner_workflows=owner_workflows,
                private_patterns=private_patterns,
                forbidden_suffixes=forbidden_suffixes,
                private_paths=private_paths,
                private_files=private_files,
                private_suffixes=private_suffixes,
                allowed_users=allowed_users,
                forbid_ai_attribution=forbid_ai_attribution,
                forbid_internal_planning=forbid_internal_planning,
                forbid_machine_observations=forbid_machine_observations,
                providers=providers,
                forbid_png_metadata=forbid_png_metadata,
            )
        )
    return found


def scan(
    root: Path,
    *,
    names: Sequence[str] = (),
    owner_workflows: Sequence[str] = (),
    private_patterns: Sequence[rules.PrivatePattern] = (),
    baseline: dict[str, Sequence[str]] | None = None,
    exclude: Sequence[str] = (),
    forbidden_suffixes: Sequence[str] = (),
    private_paths: Sequence[str] = (),
    private_files: Sequence[str] = (),
    private_suffixes: Sequence[str] = (),
    required_ignores: Sequence[str] = (),
    allowed_users: Sequence[str] = (),
    forbid_png_metadata: bool = False,
    forbid_ai_attribution: bool = False,
    forbid_internal_planning: bool = False,
    forbid_machine_observations: bool = False,
    providers: dict[str, Sequence[str]] | None = None,
    provenance_required: Sequence[str] = (),
    provenance: Mapping[str, str] | None = None,
    inspect_archives: bool = True,
    include_candidates: bool = True,
    staged: bool = False,
    paths: Sequence[str] | None = None,
    hosted_ci_mode: str = "",
) -> Report:
    provider_surfaces = providers or {}
    provenance_declarations = provenance or {}
    recorded = {path: set(kinds) for path, kinds in (baseline or {}).items()}
    unmatched = {path: set(kinds) for path, kinds in recorded.items()}
    report = Report()

    def record(finding: Finding) -> None:
        is_recorded = finding.kind in recorded.get(finding.path, set())
        if is_recorded:
            unmatched.get(finding.path, set()).discard(finding.kind)
        if finding.kind not in UNSUPPRESSIBLE_KINDS and is_recorded:
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
    tracked_modes = _tracked_modes(root)
    skip_worktree = _skip_worktree_paths(root)
    for relative in candidates:
        from_index = staged or relative in skip_worktree
        if any(fnmatch(relative, pattern) for pattern in exclude):
            report.excluded.append(relative)
            unmatched.pop(relative, None)
            is_gitlink = tracked_modes.get(relative) == "160000" and (
                from_index or (root / relative).is_dir()
            )
            found = {EXTERNAL_REPOSITORY} if is_gitlink else set()
            found |= (
                rules.kinds_in_path(
                    relative,
                    names=names,
                    owner_workflows=owner_workflows,
                )
                & OWNER_PRIVACY_KINDS
            )
            details: dict[str, str] = {}
            if is_gitlink:
                details[EXTERNAL_REPOSITORY] = "Git submodule content is not audited"
            else:
                try:
                    payload = _payload(root, relative, staged=from_index, tracked=tracked)
                    details.update(
                        _owner_payload_details(
                            relative,
                            payload,
                            names=names,
                            owner_workflows=owner_workflows,
                            private_patterns=private_patterns,
                            inspect_archives=inspect_archives,
                        )
                    )
                except OSError:
                    report.unreadable.append(relative)
            found |= set(details)
            for kind in sorted(found):
                record(Finding(relative, kind, details.get(kind, "")))
            continue
        found = rules.kinds_in_path(
            relative,
            names=names,
            owner_workflows=owner_workflows,
            forbidden_suffixes=forbidden_suffixes,
            private_paths=private_paths,
            private_files=private_files,
            private_suffixes=private_suffixes,
            providers=provider_surfaces,
        )
        details = _provenance_details(
            relative,
            required=provenance_required,
            declarations=provenance_declarations,
        )
        try:
            is_gitlink = tracked_modes.get(relative) == "160000" and (
                from_index or (root / relative).is_dir()
            )
            if is_gitlink:
                found.add(EXTERNAL_REPOSITORY)
                details[EXTERNAL_REPOSITORY] = "Git submodule content is not audited"
                found |= set(details)
                for kind in sorted(found):
                    record(Finding(relative, kind, details.get(kind, "")))
                continue
            payload = _payload(root, relative, staged=from_index, tracked=tracked)
            is_symlink = (
                tracked_modes.get(relative) == "120000"
                if from_index
                else (root / relative).is_symlink()
            )
            if is_symlink and _symlink_target_escapes(relative, payload):
                found.add(rules.ESCAPES_REPOSITORY)
            details.update(
                _payload_details(
                    relative,
                    payload,
                    names=names,
                    owner_workflows=owner_workflows,
                    private_patterns=private_patterns,
                    forbidden_suffixes=forbidden_suffixes,
                    private_paths=private_paths,
                    private_files=private_files,
                    private_suffixes=private_suffixes,
                    allowed_users=allowed_users,
                    forbid_ai_attribution=forbid_ai_attribution,
                    forbid_internal_planning=forbid_internal_planning,
                    forbid_machine_observations=forbid_machine_observations,
                    providers=provider_surfaces,
                    inspect_archives=inspect_archives,
                    forbid_png_metadata=forbid_png_metadata,
                )
            )
            # Only the current tree: a workflow that has since been guarded or removed
            # cannot start a job, so its history is not part of this rule.
            if (
                hosted_ci_mode == hosted_ci.PUBLIC_ONLY
                and hosted_ci.is_workflow(relative)
                and (problems := hosted_ci.unguarded_jobs(_decode_text(payload)))
            ):
                details[hosted_ci.HOSTED_CI] = "; ".join(problems)
        except OSError:
            report.unreadable.append(relative)
        found |= set(details)
        for kind in sorted(found):
            record(Finding(relative, kind, details.get(kind, "")))

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


def _history_precondition_failures(root: Path) -> list[str]:
    failures: list[str] = []
    shallow = _git(root, ["rev-parse", "--is-shallow-repository"])
    if shallow.returncode != 0:
        failures.append(shallow.stderr.strip() or "Git shallow-state lookup failed")
    elif shallow.stdout.strip() == "true":
        failures.append("Git history audit requires a complete non-shallow repository")

    replacements = _git(root, ["for-each-ref", "--format=%(refname)", "refs/replace"])
    if replacements.returncode != 0:
        failures.append(replacements.stderr.strip() or "Git replace-ref lookup failed")
    else:
        failures.extend(
            f"Git replace ref can alter the local history view: {reference}"
            for reference in replacements.stdout.splitlines()
            if reference
        )

    graft_lookup = _git(root, ["rev-parse", "--git-path", "info/grafts"])
    if graft_lookup.returncode != 0:
        failures.append(graft_lookup.stderr.strip() or "Git graft lookup failed")
    else:
        graft_path = Path(graft_lookup.stdout.strip())
        if not graft_path.is_absolute():
            graft_path = root / graft_path
        try:
            if graft_path.is_file() and graft_path.read_bytes().strip():
                failures.append("Git info/grafts can alter the local history view")
        except OSError:
            failures.append("Git info/grafts could not be inspected")
    return failures


def history_failures(
    root: Path,
    *,
    names: Sequence[str] = (),
    owner_workflows: Sequence[str] = (),
    private_patterns: Sequence[rules.PrivatePattern] = (),
    private_paths: Sequence[str] = (),
    private_files: Sequence[str] = (),
    private_suffixes: Sequence[str] = (),
    forbidden_suffixes: Sequence[str] = (),
    allowed_users: Sequence[str] = (),
    allowed_identities: Sequence[str] = (),
    owner_identities: Sequence[str] = (),
    exclude: Sequence[str] = (),
    forbid_ai_attribution: bool = False,
    forbid_internal_planning: bool = False,
    forbid_machine_observations: bool = False,
    providers: dict[str, Sequence[str]] | None = None,
    provenance_required: Sequence[str] = (),
    provenance: Mapping[str, str] | None = None,
    inspect_archives: bool = True,
    forbid_png_metadata: bool = False,
) -> list[str]:
    """Rules that must hold for every reachable commit before publication.

    ``owner_identities`` narrows the commit-level attribution rule to the owners' own
    commits. Attribution is a statement its author makes: an owner's commit must not
    credit an AI, while a contributor's pull request, and the merge a host synthesizes
    to test it, carries whatever the contributor chose to state.
    """
    provider_surfaces = providers or {}
    provenance_declarations = provenance or {}
    failures = _history_precondition_failures(root)
    names_result = _git(root, ["log", *HISTORY_REFS, "--name-only", "--format="])
    if names_result.returncode != 0:
        return [names_result.stderr.strip() or "Git history path inventory failed"]
    history_paths = {line.strip() for line in names_result.stdout.splitlines() if line.strip()}
    for relative in sorted(history_paths):
        excluded = any(fnmatch(relative, pattern) for pattern in exclude)
        if excluded:
            for kind in sorted(
                rules.kinds_in_path(
                    relative,
                    names=names,
                    owner_workflows=owner_workflows,
                )
                & OWNER_PRIVACY_KINDS
            ):
                failures.append(f"history {relative}: {kind}")
            continue
        for kind in sorted(
            rules.kinds_in_path(
                relative,
                names=names,
                owner_workflows=owner_workflows,
                forbidden_suffixes=forbidden_suffixes,
                private_paths=private_paths,
                private_files=private_files,
                private_suffixes=private_suffixes,
                providers=provider_surfaces,
            )
        ):
            failures.append(f"history {relative}: {kind}")
        for kind, detail in sorted(
            _provenance_details(
                relative,
                required=provenance_required,
                declarations=provenance_declarations,
            ).items()
        ):
            failures.append(f"history {relative}: {kind} ({detail})")

    failures.extend(
        _reference_metadata_failures(
            root,
            names=names,
            owner_workflows=owner_workflows,
            private_patterns=private_patterns,
            allowed_users=allowed_users,
            allowed_identities=allowed_identities,
            forbid_ai_attribution=forbid_ai_attribution,
            forbid_internal_planning=forbid_internal_planning,
            forbid_machine_observations=forbid_machine_observations,
        )
    )

    # Filled only from a complete inventory, so a failed lookup exempts nothing.
    attribution_exempt: set[str] = set()
    if allowed_identities or owner_identities:
        allowed = set(allowed_identities)
        owners = set(owner_identities)
        identities = _git(root, ["log", *HISTORY_REFS, "--format=%H%x1f%an <%ae>%x1f%cn <%ce>"])
        if identities.returncode != 0:
            failures.append(identities.stderr.strip() or "Git history identity inventory failed")
        else:
            exempt: set[str] = set()
            owner_authored = False
            for record in identities.stdout.splitlines():
                commit, _, people = record.partition("\x1f")
                author, separator, committer = people.partition("\x1f")
                if allowed and (not separator or author not in allowed or committer not in allowed):
                    failures.append(f"history identity is not allowed: {people}")
                # A record that does not parse stays judged: exempting it would fail open.
                if not owners or not separator:
                    continue
                if author in owners:
                    owner_authored = True
                else:
                    exempt.add(commit)
                # An agent can take the author or committer field instead of a trailer.
                # When an owner holds the other field, the commit is still the owner's.
                if (
                    forbid_ai_attribution
                    and owners & {author, committer}
                    and any(rules.is_ai_identity(person) for person in (author, committer))
                ):
                    failures.append(
                        f"history {commit[:12]}: commit-identity: {rules.AI_ATTRIBUTION}"
                    )
            if owners and not owner_authored:
                # A misspelt identity would silently exempt every commit.
                failures.append(
                    "history owner_identities match no commit author, so the attribution "
                    "rule would judge no commit"
                )
            else:
                attribution_exempt = exempt

    messages = _git(root, ["log", *HISTORY_REFS, "--format=%H%x1f%B%x1e"])
    if messages.returncode != 0:
        failures.append(messages.stderr.strip() or "Git history message inventory failed")
    else:
        for record in messages.stdout.split("\x1e"):
            commit, separator, message = record.strip().partition("\x1f")
            if not separator:
                continue
            message_kinds = rules.kinds_in_text(
                message,
                names=names,
                owner_workflows=owner_workflows,
                private_patterns=private_patterns,
                allowed_users=allowed_users,
                forbid_ai_attribution=forbid_ai_attribution and commit not in attribution_exempt,
                forbid_internal_planning=forbid_internal_planning,
                forbid_machine_observations=forbid_machine_observations,
            ) & {
                rules.DECLARED_NAME,
                rules.HOME_DIRECTORY,
                rules.OWNER_WORKFLOW,
                rules.PERSONAL_DATA,
                rules.INTERNAL_PLANNING,
                rules.AI_ATTRIBUTION,
                rules.MACHINE_OBSERVATION,
            }
            for kind in sorted(message_kinds):
                failures.append(f"history {commit[:12]}: commit-message: {kind}")

    revisions = _git(root, ["rev-list", *HISTORY_REFS])
    if revisions.returncode != 0:
        failures.append(revisions.stderr.strip() or "Git revision inventory failed")
        return failures
    commits = [commit for commit in revisions.stdout.splitlines() if commit]
    failures.extend(
        _commit_metadata_failures(
            root,
            commits,
            names=names,
            owner_workflows=owner_workflows,
            private_patterns=private_patterns,
            allowed_users=allowed_users,
            forbid_ai_attribution=forbid_ai_attribution,
            forbid_internal_planning=forbid_internal_planning,
            forbid_machine_observations=forbid_machine_observations,
            attribution_exempt=attribution_exempt,
        )
    )
    patterns = [r"[A-Za-z]:[\\/]+(Users|Documents and Settings)[\\/]+", r"/(Users|home)/"]
    patterns.extend(re.escape(name) for name in names)
    patterns.extend(re.escape(name) for name in owner_workflows)
    patterns.extend(re.escape(name) for name in provider_surfaces)
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
                kinds = set(
                    _owner_text_details(
                        content,
                        relative_path=relative,
                        names=names,
                        owner_workflows=owner_workflows,
                        private_patterns=private_patterns,
                    )
                )
            else:
                kinds = rules.kinds_in_text(
                    content,
                    relative_path=relative,
                    names=names,
                    owner_workflows=owner_workflows,
                    allowed_users=allowed_users,
                    providers=provider_surfaces,
                )
            for kind in sorted(kinds):
                finding = f"history {commit[:12]}:{relative}:{line_number}: {kind}"
                if finding not in seen:
                    seen.add(finding)
                    failures.append(finding)
    failures.extend(
        _blob_history_failures(
            root,
            names=names,
            owner_workflows=owner_workflows,
            private_patterns=private_patterns,
            forbidden_suffixes=forbidden_suffixes,
            private_paths=private_paths,
            private_files=private_files,
            private_suffixes=private_suffixes,
            allowed_users=allowed_users,
            forbid_ai_attribution=forbid_ai_attribution,
            forbid_internal_planning=forbid_internal_planning,
            forbid_machine_observations=forbid_machine_observations,
            providers=provider_surfaces,
            inspect_archives=inspect_archives,
            exclude=exclude,
            forbid_png_metadata=forbid_png_metadata,
        )
    )
    return failures
