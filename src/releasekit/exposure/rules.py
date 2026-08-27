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
import unicodedata
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
POSIX_USER_ROOT = re.compile(r"(?i)(?<![A-Za-z0-9/])/(?:Users|home|var/home)/([^/\s\"'<>:]+)")
UNC_USER_ROOT = re.compile(
    r"(?i)(?<![A-Za-z0-9:])[\\/]{2,}[^\\/\s\"'<>:]+[\\/]+"
    r"(?:(?:[A-Z]\$)[\\/]+)?(?:Users|Documents and Settings)[\\/]+([^\\/\s\"'<>:]+)"
)
WINDOWS_COMPAT_USER_ROOT = re.compile(
    r"(?i)(?<![A-Za-z0-9/])/(?:mnt|cygdrive)/[a-z]/"
    r"(?:Users|Documents and Settings)/([^/\s\"'<>:]+)"
)
ROOT_HOME = re.compile(r"(?i)(?<![A-Za-z0-9/])/root(?:/|(?=$|[\s\"')]))")
FILE_URI_USER_ROOT = re.compile(
    r"(?i)\bfile:(?://[^/\s\"'<>:]+)?/+"
    r"(?:Users|home|var/home)/([^/\s\"'<>:]+)"
)
FILE_URI_ROOT_HOME = re.compile(r"(?i)\bfile:(?://[^/\s\"'<>:]+)?/+root(?:/|(?=$|[\s\"')]))")

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
FILE_RELATIVE_PATH = re.compile(r"(?:^|[\"'\s=])((?:\.\./)+[A-Za-z0-9_.\-/]*)")


def _escapes(candidate: str, base: str) -> bool:
    resolved = posixpath.normpath(posixpath.join(base, candidate))
    return resolved == ".." or resolved.startswith("../")


# Placeholder accounts that legitimately appear in documentation and CI runners.
DEFAULT_ALLOWED_USERS = frozenset({"example", "runner", "runneradmin", "vagrant"})

# An account written as an address under a domain the standards reserve for
# documentation is a placeholder by definition rather than by local convention, so it
# needs no per-project declaration. RFC 2606 reserves the three second-level names and
# the first three suffixes; RFC 6761 adds localhost.
RESERVED_EXAMPLE_DOMAINS = frozenset({"example.com", "example.org", "example.net"})
RESERVED_EXAMPLE_SUFFIXES = (".example", ".invalid", ".test", ".localhost")


def _is_placeholder(account: str, allowed: Iterable[str]) -> bool:
    lowered = _normalized(account).casefold()
    if lowered in allowed:
        return True
    _, separator, domain = lowered.partition("@")
    if not separator or not domain:
        return False
    return domain in RESERVED_EXAMPLE_DOMAINS or domain.endswith(RESERVED_EXAMPLE_SUFFIXES)


PROSE_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc"})

DEFAULT_FORBIDDEN_SUFFIXES = frozenset(
    {
        ".bundle",
        ".cer",
        ".crt",
        ".db",
        ".gitbundle",
        ".key",
        ".log",
        ".p12",
        ".pem",
        ".pfx",
        ".sqlite",
        ".sqlite3",
    }
)
DEFAULT_PRIVATE_FILES = frozenset({".publication-owner.toml", ".publication-private-values"})

WHITESPACE = re.compile(r"\s+")


def _normalized(text: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", text)
        if unicodedata.category(character) != "Cf"
    )


INTERNAL_PLANNING_PATTERNS = (
    re.compile(r"(?im)^\s*card\s*:\s*\d+(?:\s*,\s*\d+)*\s*$"),
    re.compile(r"(?i)\bcard\s*#\s*\d+\b"),
    re.compile(r"(?i)<!--\s*kb:[^>]+-->"),
)

AI_VENDOR = (
    r"(?:(?:anthropic\s+)?claude|(?:openai\s+)?(?:codex|chatgpt)|"
    r"(?:github\s+)?copilot|(?:google\s+)?gemini|cursor|windsurf|devin|"
    r"(?:ai|coding)\s+agent)"
)
COMMENT_PREFIX = r"(?:[#;]|//|/\*+|\*|<!--)?\s*"
AI_ATTRIBUTION_PATTERNS = (
    re.compile(
        rf"(?im)^\s*{COMMENT_PREFIX}"
        rf"(?:co-authored-by|generated-by|reviewed-by|tested-by):[^\n]*\b{AI_VENDOR}\b"
    ),
    re.compile(
        rf"(?im)^\s*{COMMENT_PREFIX}"
        rf"(?:this\s+(?:file|code|change|commit|pull\s+request)\s+(?:was|is)\s+)?"
        rf"(?:generated|written|reviewed|tested|created|ai[- ]assisted)\s+"
        rf"(?:with|by|using)\s+{AI_VENDOR}\b"
    ),
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
    folded = _normalized(text).casefold()
    normalized = WHITESPACE.sub(" ", folded)
    return any(
        _normalized(name).casefold() not in folded
        and WHITESPACE.sub(" ", _normalized(name).casefold()) in normalized
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
    folded = _normalized(text).casefold()
    return any(_normalized(name).casefold() in folded for name in names) or (
        contains_wrapped_declared_name(text, names)
    )


def provider_surface_finding(
    text: str,
    *,
    relative_path: str,
    providers: dict[str, Sequence[str]],
) -> str:
    # The public configuration is the declaration of the provider contract, not a
    # product surface that consumes provider data. Requiring adopters to allow the
    # configuration path would make every valid declaration reject itself.
    normalized_path = _normalized(relative_path)
    if normalized_path == "relkit.toml":
        return ""
    for provider, surfaces in providers.items():
        if _contains_declared_name(text, (provider,)) and not any(
            fnmatch(normalized_path, _normalized(surface)) for surface in surfaces
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
    allowed = DEFAULT_ALLOWED_USERS | {_normalized(user).casefold() for user in allowed_users}
    found: dict[str, str] = {}
    searchable = _normalized(text)
    path_text = searchable.replace("\\/", "/")
    for pattern in (
        WINDOWS_USER_ROOT,
        UNC_USER_ROOT,
        WINDOWS_COMPAT_USER_ROOT,
        POSIX_USER_ROOT,
        FILE_URI_USER_ROOT,
    ):
        if any(
            not _is_placeholder(match.group(1), allowed) for match in pattern.finditer(path_text)
        ):
            found[HOME_DIRECTORY] = ""
            break
    if ROOT_HOME.search(path_text) or FILE_URI_ROOT_HOME.search(path_text):
        found[HOME_DIRECTORY] = ""

    checks_relative_paths = (
        posixpath.splitext(relative_path)[1].lower() not in PROSE_SUFFIXES
        and relative_path != "relkit.toml"
    )
    if checks_relative_paths:
        directory = posixpath.dirname(relative_path)
        if any(
            _escapes(match.group(1), "") for match in VARIABLE_ANCHORED_PATH.finditer(searchable)
        ) or any(
            _escapes(match.group(1), directory) for match in FILE_RELATIVE_PATH.finditer(searchable)
        ):
            found[ESCAPES_REPOSITORY] = ""

    if _contains_declared_name(searchable, names):
        found[DECLARED_NAME] = ""
    if _contains_declared_name(searchable, owner_workflows):
        found[OWNER_WORKFLOW] = "declared by the private owner policy"
    for pattern in private_patterns:
        if re.search(pattern.expression, searchable):
            found.setdefault(pattern.kind, "matched a private owner rule")
    if forbid_internal_planning and any(
        pattern.search(searchable) for pattern in INTERNAL_PLANNING_PATTERNS
    ):
        found[INTERNAL_PLANNING] = "internal card or knowledge-base reference"
    if forbid_ai_attribution and any(
        pattern.search(searchable) for pattern in AI_ATTRIBUTION_PATTERNS
    ):
        found[AI_ATTRIBUTION] = "machine authorship or review attribution"
    if forbid_machine_observations and any(
        pattern.search(searchable) for pattern in MACHINE_OBSERVATION_PATTERNS
    ):
        found[MACHINE_OBSERVATION] = "observation about the owner's workstation"

    if detail := provider_surface_finding(
        searchable, relative_path=relative_path, providers=providers or {}
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
    normalized = _normalized(PurePosixPath(relative).as_posix())
    normalized_folded = normalized.casefold()
    suffix = PurePosixPath(normalized).suffix.lower()
    kinds: set[str] = set()
    folded = _normalized(relative).casefold()
    if any(_normalized(name).casefold() in folded for name in names):
        kinds.add(DECLARED_NAME)
    if any(_normalized(name).casefold() in folded for name in owner_workflows):
        kinds.add(OWNER_WORKFLOW)
    for provider, surfaces in (providers or {}).items():
        if _normalized(provider).casefold() in folded and not any(
            fnmatch(normalized, _normalized(item)) for item in surfaces
        ):
            kinds.add(PROVIDER_SURFACE)
    suffixes = DEFAULT_FORBIDDEN_SUFFIXES | {item.lower() for item in forbidden_suffixes}
    if suffix in suffixes:
        kinds.add(FORBIDDEN_KIND)
    private_roots = tuple(
        _normalized(PurePosixPath(item.rstrip("/")).as_posix()).casefold()
        for item in private_paths
        if item
    )
    configured_files = {
        _normalized(PurePosixPath(item).as_posix()).casefold() for item in private_files if item
    }
    private_suffixes_folded = tuple(_normalized(item).casefold() for item in private_suffixes)
    basename = PurePosixPath(normalized).name.casefold()
    if (
        basename in DEFAULT_PRIVATE_FILES
        or normalized_folded in configured_files
        or any(
            normalized_folded == root or normalized_folded.startswith(f"{root}/")
            for root in private_roots
        )
        or any(normalized_folded.endswith(item) for item in private_suffixes_folded)
    ):
        kinds.add(PRIVATE_PATH)
    return kinds
