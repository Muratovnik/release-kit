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
PRIVATE_PATH = "private-path"
NOT_IGNORED = "not-ignored"

# An absolute home directory is a defect before it is anything else: it resolves on
# exactly one machine, so a tracked file carrying one is already broken in every other
# clone. The captured segment is the account name, and it is captured only to let the
# placeholder accounts of documentation and CI runners through - the rule is about the
# path being machine-local, never about whose name appears in it.
#
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
#
# An opening parenthesis is deliberately not a prefix here. It would match the target
# of every markdown link, and links are checked precisely elsewhere, against where
# they actually land; reporting both turns one defect into two lines and teaches the
# reader to skim the output.
VARIABLE_ANCHORED_PATH = re.compile(r"\$\{[A-Z_][A-Z0-9_]*\}/((?:\.\./)+[A-Za-z0-9_.\-/]*)")
FILE_RELATIVE_PATH = re.compile(r"[\"'\s=]((?:\.\./)+[A-Za-z0-9_.\-/]*)")


def _escapes(candidate: str, base: str) -> bool:
    resolved = posixpath.normpath(posixpath.join(base, candidate))
    return resolved == ".." or resolved.startswith("../")


# Placeholder accounts that legitimately appear in documentation and CI runners.
DEFAULT_ALLOWED_USERS = frozenset(
    {"alice", "bob", "example", "owner", "user", "runner", "runneradmin", "vagrant"}
)

# An account written as an address under a domain the standards reserve for
# documentation is a placeholder by definition rather than by local convention, so it
# needs no per-project declaration. RFC 2606 reserves the three second-level names and
# the first three suffixes; RFC 6761 adds localhost.
RESERVED_EXAMPLE_DOMAINS = frozenset({"example.com", "example.org", "example.net"})
RESERVED_EXAMPLE_SUFFIXES = (".example", ".invalid", ".test", ".localhost")


def _is_placeholder(account: str, allowed: Iterable[str]) -> bool:
    lowered = account.lower()
    if lowered in allowed:
        return True
    _, separator, domain = lowered.partition("@")
    if not separator or not domain:
        return False
    return domain in RESERVED_EXAMPLE_DOMAINS or domain.endswith(RESERVED_EXAMPLE_SUFFIXES)


PROSE_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc"})

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
    # Additive, never replacing: a project declaring the one placeholder its own domain
    # uses must not thereby lose alice, bob and the CI runners. Replacing was the first
    # behaviour, and pointing the scan at a real repository turned one finding into
    # eleven.
    allowed = DEFAULT_ALLOWED_USERS | {user.lower() for user in allowed_users}
    kinds: set[str] = set()
    for pattern in (WINDOWS_USER_ROOT, POSIX_USER_ROOT):
        if any(not _is_placeholder(match.group(1), allowed) for match in pattern.finditer(text)):
            kinds.add(HOME_DIRECTORY)
            break
    # Prose is exempt from this one. The rule detects a machine-local assumption in
    # configuration - a command or a setting that only resolves where a particular
    # sibling exists. Documentation legitimately prints paths that do not resolve
    # here: an example of somebody else's layout, or of the very arrangement this
    # tool configures. Applying it to prose fires on every project that documents
    # one, and a check that is wrong about the common case gets switched off. What
    # markdown is still held to: home directories, declared names, and links, which
    # are resolved properly rather than matched.
    # release-kit's own config may deliberately name a sibling private repository.
    # Those paths are resolved and ownership-checked by the overlay verifier, so the
    # generic text heuristic would be a duplicate and a false positive here.
    checks_relative_paths = (
        posixpath.splitext(relative_path)[1].lower() not in PROSE_SUFFIXES
        and relative_path != "relkit.toml"
    )
    if checks_relative_paths:
        directory = posixpath.dirname(relative_path)
        if any(
            _escapes(match.group(1), "") for match in VARIABLE_ANCHORED_PATH.finditer(text)
        ) or any(
            _escapes(match.group(1), directory) for match in FILE_RELATIVE_PATH.finditer(text)
        ):
            kinds.add(ESCAPES_REPOSITORY)
    if any(name in text for name in names):
        kinds.add(DECLARED_NAME)
    return kinds


def kinds_in_path(
    relative: str,
    *,
    forbidden_suffixes: Iterable[str] = (),
    private_paths: Iterable[str] = (),
    private_files: Iterable[str] = (),
    private_suffixes: Iterable[str] = (),
) -> set[str]:
    """Kinds a path carries on its own, before its content is read.

    A private path is the blunt instrument and the important one. Judging a file by
    what it contains cannot keep a whole surface out of a repository, because the next
    file added to it is judged again from scratch; declaring the surface private
    settles it once, for everything now in it and everything later.
    """
    normalized = PurePosixPath(relative).as_posix()
    suffix = PurePosixPath(normalized).suffix.lower()
    kinds: set[str] = set()
    suffixes = DEFAULT_FORBIDDEN_SUFFIXES | {item.lower() for item in forbidden_suffixes}
    if suffix in suffixes:
        kinds.add(FORBIDDEN_KIND)
    private_prefixes = tuple(item if item.endswith("/") else f"{item}/" for item in private_paths)
    if (
        normalized in set(private_files)
        or normalized.startswith(private_prefixes)
        or any(normalized.endswith(item) for item in private_suffixes)
    ):
        kinds.add(PRIVATE_PATH)
    return kinds
