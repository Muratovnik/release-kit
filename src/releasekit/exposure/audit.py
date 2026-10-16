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
import threading
import zipfile
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
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
# Every C0 control except tab, line feed and carriage return.
SUSPICIOUS_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
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
        suspicious_controls = SUSPICIOUS_CONTROL.search(utf8) is not None
        if utf8 and not suspicious_controls:
            return utf8
        ascii_text = "\n".join(
            match.group().decode("ascii") for match in ASCII_TEXT_RUN.finditer(payload)
        )
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
HISTORY_GREP_WORKERS = 4
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
    scope: PushScope | None = None,
) -> dict[str, set[str]]:
    """Map blobs to every committed or directly referenced tree path where they appear."""
    result = _git_bytes(
        root,
        [
            "log",
            *(HISTORY_REFS if scope is None else scope.revisions),
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
    # A public ref can expose a tree that no commit contains, including a second path
    # for a blob already seen in commit history. ls-tree preserves names and modes;
    # rev-list --objects only offers one unframed display name per object.
    for mode, object_id, relative in _public_tree_entries(root, scope):
        if any(fnmatch(relative, pattern) for pattern in exclude):
            continue
        paths.setdefault(object_id, set()).add(relative)
        if symlinks is not None and mode == "120000":
            symlinks.add((object_id, relative))
        if gitlinks is not None and mode == "160000":
            gitlinks.add((object_id, relative))
    return paths


def _public_tree_entries(
    root: Path, scope: PushScope | None = None
) -> Iterator[tuple[str, str, str]]:
    if scope is None:
        references = _git(root, ["for-each-ref", "--format=%(objectname)", *PUBLIC_REF_PREFIXES])
        if references.returncode:
            raise RuntimeError(references.stderr.strip() or "Git public object inventory failed")
        object_ids = sorted(set(references.stdout.splitlines()))
    else:
        object_ids = sorted(scope.tips)
    if not object_ids:
        return
    peeled = _git(
        root,
        ["cat-file", "--batch-check=%(objectname) %(objecttype)"],
        stdin="\n".join(f"{object_id}^{{}}" for object_id in object_ids) + "\n",
    )
    if peeled.returncode:
        raise RuntimeError(peeled.stderr.strip() or "Git public ref target inventory failed")
    for record in sorted(set(peeled.stdout.splitlines())):
        fields = record.split()
        if len(fields) != 2 or fields[1] not in {"tree", "blob", "commit"}:
            raise RuntimeError("Git public ref target inventory returned an unexpected object")
        object_id, kind = fields
        if kind == "blob":
            yield "100644", object_id, ""
        elif kind == "tree":
            tree = _git_bytes(root, ["ls-tree", "-r", "--full-tree", "-z", object_id])
            if tree.returncode:
                raise RuntimeError(
                    tree.stderr.decode("utf-8", errors="replace").strip()
                    or "Git public tree inventory failed"
                )
            for entry in tree.stdout.split(b"\0"):
                if not entry:
                    continue
                metadata, separator, raw_path = entry.partition(b"\t")
                fields = metadata.decode("ascii").split()
                if not separator or len(fields) != 3:
                    raise RuntimeError("Git public tree inventory returned malformed metadata")
                mode, _kind, blob = fields
                yield mode, blob, raw_path.decode("utf-8", errors="surrogateescape")


@dataclass(frozen=True)
class _BlobInventory:
    """Every blob a history exposes, with the paths and the modes it appears under."""

    paths: dict[str, set[str]]
    symlinks: set[tuple[str, str]]
    gitlinks: set[tuple[str, str]]


def _blob_inventory(root: Path, scope: PushScope | None = None) -> _BlobInventory:
    symlinks: set[tuple[str, str]] = set()
    gitlinks: set[tuple[str, str]] = set()
    paths = _changed_blob_paths(root, (), symlinks=symlinks, gitlinks=gitlinks, scope=scope)
    return _BlobInventory(paths, symlinks, gitlinks)


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
    scope: PushScope | None = None,
    inventory: _BlobInventory | None = None,
    cancelled: threading.Event | None = None,
) -> list[str]:
    """Judge the content of every historical blob.

    ``inventory`` is the caller's own listing of the same history, so it is read once;
    ``cancelled`` lets a caller that no longer needs the answer stop the scan between
    batches, in which case what is returned is not a verdict.
    """
    wrapped_names = tuple(name for name in names if len(name.split()) > 1)
    wrapped_workflows = tuple(name for name in owner_workflows if len(name.split()) > 1)
    if inventory is None:
        try:
            inventory = _blob_inventory(root, scope)
        except (RuntimeError, UnicodeError) as error:
            return [str(error)]
    paths = inventory.paths
    symlink_blobs = inventory.symlinks
    gitlinks = inventory.gitlinks
    if not paths:
        return []
    normal_ids = {
        object_id
        for object_id, relatives in paths.items()
        if any((object_id, relative) not in gitlinks for relative in relatives)
    }
    object_ids = sorted(normal_ids)
    if object_ids:
        checks = _git(
            root,
            ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
            stdin="\n".join(object_ids) + "\n",
        )
        if checks.returncode != 0:
            return [checks.stderr.strip() or "Git history object type inventory failed"]
        records = checks.stdout.splitlines()
    else:
        records = []
    if len(records) != len(object_ids):
        return [
            (
                "Git history object type inventory returned "
                f"{len(records)} records for {len(object_ids)} requested objects"
            )
        ]
    sized_blobs: list[tuple[str, int]] = []
    seen: set[str] = set()
    for record in records:
        fields = record.split()
        if len(fields) != 3 or not fields[2].isascii() or not fields[2].isdecimal():
            return [f"Git history object type inventory returned an unexpected record: {record}"]
        object_id, object_type, raw_size = fields
        if object_id not in normal_ids:
            return [
                f"Git history object type inventory returned an unrequested object: {object_id}"
            ]
        if object_id in seen:
            return [f"Git history object type inventory returned a duplicate object: {object_id}"]
        seen.add(object_id)
        if object_type != "blob":
            return [f"Git history object type inventory returned an unexpected record: {record}"]
        sized_blobs.append((object_id, int(raw_size)))
    if seen != set(object_ids):
        return ["Git history object type inventory omitted a requested object"]
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
    expected_sizes = dict(sized_blobs)
    for batch in _history_batches(sized_blobs):
        if cancelled is not None and cancelled.is_set():
            return failures
        try:
            blobs = _batch_blobs(root, batch)
        except (RuntimeError, ValueError) as error:
            return [str(error)]
        if any(len(payload) != expected_sizes[object_id] for object_id, payload in blobs):
            return ["Git history object size inventory disagrees with blob data"]
        for object_id, payload in blobs:
            # A blob's text does not depend on the path it appears under.
            text = _decode_text(payload)
            wrapped: dict[str, bool] = {}
            for relative in sorted(paths[object_id]):
                details: dict[str, str] = {}
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
                                if kind in UNSUPPRESSIBLE_KINDS or kind == rules.PRIVATE_PATH
                            }
                        )
                else:
                    if text and not wrapped:
                        wrapped["names"] = rules.contains_wrapped_declared_name(text, wrapped_names)
                        wrapped["workflows"] = rules.contains_wrapped_declared_name(
                            text, wrapped_workflows
                        )
                    if text and wrapped["names"]:
                        details[rules.DECLARED_NAME] = "wrapped owner value"
                    if text and wrapped["workflows"]:
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
    unsuppressible: set[str] | None = None,
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
            unsuppressible=unsuppressible,
        ).items()
        if kind in UNSUPPRESSIBLE_KINDS or kind == rules.PRIVATE_PATH
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
    scope: PushScope | None = None,
) -> list[str]:
    """Inspect public ref names and annotated tag objects outside commit history."""
    if scope is None:
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
        inventory = refs.stdout.splitlines()
    else:
        inventory = [
            f"{reference}\0{object_type}\0{object_id}"
            for reference, object_type, object_id in scope.references
        ]
    failures: list[str] = []
    tag_objects: list[str] = []
    for record in inventory:
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


def _git(
    root: Path,
    arguments: Sequence[str],
    *,
    stdin: str | None = None,
    strict_utf8: bool = False,
):
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
            errors="strict" if strict_utf8 else "replace",
            timeout=120,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"git {' '.join(arguments[:2])} timed out after 120s in {root}"
        ) from error
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
        raise RuntimeError(
            f"git {' '.join(arguments[:2])} timed out after 120s in {root}"
        ) from error
    except OSError as error:
        raise RuntimeError(f"Git byte command could not run: {error}") from error


def scannable_paths(root: Path, *, include_candidates: bool = True) -> tuple[str, ...]:
    """What Git would publish, plus what is one `git add -A` from being published."""
    arguments = ["ls-files", "-z", "--cached"]
    if include_candidates:
        arguments += ["--others", "--exclude-standard"]
    # NUL delimiters do not disable text-mode universal-newline conversion.
    # Preserve every path byte before decoding, including literal CR and CRLF.
    result = _git_bytes(root, arguments)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace").strip() or "git ls-files failed"
        )
    return tuple(
        sorted(
            item.decode("utf-8", errors="surrogateescape")
            for item in result.stdout.split(b"\0")
            if item
        )
    )


def worktree_paths(root: Path, *, include_candidates: bool = True) -> tuple[str, ...]:
    """Files present after the next `git add -A`, excluding tracked deletions."""
    modes = _tracked_modes(root)
    skipped = _skip_worktree_paths(root)
    return tuple(
        relative
        for relative in scannable_paths(root, include_candidates=include_candidates)
        if relative in skipped
        or (
            not _has_symlink_parent(root, relative)
            and (
                (root / relative).is_file()
                or (root / relative).is_symlink()
                or (modes.get(relative) == "160000" and (root / relative).is_dir())
            )
        )
    )


def _has_symlink_parent(root: Path, relative: str) -> bool:
    parent = root
    for part in Path(relative).parts[:-1]:
        parent /= part
        try:
            info = parent.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return False
        if stat.S_ISLNK(info.st_mode):
            return True
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError(
                f"publication path {relative} traverses a junction or directory reparse point "
                f"at {parent}; remove the junction or replace it with a real directory"
            )
    return False


def worktree_changes(root: Path) -> tuple[str, ...]:
    """Porcelain status entries: tracked differences and untracked candidates.

    The caller decides which of them invalidate its verdict. A tracked difference
    means a scanned file is not the committed one; an untracked entry only adds a
    file that no commit contains, which the worktree pass scans anyway.
    """
    result = _git_bytes(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace").strip() or "git status failed"
        )
    return tuple(
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    )


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


class _IndexPayloads:
    """Indexed bytes of the paths a scan reads from the index, one bounded batch at a time.

    One `git show :path` per file was most of a staged audit: a process start per
    tracked file. The same raw blobs come from `git cat-file --batch`, read in the
    order the scan asks for them and in pieces bounded by `_history_batches`, so
    memory stays bounded however large the index is. A path without a stage-0 entry
    is unreadable, as `git show :path` reported it.
    """

    def __init__(self, root: Path, wanted: Sequence[str]):
        self.root = root
        result = _git_bytes(root, ["ls-files", "--stage", "-z"])
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.decode("utf-8", errors="replace").strip()
                or "git ls-files index inventory failed"
            )
        entries: dict[str, str] = {}
        for record in result.stdout.split(b"\0"):
            metadata, separator, raw_path = record.partition(b"\t")
            fields = metadata.decode("ascii", errors="replace").split()
            if separator and len(fields) == 3 and fields[2] == "0" and fields[0] != "160000":
                entries[raw_path.decode("utf-8", errors="surrogateescape")] = fields[1]
        self.objects = {path: entries[path] for path in wanted if path in entries}
        ordered = list(dict.fromkeys(self.objects.values()))
        sizes: dict[str, int] = {}
        if ordered:
            checks = _git(
                root,
                ["cat-file", "--batch-check=%(objectname) %(objectsize)"],
                stdin="\n".join(ordered) + "\n",
            )
            for line in checks.stdout.splitlines():
                fields = line.split()
                if len(fields) == 2 and fields[1].isdecimal():
                    sizes[fields[0]] = int(fields[1])
        self.batch_of: dict[str, int] = {}
        self.batches: list[list[str]] = []
        for batch in _history_batches([(oid, sizes.get(oid, 0)) for oid in ordered]):
            for object_id in batch:
                self.batch_of[object_id] = len(self.batches)
            self.batches.append(batch)
        self.blobs: dict[str, bytes] = {}

    def read(self, relative: str) -> bytes:
        object_id = self.objects.get(relative)
        if object_id is None:
            raise OSError(f"{relative} has no merged index entry")
        if object_id not in self.blobs:
            # Only the current batch is held; the scan asks in index order.
            try:
                self.blobs = dict(_batch_blobs(self.root, self.batches[self.batch_of[object_id]]))
            except (RuntimeError, ValueError) as error:
                raise OSError(str(error)) from error
        return self.blobs[object_id]


def _payload(
    root: Path,
    relative: str,
    *,
    staged: bool,
    tracked: set[str],
    index: _IndexPayloads | None,
) -> bytes:
    if staged and relative in tracked:
        if index is None:
            raise OSError(f"{relative} was not inventoried from the index")
        return index.read(relative)
    path = root / relative
    if path.is_symlink():
        return os.readlink(path).encode("utf-8", errors="surrogateescape")
    return path.read_bytes()


def _tracked_modes(root: Path) -> dict[str, str]:
    result = _git_bytes(root, ["ls-files", "-s", "-z"])
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace").strip()
            or "git ls-files mode inventory failed"
        )
    modes: dict[str, str] = {}
    for record in result.stdout.split(b"\0"):
        metadata, separator, relative = record.partition(b"\t")
        if separator:
            modes[relative.decode("utf-8", errors="surrogateescape")] = metadata.split(maxsplit=1)[
                0
            ].decode("ascii")
    return modes


def _skip_worktree_paths(root: Path) -> set[str]:
    result = _git_bytes(root, ["ls-files", "-t", "-z"])
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace").strip()
            or "git ls-files sparse inventory failed"
        )
    return {
        record[2:].decode("utf-8", errors="surrogateescape")
        for record in result.stdout.split(b"\0")
        if len(record) >= 3 and record.startswith(b"S ")
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
    _unsuppressible: set[str] | None = None,
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
                    # Reads stay in memory: an unsafe name cannot exempt the
                    # member's private filename or bounded payload inspection.
                if entry.file_size > MAX_ARCHIVE_ENTRY_SIZE:
                    found.setdefault(ARCHIVE_LIMIT, f"archive entry too large: {entry.filename}")
                    continue
                entry_name = posixpath.normpath(entry.filename.replace("\\", "/"))
                if _unsuppressible is not None and rules.is_owner_policy_path(entry_name):
                    _unsuppressible.add(rules.PRIVATE_PATH)
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
                        _unsuppressible=_unsuppressible,
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
    unsuppressible: set[str] | None = None,
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
                _unsuppressible=unsuppressible,
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

    def record(finding: Finding, *, unsuppressible: bool = False) -> None:
        is_recorded = finding.kind in recorded.get(finding.path, set())
        if is_recorded:
            unmatched.get(finding.path, set()).discard(finding.kind)
        if not unsuppressible and finding.kind not in UNSUPPRESSIBLE_KINDS and is_recorded:
            report.baselined.append(finding)
        else:
            report.new.append(finding)

    candidates = paths
    if candidates is None:
        inventory = scannable_paths if staged else worktree_paths
        candidates = inventory(root, include_candidates=include_candidates)
    tracked = set(scannable_paths(root, include_candidates=False))
    tracked_modes = _tracked_modes(root)
    skip_worktree = _skip_worktree_paths(root)
    from_index_paths = [
        relative
        for relative in candidates
        if (staged or relative in skip_worktree) and relative in tracked
    ]
    index = _IndexPayloads(root, from_index_paths) if from_index_paths else None
    for relative in candidates:
        from_index = staged or relative in skip_worktree
        unsuppressible = {rules.PRIVATE_PATH} if rules.is_owner_policy_path(relative) else set()
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
            found |= unsuppressible
            details: dict[str, str] = {}
            if is_gitlink:
                details[EXTERNAL_REPOSITORY] = "Git submodule content is not audited"
            else:
                try:
                    payload = _payload(
                        root, relative, staged=from_index, tracked=tracked, index=index
                    )
                    details.update(
                        _owner_payload_details(
                            relative,
                            payload,
                            names=names,
                            owner_workflows=owner_workflows,
                            private_patterns=private_patterns,
                            inspect_archives=inspect_archives,
                            unsuppressible=unsuppressible,
                        )
                    )
                except OSError:
                    report.unreadable.append(relative)
            found |= set(details)
            for kind in sorted(found):
                record(
                    Finding(relative, kind, details.get(kind, "")),
                    unsuppressible=kind in unsuppressible,
                )
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
            payload = _payload(root, relative, staged=from_index, tracked=tracked, index=index)
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
                    unsuppressible=unsuppressible,
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
            record(
                Finding(relative, kind, details.get(kind, "")),
                unsuppressible=kind in unsuppressible,
            )

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


PUSH_NO_COMMITS = ("--all", "--not", "--all")
PUSH_OBJECT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
# A remote name is also what Git passes when the destination is a bare URL, which has no
# remote-tracking refs. Only a plain name may become a ref glob.
PUSH_REMOTE_NAME = re.compile(r"[A-Za-z0-9._+@-]+(?:/[A-Za-z0-9._+@-]+)*")


@dataclass(frozen=True)
class PushedRef:
    """One line Git writes to a pre-push hook: what the push would do to one ref."""

    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str

    @property
    def deletes(self) -> bool:
        return not self.local_sha.strip("0")

    @property
    def creates(self) -> bool:
        return not self.remote_sha.strip("0")


@dataclass(frozen=True)
class PushScope:
    """The part of history one push publishes, and nothing else.

    ``revisions`` selects exactly the commits the remote does not have, as arguments
    for ``git log`` and ``git rev-list``; it selects nothing when only a non-commit
    object is pushed. ``references`` are the remote ref names and their objects, and
    ``tips`` every pushed object, so a ref aimed at a tree or blob is still read.
    """

    revisions: tuple[str, ...]
    references: tuple[tuple[str, str, str], ...]
    tips: tuple[str, ...]
    commits: int = 0

    @property
    def log_opts(self) -> str:
        return " ".join(self.revisions)


def parse_push_updates(text: str) -> list[PushedRef]:
    """Read the lines of a pre-push hook's standard input."""
    updates: list[PushedRef] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split(" ")
        if len(fields) != 4 or not all(PUSH_OBJECT.fullmatch(fields[index]) for index in (1, 3)):
            raise ValueError(
                f"pre-push input line {number} is not "
                "'<local ref> <local sha> <remote ref> <remote sha>'"
            )
        updates.append(PushedRef(*fields))
    return updates


def _object_types(root: Path, names: Sequence[str]) -> list[str]:
    """The type Git reports for each name, or ``missing``, in order."""
    result = _git(
        root,
        ["cat-file", "--batch-check=%(objecttype)"],
        stdin="".join(f"{name}\n" for name in names),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Git pushed object inventory failed")
    kinds = [
        "missing" if line.endswith(" missing") else line.strip()
        for line in result.stdout.splitlines()
    ]
    if len(kinds) != len(names):
        raise RuntimeError("Git pushed object inventory returned an unexpected record count")
    return kinds


def push_scope(root: Path, remote: str, updates: Sequence[PushedRef]) -> PushScope:
    """Select what a push publishes: the commits new to ``remote``, from the pushed objects.

    An update names the remote's current tip, so the new commits are those it does not
    contain. A new ref, or a tip this checkout never fetched, falls back to everything
    the remote-tracking refs of that remote already hold; a destination with none
    (an empty remote, or a bare URL) is entirely new. Other remotes and local-only
    branches are not part of what is published, so they are not part of the verdict.
    """
    live = [update for update in updates if not update.deletes]
    if not live:
        return PushScope(PUSH_NO_COMMITS, (), ())
    local_ids = sorted({update.local_sha for update in live})
    direct = dict(zip(local_ids, _object_types(root, local_ids), strict=True))
    peeled = dict(
        zip(local_ids, _object_types(root, [f"{name}^{{}}" for name in local_ids]), strict=True)
    )
    if "missing" in (*direct.values(), *peeled.values()):
        raise RuntimeError("a pushed object is not available in this repository")
    commits = [name for name in local_ids if peeled[name] == "commit"]
    remote_ids = sorted({update.remote_sha for update in live if not update.creates})
    remote_kinds = dict(
        zip(
            remote_ids,
            _object_types(root, [f"{name}^{{commit}}" for name in remote_ids]),
            strict=True,
        )
    )
    negatives = [name for name in remote_ids if remote_kinds[name] == "commit"]
    unknown = any(update.creates for update in live) or len(negatives) != len(remote_ids)
    if unknown and PUSH_REMOTE_NAME.fullmatch(remote):
        negatives.append(f"--remotes={remote}")
    revisions = PUSH_NO_COMMITS
    count = 0
    if commits:
        revisions = (*commits, *(("--not", *negatives) if negatives else ()))
        counted = _git(root, ["rev-list", "--count", *revisions])
        if counted.returncode:
            raise RuntimeError(counted.stderr.strip() or "Git pushed revision inventory failed")
        count = int(counted.stdout.strip() or 0)
    return PushScope(
        revisions,
        tuple((update.remote_ref, direct[update.local_sha], update.local_sha) for update in live),
        tuple(local_ids),
        count,
    )


def _head_has_owner_author(root: Path, owners: Collection[str]) -> bool:
    people = _git(root, ["log", "HEAD", "--format=%an <%ae>"])
    return people.returncode == 0 and bool(owners & set(people.stdout.splitlines()))


def _history_precondition_failures(root: Path, *, shallow_matters: bool = True) -> list[str]:
    failures: list[str] = []
    shallow = _git(root, ["rev-parse", "--is-shallow-repository"])
    if shallow.returncode != 0:
        failures.append(shallow.stderr.strip() or "Git shallow-state lookup failed")
    elif shallow.stdout.strip() == "true" and shallow_matters:
        # A pushed range ends where Git says the remote has the history, so a shallow
        # boundary only makes its commit look new: that reads more, never less.
        failures.append(
            f"Git history audit requires a complete non-shallow repository; "
            f"run `git fetch --unshallow` in {root}"
        )

    replacements = _git(root, ["for-each-ref", "--format=%(refname)", "refs/replace"])
    if replacements.returncode != 0:
        failures.append(replacements.stderr.strip() or "Git replace-ref lookup failed")
    else:
        failures.extend(
            f"Git replace ref can alter the local history view: {reference}; "
            f"delete it with `git replace -d {reference.rsplit('/', 1)[-1]}`"
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
                failures.append(
                    f"Git info/grafts can alter the local history view: remove {graft_path}"
                )
        except OSError:
            failures.append("Git info/grafts could not be inspected")
    return failures


class _ThreadedScan:
    """Run one scan on its own thread, then hand back what it returned or raised.

    The thread is a daemon so that a caller that stops waiting, whether through
    ``cancel`` or because it failed itself, never keeps the process alive for a result
    nobody will read.
    """

    def __init__(self, run: Callable[[threading.Event], list[str]]) -> None:
        self._cancelled = threading.Event()
        self._outcome: tuple[list[str], BaseException | None] = ([], None)
        self._thread = threading.Thread(
            target=self._work, args=(run,), name="relkit-history-blobs", daemon=True
        )
        self._thread.start()

    def _work(self, run: Callable[[threading.Event], list[str]]) -> None:
        try:
            self._outcome = (run(self._cancelled), None)
        except BaseException as error:  # noqa: BLE001 -- re-raised by result(), on the caller's thread
            self._outcome = ([], error)

    def result(self) -> list[str]:
        self._thread.join()
        value, error = self._outcome
        if error is not None:
            raise error
        return value

    def cancel(self) -> None:
        """Stop between batches and wait for the batch in hand; the result is discarded."""
        self._cancelled.set()
        self._thread.join()


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
    scope: PushScope | None = None,
) -> list[str]:
    """Rules that must hold for every reachable commit before publication.

    ``scope`` narrows every inventory below to one push, so the same rules judge only
    the commits, ref names and tag objects that push publishes.

    ``owner_identities`` narrows the commit-level attribution rule to the owners' own
    commits. Attribution is a statement its author makes: an owner's commit must not
    credit an AI, while a contributor's pull request, and the merge a host synthesizes
    to test it, carries whatever the contributor chose to state.
    """
    provider_surfaces = providers or {}
    provenance_declarations = provenance or {}
    revisions = HISTORY_REFS if scope is None else scope.revisions
    failures = _history_precondition_failures(root, shallow_matters=scope is None)
    try:
        inventory = _blob_inventory(root, scope)
    except (RuntimeError, UnicodeError) as error:
        return [str(error)]
    history_paths = {path for paths in inventory.paths.values() for path in paths if path}
    # Reading and judging every historical blob is the longest stage and needs nothing
    # the others produce, while they mostly wait on Git. It starts now and is collected
    # last, so the findings keep the order they always had.
    blob_scan = _ThreadedScan(
        lambda cancelled: _blob_history_failures(
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
            scope=scope,
            inventory=inventory,
            cancelled=cancelled,
        )
    )
    for relative in sorted(history_paths):
        excluded = any(fnmatch(relative, pattern) for pattern in exclude)
        if excluded:
            if rules.is_owner_policy_path(relative):
                failures.append(f"history {relative}: {rules.PRIVATE_PATH}")
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
            scope=scope,
        )
    )

    # Filled only from a complete inventory, so a failed lookup exempts nothing.
    attribution_exempt: set[str] = set()
    if allowed_identities or owner_identities:
        allowed = set(allowed_identities)
        owners = set(owner_identities)
        identities = _git(root, ["log", *revisions, "--format=%H%x1f%an <%ae>%x1f%cn <%ce>"])
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
            if scope is not None and owners and not owner_authored:
                # A push of other people's commits holds no owner commit by design; the
                # misspelling this guards against shows in the history the owner is on.
                owner_authored = _head_has_owner_author(root, owners)
            if owners and not owner_authored:
                # A misspelt identity would silently exempt every commit.
                failures.append(
                    "history owner_identities match no commit author, so the attribution "
                    "rule would judge no commit"
                )
            else:
                attribution_exempt = exempt

    try:
        messages = _git(
            root,
            ["log", "--encoding=UTF-8", *revisions, "--format=%H%x1f%B%x1e"],
            strict_utf8=True,
        )
    except UnicodeError:
        failures.append("Git history message inventory was not valid UTF-8")
        messages = None
    if messages is None:
        pass
    elif messages.returncode != 0:
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

    reachable = _git(root, ["rev-list", *revisions])
    if reachable.returncode != 0:
        failures.append(reachable.stderr.strip() or "Git revision inventory failed")
        blob_scan.cancel()
        return failures
    commits = [commit for commit in reachable.stdout.splitlines() if commit]
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
    # Each batch is an independent Git process searching whole trees; running a few
    # at once keeps the order of the results, which are still read batch by batch.
    batches = [commits[offset : offset + 24] for offset in range(0, len(commits), 24)]
    with ThreadPoolExecutor(max_workers=HISTORY_GREP_WORKERS) as pool:
        searches = list(
            pool.map(
                lambda batch: _git(
                    root, ["grep", "-I", "-i", "-n", "-E", expression, *batch, "--"]
                ),
                batches,
            )
        )
    for result in searches:
        if result.returncode not in {0, 1}:
            failures.append(result.stderr.strip() or "Git history content scan failed")
            break
        for line in result.stdout.splitlines():
            fields = line.split(":", maxsplit=3)
            if len(fields) != 4:
                continue
            commit, relative, line_number, content = fields
            if scope is not None and relative not in history_paths:
                # The tree of a pushed commit also holds files the remote already has,
                # which this push neither adds nor changes.
                continue
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
    failures.extend(blob_scan.result())
    return failures
