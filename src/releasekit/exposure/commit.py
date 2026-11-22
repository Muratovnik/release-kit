"""What one commit adds, judged by the owner's private policy alone.

A commit hook runs on every commit, so this reads only what the commit brings in: the
lines its staged diff adds, the names of the paths it creates, the whole of each
changed file Git cannot show as lines, and its message. What the repository already
holds was judged when it arrived, and the push guard judges what leaves the machine.
Reading the staged diff instead of the whole index is what keeps a commit fast: the
cost follows the size of the change, not of the repository.

Only the owner's kinds are judged. A public rule a repository already fails, or a
guard that has drifted, is the full audit's verdict; refusing every commit over it
would teach the owner to bypass the hook, which costs the private policy with it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import audit, rules

MESSAGE = "commit message"
# What the commit check reports: the owner's privacy kinds, and a private path, which
# is how an owner policy file or a mounted private surface entering the index shows.
KINDS = audit.OWNER_PRIVACY_KINDS | {rules.PRIVATE_PATH}
GITLINK = "160000"
NEW_NAME = frozenset("ACR")
# `git commit --verbose` appends the diff below this line; Git drops it, and so must a
# check of the message, or the removed lines of the diff would be judged as prose.
SCISSORS = re.compile(r"^\S+ -{24} >8 -{24}\s*$", re.MULTILINE)
# Every option that a user's configuration could otherwise change in this output.
DIFF_OPTIONS = (
    "--cached",
    "--no-ext-diff",
    "--no-textconv",
    "--no-relative",
    "--find-renames",
    "--diff-filter=ACMRT",
)
C_ESCAPES = {
    ord("a"): 7,
    ord("b"): 8,
    ord("t"): 9,
    ord("n"): 10,
    ord("v"): 11,
    ord("f"): 12,
    ord("r"): 13,
    ord('"'): 34,
    ord("\\"): 92,
}


@dataclass(frozen=True)
class Change:
    """One path the commit adds or changes, as `git diff --raw` lists it."""

    path: str
    status: str
    old_object: str
    new_object: str
    new_mode: str


@dataclass
class Verdict:
    findings: list[audit.Finding] = field(default_factory=list)
    changed_paths: int = 0


def _decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")


def staged_changes(root: Path) -> list[Change]:
    """The paths the next commit adds, changes, renames or retypes; deletions add nothing."""
    result = audit._git_bytes(root, ["diff", *DIFF_OPTIONS, "--raw", "-z", "--no-abbrev"])
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace").strip()
            or "git diff could not list the staged changes"
        )
    fields = result.stdout.split(b"\0")
    changes: list[Change] = []
    index = 0
    while index < len(fields) and fields[index]:
        metadata = fields[index].decode("ascii", errors="replace").lstrip(":").split()
        if len(metadata) != 5:
            raise RuntimeError(f"git diff --raw returned an unexpected record: {fields[index]!r}")
        _old_mode, new_mode, old_object, new_object, status = metadata
        # A rename or copy names its source before its destination; only the
        # destination is in the commit.
        index += 3 if status[0] in "RC" else 2
        changes.append(
            Change(
                path=_decode_path(fields[index - 1]),
                status=status[0],
                old_object=old_object,
                new_object=new_object,
                new_mode=new_mode,
            )
        )
    return changes


def _unquote(raw: bytes) -> bytes:
    """A path Git wrote in C-style quotes, as its bytes."""
    if not (len(raw) >= 2 and raw.startswith(b'"') and raw.endswith(b'"')):
        return raw
    body = raw[1:-1]
    answer = bytearray()
    index = 0
    while index < len(body):
        byte = body[index]
        if byte != ord("\\") or index + 1 == len(body):
            answer.append(byte)
            index += 1
            continue
        escaped = body[index + 1]
        if escaped in C_ESCAPES:
            answer.append(C_ESCAPES[escaped])
            index += 2
        elif body[index + 1 : index + 4].isdigit():
            answer.append(int(body[index + 1 : index + 4], 8) & 0xFF)
            index += 4
        else:
            answer.append(escaped)
            index += 2
    return bytes(answer)


def added_hunks(root: Path) -> dict[str, list[str]]:
    """The lines each staged text file gains, one string per contiguous hunk.

    Every file Git shows as text has an entry, an empty one when the change only
    removes lines. Files Git shows as binary have none; the caller judges their blob.
    """
    result = audit._git_bytes(
        root,
        [
            "-c",
            "core.quotePath=false",
            "diff",
            *DIFF_OPTIONS,
            "--no-color",
            "--unified=0",
            "--src-prefix=a/",
            "--dst-prefix=b/",
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace").strip()
            or "git diff could not show the staged changes"
        )
    hunks: dict[str, list[str]] = {}
    target: str | None = None
    in_header = False
    current: list[bytes] | None = None

    def close() -> None:
        if target is not None and current:
            hunks.setdefault(target, []).append(
                b"\n".join(current).decode("utf-8", errors="replace")
            )

    for line in result.stdout.split(b"\n"):
        if line.startswith(b"diff --git "):
            close()
            target, in_header, current = None, True, None
        elif in_header and line.startswith(b"+++ "):
            name = _unquote(line[4:].rstrip(b"\t"))
            # A type change is shown as a deletion and a creation; the deletion's
            # side adds nothing.
            target = _decode_path(name[2:]) if name.startswith(b"b/") else None
            if target is not None:
                hunks.setdefault(target, [])
        elif line.startswith(b"@@"):
            close()
            in_header, current = False, []
        elif current is not None and line.startswith(b"+"):
            current.append(line[1:].rstrip(b"\r"))
    close()
    return hunks


def message_text(root: Path, path: Path) -> str:
    """The message Git will record: comments and a verbose diff removed, as Git does."""
    raw = path.read_bytes().decode("utf-8", errors="replace")
    if cut := SCISSORS.search(raw):
        raw = raw[: cut.start()]
    stripped = audit._git(root, ["stripspace", "--strip-comments"], stdin=raw)
    if stripped.returncode != 0:
        raise RuntimeError(stripped.stderr.strip() or "git stripspace failed")
    return stripped.stdout


def judge(
    root: Path,
    *,
    names: Sequence[str],
    owner_workflows: Sequence[str],
    private_patterns: Sequence[rules.PrivatePattern],
    private_paths: Sequence[str] = (),
    message: Path | None = None,
) -> Verdict:
    verdict = Verdict()
    changes = staged_changes(root)
    verdict.changed_paths = len(changes)
    hunks = added_hunks(root) if changes else {}
    whole: dict[str, list[Change]] = {}
    found: dict[tuple[str, str], str] = {}

    def record(path: str, details: dict[str, str]) -> None:
        for kind, detail in details.items():
            if kind in KINDS:
                found.setdefault((path, kind), detail)

    for change in changes:
        if change.status in NEW_NAME:
            record(
                change.path,
                dict.fromkeys(
                    rules.kinds_in_path(
                        change.path,
                        names=names,
                        owner_workflows=owner_workflows,
                        private_paths=private_paths,
                    ),
                    "",
                ),
            )
        if change.new_mode == GITLINK or change.old_object == change.new_object:
            continue
        if change.path in hunks:
            for text in hunks[change.path]:
                record(
                    change.path,
                    audit._owner_text_details(
                        text,
                        relative_path=change.path,
                        names=names,
                        owner_workflows=owner_workflows,
                        private_patterns=private_patterns,
                    ),
                )
        else:
            whole.setdefault(change.new_object, []).append(change)
    for object_id, payload in audit._batch_blobs(root, list(whole)):
        for change in whole[object_id]:
            record(
                change.path,
                audit._owner_payload_details(
                    change.path,
                    payload,
                    names=names,
                    owner_workflows=owner_workflows,
                    private_patterns=private_patterns,
                    inspect_archives=True,
                ),
            )
    if message is not None:
        record(
            MESSAGE,
            {
                kind: ""
                for kind in rules.kinds_in_text(
                    message_text(root, message),
                    names=names,
                    owner_workflows=owner_workflows,
                    private_patterns=private_patterns,
                )
            },
        )
    verdict.findings = [
        audit.Finding(path, kind, detail) for (path, kind), detail in sorted(found.items())
    ]
    return verdict
