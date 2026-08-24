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
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import PurePosixPath

HOME_DIRECTORY = "home-directory"
ESCAPES_REPOSITORY = "escapes-repository"
FORBIDDEN_KIND = "forbidden-kind"
DECLARED_NAME = "private-value"
PRIVATE_PATH = "private-path"
NOT_IGNORED = "not-ignored"
OWNER_WORKFLOW = "owner-workflow"
PERSONAL_DATA = "personal-data"
INTERNAL_PLANNING = "internal-planning"
AI_ATTRIBUTION = "ai-attribution"
MACHINE_OBSERVATION = "machine-observation"
PROVIDER_SURFACE = "provider-surface"

PRIVATE_PATTERN_KINDS = frozenset({OWNER_WORKFLOW, PERSONAL_DATA, MACHINE_OBSERVATION})


@dataclass(frozen=True)
class PrivatePattern:
    """One owner-supplied semantic rule whose expression never enters public config."""

    name: str
    kind: str
    expression: str


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
DEFAULT_PRIVATE_FILES = frozenset({".publication-owner.toml", ".publication-private-values"})

WHITESPACE = re.compile(r"\s+")

INTERNAL_PLANNING_PATTERNS = (
    re.compile(r"(?im)^\s*card\s*:\s*\d+(?:\s*,\s*\d+)*\s*$"),
    re.compile(r"(?i)\bcard\s*#\s*\d+\b"),
    re.compile(r"(?i)<!--\s*kb:[^>]+-->"),
)

AI_VENDOR = r"(?:claude|codex|chatgpt|copilot|gemini|cursor|windsurf|devin)"
AI_ATTRIBUTION_PATTERNS = (
    re.compile(
        rf"(?im)^\s*(?:co-authored-by|generated-by|reviewed-by|tested-by):[^\n]*\b{AI_VENDOR}\b"
    ),
    re.compile(rf"(?im)^\s*(?:generated|written|reviewed)\s+(?:with|by)\s+{AI_VENDOR}\b"),
)

MACHINE_OBSERVATION_PATTERNS = (
    re.compile(r"(?i)\bon\s+(?:this|my|our)\s+workstation\b"),
    re.compile(
        r"(?i)\b(?:the|this|my|our)\s+(?:real\s+)?(?:installation|workstation)\s+"
        r"(?:has|had|contains?|uses?|reports?|shows?|includes?)\b"
    ),
)


def contains_wrapped_declared_name(text: str, names: Sequence[str]) -> bool:
    """Whether whitespace wrapping hides a declared value from raw matching."""
    folded = text.casefold()
    normalized = WHITESPACE.sub(" ", folded)
    return any(
        name.casefold() not in folded and WHITESPACE.sub(" ", name.casefold()) in normalized
        for name in names
        if len(name.split()) > 1
    )


def _contains_declared_name(text: str, names: Sequence[str]) -> bool:
    """Match owner values even when prose wrapping changes the whitespace.

    Owner policy entries are deliberately one value per line. Markdown formatters
    may wrap a multi-word value across lines without changing what a reader sees,
    so a raw substring check alone lets the same private value evade the gate.
    Collapsing whitespace preserves word boundaries and does not turn separated
    tokens into a match.
    """
    folded = text.casefold()
    return any(name.casefold() in folded for name in names) or contains_wrapped_declared_name(
        text, names
    )


def provider_surface_finding(
    text: str,
    *,
    relative_path: str,
    providers: dict[str, Sequence[str]],
) -> str:
    for provider, surfaces in providers.items():
        if _contains_declared_name(text, (provider,)) and not any(
            fnmatch(relative_path, surface) for surface in surfaces
        ):
            return f"provider {provider} is outside its declared surfaces"
    return ""


def text_findings(
    text: str,
    *,
    relative_path: str = "",
    names: Sequence[str] = (),
    owner_workflows: Sequence[str] = (),
    private_patterns: Sequence[PrivatePattern] = (),
    allowed_users: Iterable[str] = DEFAULT_ALLOWED_USERS,
    forbid_ai_attribution: bool = False,
    forbid_internal_planning: bool = False,
    forbid_machine_observations: bool = False,
    providers: dict[str, Sequence[str]] | None = None,
) -> dict[str, str]:
    """Return stable finding kinds with non-sensitive explanations."""
    allowed = DEFAULT_ALLOWED_USERS | {user.lower() for user in allowed_users}
    found: dict[str, str] = {}
    for pattern in (WINDOWS_USER_ROOT, POSIX_USER_ROOT):
        if any(not _is_placeholder(match.group(1), allowed) for match in pattern.finditer(text)):
            found[HOME_DIRECTORY] = ""
            break

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
            found[ESCAPES_REPOSITORY] = ""

    if _contains_declared_name(text, names):
        found[DECLARED_NAME] = ""
    if _contains_declared_name(text, owner_workflows):
        found[OWNER_WORKFLOW] = "declared by the private owner policy"
    for pattern in private_patterns:
        if re.search(pattern.expression, text):
            found.setdefault(pattern.kind, "matched a private owner rule")
    if forbid_internal_planning and any(
        pattern.search(text) for pattern in INTERNAL_PLANNING_PATTERNS
    ):
        found[INTERNAL_PLANNING] = "internal card or knowledge-base reference"
    if forbid_ai_attribution and any(pattern.search(text) for pattern in AI_ATTRIBUTION_PATTERNS):
        found[AI_ATTRIBUTION] = "machine authorship or review attribution"
    if forbid_machine_observations and any(
        pattern.search(text) for pattern in MACHINE_OBSERVATION_PATTERNS
    ):
        found[MACHINE_OBSERVATION] = "observation about the owner's workstation"

    if detail := provider_surface_finding(
        text, relative_path=relative_path, providers=providers or {}
    ):
        found[PROVIDER_SURFACE] = detail
    return found


def kinds_in_text(
    text: str,
    *,
    relative_path: str = "",
    names: Sequence[str] = (),
    owner_workflows: Sequence[str] = (),
    private_patterns: Sequence[PrivatePattern] = (),
    allowed_users: Iterable[str] = DEFAULT_ALLOWED_USERS,
    forbid_ai_attribution: bool = False,
    forbid_internal_planning: bool = False,
    forbid_machine_observations: bool = False,
    providers: dict[str, Sequence[str]] | None = None,
) -> set[str]:
    """Which kinds of non-publishable material a file's text carries.

    `relative_path` is the file's own path within the repository; it decides where a
    relative path resolves from. Left empty, the file is treated as sitting at the root.
    """
    return set(
        text_findings(
            text,
            relative_path=relative_path,
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


def kinds_in_path(
    relative: str,
    *,
    names: Iterable[str] = (),
    owner_workflows: Iterable[str] = (),
    forbidden_suffixes: Iterable[str] = (),
    private_paths: Iterable[str] = (),
    private_files: Iterable[str] = (),
    private_suffixes: Iterable[str] = (),
    providers: dict[str, Sequence[str]] | None = None,
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
    folded = relative.casefold()
    if any(name.casefold() in folded for name in names):
        kinds.add(DECLARED_NAME)
    if any(name.casefold() in folded for name in owner_workflows):
        kinds.add(OWNER_WORKFLOW)
    for provider, surfaces in (providers or {}).items():
        if provider.casefold() in folded and not any(
            fnmatch(normalized, item) for item in surfaces
        ):
            kinds.add(PROVIDER_SURFACE)
    suffixes = DEFAULT_FORBIDDEN_SUFFIXES | {item.lower() for item in forbidden_suffixes}
    if suffix in suffixes:
        kinds.add(FORBIDDEN_KIND)
    private_prefixes = tuple(item if item.endswith("/") else f"{item}/" for item in private_paths)
    if (
        normalized in DEFAULT_PRIVATE_FILES | set(private_files)
        or normalized.startswith(private_prefixes)
        or any(normalized.endswith(item) for item in private_suffixes)
    ):
        kinds.add(PRIVATE_PATH)
    return kinds
