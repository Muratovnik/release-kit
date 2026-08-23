"""Structural rules: what a repository must not carry, stated without naming anything.

Every rule here is a property of the text, not a fact about any particular machine,
person, or project. That separation is the point: a rule that had to name something
private could not live in a published tool, and a tool that shipped such a list would
publish exactly what it exists to protect. Names come from the caller instead, through
a list the guarded repository keeps out of its own history.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath

HOME_DIRECTORY = "home-directory"
ESCAPES_REPOSITORY = "escapes-repository"
FORBIDDEN_KIND = "forbidden-kind"
DECLARED_NAME = "declared-name"

# The separator is allowed to repeat because a path embedded in JSON, in a quoted
# command line, or in an escaped shell string arrives with its backslashes doubled.
# A pattern that matched exactly one reported a clean repository while four absolute
# home paths sat in a tracked settings file.
WINDOWS_USER_ROOT = re.compile(
    r"(?i)\b[A-Z]:[\\/]+(?:Users|Documents and Settings)[\\/]+([^\\/\s\"'<>:]+)"
)
POSIX_USER_ROOT = re.compile(r"(?i)(?:^|[\s\"'(=])/(?:Users|home)/([^/\s\"'<>:]+)")

# A relative path that climbs above the repository resolves only where the sibling it
# expects happens to exist, so it encodes a machine layout rather than a dependency.
#
# Whether it climbs out cannot be decided from the text alone: a documentation link
# like ../../web/src/App.vue written in docs/audits/ lands back inside the repository
# and is perfectly ordinary. So each candidate is resolved against the directory of
# the file that contains it, and only a result outside the root is a finding. A rule
# that flagged both would be turned off within a week.
#
# The two forms differ in what they resolve against. A path behind a variable such as
# ${PROJECT_DIR}/ is anchored at the root the variable names, not at the file, which
# is exactly how a hook command escapes while looking harmless in a nested file.
VARIABLE_ANCHORED_PATH = re.compile(r"\$\{[A-Z_][A-Z0-9_]*\}/((?:\.\./)+[A-Za-z0-9_.\-/]*)")
FILE_RELATIVE_PATH = re.compile(r"[\"'\s=(]((?:\.\./)+[A-Za-z0-9_.\-/]*)")


def _escapes(candidate: str, base: str) -> bool:
    resolved = posixpath.normpath(posixpath.join(base, candidate))
    return resolved == ".." or resolved.startswith("../")

# Placeholder accounts that legitimately appear in documentation and CI runners.
DEFAULT_ALLOWED_USERS = frozenset(
    {"alice", "bob", "example", "owner", "user", "runner", "runneradmin", "vagrant"}
)

DEFAULT_FORBIDDEN_SUFFIXES = frozenset(
    {".cer", ".crt", ".db", ".key", ".log", ".p12", ".pem", ".pfx", ".sqlite", ".sqlite3"}
)


def kinds_in_text(
    text: str,
    *,
    relative_path: str = "",
    names: Sequence[str] = (),
    allowed_users: Iterable[str] = DEFAULT_ALLOWED_USERS,
) -> set[str]:
    """Which kinds of non-publishable material a file's text carries.

    `relative_path` is the file's own path within the repository; it decides where a
    relative path resolves from. Left empty, the file is treated as sitting at the root.
    """
    allowed = {user.lower() for user in allowed_users}
    kinds: set[str] = set()
    for pattern in (WINDOWS_USER_ROOT, POSIX_USER_ROOT):
        if any(match.group(1).lower() not in allowed for match in pattern.finditer(text)):
            kinds.add(HOME_DIRECTORY)
            break
    directory = posixpath.dirname(relative_path)
    escaping = any(
        _escapes(match.group(1), "") for match in VARIABLE_ANCHORED_PATH.finditer(text)
    ) or any(_escapes(match.group(1), directory) for match in FILE_RELATIVE_PATH.finditer(text))
    if escaping:
        kinds.add(ESCAPES_REPOSITORY)
    if any(name in text for name in names):
        kinds.add(DECLARED_NAME)
    return kinds


def kinds_in_path(relative: str, *, forbidden_suffixes: Iterable[str] = ()) -> set[str]:
    """Kinds a path carries on its own, before its content is read."""
    suffixes = {suffix.lower() for suffix in forbidden_suffixes} or set(
        DEFAULT_FORBIDDEN_SUFFIXES
    )
    return {FORBIDDEN_KIND} if PurePosixPath(relative).suffix.lower() in suffixes else set()
