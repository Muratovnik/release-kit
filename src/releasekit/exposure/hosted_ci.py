"""Hosted CI that a repository declares it does not run while private.

A private repository whose owner does not pay for hosted runners gets a red run on
every push: the host creates the run and then refuses to start its jobs. That red is
the configured state, not a failure, yet every reader who meets it - a person, a
status check, an agent - has to be told so again. A job that cannot start is better
not started. Guarded by the event's own repository, it is skipped instead of refused,
and it starts again, unchanged, once the repository is public.

The guard has two conjuncts because neither is enough alone. A scheduled run carries
no repository in its event, and the host's expressions compare loosely: a bare
``!github.event.repository.private`` is true there, and so is ``... .private ==
false``, since null and false both coerce to 0. Requiring the repository object first
makes a job start only when its event proves the repository public. The price is
that a scheduled job never starts under the guard, public or not.

Workflows are read without a YAML library, because the base CLI has no runtime
dependencies. Only the block layout Actions workflows use is understood; anything
else is reported as unreadable rather than guessed at, so an unfamiliar spelling can
fail the check but never pass it.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

HOSTED_CI = "hosted-ci"
PUBLIC_ONLY = "public-only"
MODES = frozenset({PUBLIC_ONLY})

GUARD = "github.event.repository && !github.event.repository.private"
_GUARD_CONJUNCTS = frozenset({"github.event.repository", "!github.event.repository.private"})

_KEY = re.compile(r"""^(?P<key>"[^"]*"|'[^']*'|[^\s"'#][^:#]*?)\s*:(?:\s+(?P<value>.*))?$""")
_BLOCK_SCALAR = re.compile(r"^[|>][+-]?[0-9]?$")
_ANCHOR = re.compile(r"^&\S+$")
_WRAPPED = re.compile(r"^\$\{\{(?P<inner>.*)\}\}$", re.DOTALL)


def is_workflow(relative: str) -> bool:
    """Whether the host would load this path as a workflow; it reads no subdirectory."""
    path = PurePosixPath(relative)
    return (
        len(path.parts) == 3
        and path.parts[:2] == (".github", "workflows")
        and path.suffix.lower() in {".yml", ".yaml"}
    )


def _strip_comment(value: str) -> str:
    quote = ""
    for index, character in enumerate(value):
        if quote:
            if character == quote:
                quote = ""
        elif character in "'\"":
            quote = character
        elif character == "#" and (index == 0 or value[index - 1] in " \t"):
            return value[:index].rstrip()
    return value.rstrip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _key(line: str) -> tuple[str, str] | None:
    match = _KEY.match(line.strip())
    if match is None:
        return None
    return _unquote(match["key"]), _strip_comment(match["value"] or "")


def _block(lines: list[str], start: int, indent: int) -> tuple[list[str], int]:
    """The lines indented deeper than ``indent`` from ``start``, and where they end."""
    end = start
    while end < len(lines) and (not _is_content(lines[end]) or _indent(lines[end]) > indent):
        end += 1
    return lines[start:end], end


def _conjuncts(expression: str) -> list[str] | None:
    """Top-level operands of ``&&``; None when an ``||`` makes the whole unprovable."""
    parts: list[str] = []
    depth = 0
    quoted = False
    start = 0
    index = 0
    while index < len(expression):
        character = expression[index]
        pair = expression[index : index + 2]
        if quoted:
            if character == "'":
                quoted = False
        elif character == "'":
            quoted = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and pair == "||":
            return None
        elif depth == 0 and pair == "&&":
            parts.append(expression[start:index])
            start = index + 2
            index += 1
        index += 1
    parts.append(expression[start:])
    flattened: list[str] = []
    for part in (item.strip() for item in parts):
        if part.startswith("(") and part.endswith(")") and _balanced(part[1:-1]):
            inner = _conjuncts(part[1:-1])
            flattened.extend(inner if inner is not None else [part])
        else:
            flattened.append(part)
    return flattened


def _balanced(expression: str) -> bool:
    depth = 0
    for character in expression:
        depth += {"(": 1, ")": -1}.get(character, 0)
        if depth < 0:
            return False
    return depth == 0


def is_guarded(condition: str) -> bool:
    """Whether a job-level ``if`` lets the job start only in a proven public repository."""
    expression = condition.strip()
    wrapped = _WRAPPED.match(expression)
    if wrapped:
        expression = wrapped["inner"]
    conjuncts = _conjuncts(expression)
    if conjuncts is None:
        return False
    normalized = {re.sub(r"\s+", "", item).casefold() for item in conjuncts}
    return _GUARD_CONJUNCTS <= normalized


def _condition(job: list[str], indent: int) -> tuple[str | None, bool]:
    """The job's own ``if`` and whether a merge key could supply another one."""
    condition = None
    merged = False
    index = 0
    while index < len(job):
        line = job[index]
        index += 1
        if not _is_content(line) or _indent(line) != indent:
            continue
        entry = _key(line)
        if entry is None:
            continue
        key, value = entry
        if key == "<<":
            merged = True
        if key != "if":
            continue
        continuation, index = _block(job, index, indent)
        pieces = [] if _BLOCK_SCALAR.match(value) else [value]
        pieces.extend(item.strip() for item in continuation if _is_content(item))
        condition = _unquote(" ".join(piece for piece in pieces if piece))
    return condition, merged


def unguarded_jobs(text: str) -> list[str]:
    """Problems that would let a job start, or be requested, in a private repository."""
    lines = text.lstrip("﻿").splitlines()
    top = next(
        (
            index
            for index, line in enumerate(lines)
            if _is_content(line) and _indent(line) == 0 and (_key(line) or ("", ""))[0] == "jobs"
        ),
        None,
    )
    if top is None:
        return []
    _key_name, value = _key(lines[top]) or ("", "")
    if value:
        return ["jobs cannot be read as a block mapping"]
    body, _end = _block(lines, top + 1, 0)
    content = [line for line in body if _is_content(line)]
    if not content:
        return []
    job_indent = _indent(content[0])
    problems: list[str] = []
    index = 0
    while index < len(body):
        line = body[index]
        index += 1
        if not _is_content(line):
            continue
        entry = _key(line) if _indent(line) == job_indent else None
        if entry is None:
            return ["jobs cannot be read as a block mapping"]
        job, value = entry
        job_lines, index = _block(body, index, job_indent)
        if value and not _ANCHOR.match(value):
            problems.append(f"job '{job}' cannot be read as a block mapping")
            continue
        properties = [item for item in job_lines if _is_content(item)]
        if not properties:
            problems.append(f"job '{job}' cannot be read as a block mapping")
            continue
        condition, merged = _condition(job_lines, _indent(properties[0]))
        if condition is None and merged:
            problems.append(f"job '{job}' takes its keys from a merge and cannot be verified")
        elif condition is None or not is_guarded(condition):
            problems.append(f"job '{job}' can start in a private repository")
    return problems
