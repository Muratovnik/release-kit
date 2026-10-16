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
import threading
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import PurePosixPath
from typing import NamedTuple

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
    # ASCII is already in NFKC form and holds no format characters: the Cf category
    # starts at U+00AD, and ASCII controls are Cc. Most scanned bytes are ASCII, and
    # the general path below walks every character, so this decides the scan's cost.
    if text.isascii():
        return text
    normalized = unicodedata.normalize("NFKC", text)
    # Asking once per distinct character, not once per character: a long non-ASCII
    # file repeats the same few hundred characters, and history scans thousands.
    if not any(unicodedata.category(character) == "Cf" for character in set(normalized)):
        return normalized
    return "".join(character for character in normalized if unicodedata.category(character) != "Cf")


class _Prepared:
    """One file's text in the forms the rules compare against.

    Building these costs a pass over the whole file, and every declared name, owner
    workflow and provider used to trigger another one. They do not depend on what is
    being searched for, so they are built once per text and handed around. The two
    forms most texts never need are built on first use.
    """

    __slots__ = ("_collapsed", "_scan", "folded", "normalized")

    def __init__(self, normalized: str, folded: str) -> None:
        self.normalized = normalized
        self.folded = folded
        self._collapsed: str | None = None
        self._scan: str | None = None

    @property
    def collapsed(self) -> str:
        """The folded text with every whitespace run reduced to one space."""
        if self._collapsed is None:
            self._collapsed = WHITESPACE.sub(" ", self.folded)
        return self._collapsed

    @property
    def scan(self) -> str:
        """The text a literal prefilter looks in; see `_Gate`."""
        if self._scan is None:
            self._scan = _gate_text(self.folded)
        return self._scan


# Spelled by code point: the formatter rewrites an escape into the bare character, and
# a combining mark in source is invisible to a reader.
DOTLESS_I = chr(0x131)
LOWERCASE_I_WITH_DOT = "i" + chr(0x307)  # what casefolding makes of U+0130


def _gate_text(folded: str) -> str:
    """Fold the two letters whose case-insensitive match casefolding misses.

    An ignore-case expression treats the dotless U+0131 and the dotted capital U+0130
    as an `i`, but casefolding leaves the first alone and turns the second into an `i`
    plus a combining dot. A literal containing `i` would then be absent from the folded
    text while the expression still matches it. Every other ASCII letter folds to
    itself from each character an ignore-case expression equates with it; the test
    suite sweeps all of Unicode to keep that true on each supported Python.
    """
    return folded.replace(DOTLESS_I, "i").replace(LOWERCASE_I_WITH_DOT, "i")


class _Gate(NamedTuple):
    """Literals every match of an expression must contain, checked before it runs.

    Each group is a set of alternatives of which at least one must occur in the text,
    and every group must be satisfied. A gate only rules out; it names what a match
    cannot do without, so an expression it rejects would have found nothing and one it
    admits still decides for itself. Needles are lowercase and are looked for in
    `_Prepared.scan`, a plain substring search several times faster than running the
    expression itself over a large text. Expressions that never match a given text
    are the common case, which is why the gate pays for itself.
    """

    groups: tuple[tuple[str, ...], ...]

    def admits(self, haystack: str) -> bool:
        return all(any(needle in haystack for needle in group) for group in self.groups)


# The same text is prepared for several rules in a row - wrapped values, workflows,
# then every structural rule - and each preparation is a full pass. The forms depend on
# the text alone, so the latest few are kept. They are kept per thread: a history scan
# judges files on one thread while another reads commit messages, and one shared cache
# lets the small texts evict the large file in the middle of its own rules.
_RECENT_TEXTS = 4
_recent = threading.local()


def _prepare(text: str) -> _Prepared:
    cache: dict[str, _Prepared] | None = getattr(_recent, "cache", None)
    if cache is None:
        cache = _recent.cache = {}
    prepared = cache.get(text)
    if prepared is None:
        normalized = _normalized(text)
        prepared = _Prepared(normalized, normalized.casefold())
        if len(cache) >= _RECENT_TEXTS:
            del cache[next(iter(cache))]
        cache[text] = prepared
    return prepared


@lru_cache(maxsize=4096)
def _normalized_value(value: str) -> str:
    """Policy values and paths are short and repeat for every file; normalize once."""
    return _normalized(value)


@lru_cache(maxsize=4096)
def _folded_value(value: str) -> str:
    return _normalized_value(value).casefold()


@lru_cache(maxsize=4096)
def _collapsed_value(value: str) -> str:
    return WHITESPACE.sub(" ", _folded_value(value))


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
# Git records a commit's author and committer outside its message, so the trailer
# patterns never see an agent that was set as either one.
AI_IDENTITY = re.compile(rf"(?i)\b{AI_VENDOR}\b")


def is_ai_identity(identity: str) -> bool:
    """Whether a Git ``Name <email>`` identity names an AI tool or coding agent."""
    return AI_IDENTITY.search(_normalized(identity)) is not None


MACHINE_OBSERVATION_PATTERNS = (
    re.compile(r"(?i)\bon\s+(?:this|my|our)\s+workstation\b"),
    re.compile(
        r"(?i)\b(?:the|this|my|our)\s+(?:real\s+)?(?:installation|workstation)\s+"
        r"(?:has|had|contains?|uses?|reports?|shows?|includes?)\b"
    ),
)


# Prefilters for the expressions above, each naming what a match cannot do without
# (see `_Gate`). A history scan runs every expression over every historical file, and
# almost none of them match, so the time goes to expressions that find nothing.
#
# The home-directory expressions run on a text whose escaped slashes are plain, so
# their needles are looked for in that form. Each separator is part of a needle because
# the expressions require one next to the directory name.
_WINDOWS_PROFILE_DIRECTORIES = (
    "users/",
    "users\\",
    "documents and settings/",
    "documents and settings\\",
)
_POSIX_HOME_DIRECTORIES = ("/users/", "/home/")
USER_ROOT_GATES: tuple[tuple[re.Pattern[str], _Gate], ...] = (
    (WINDOWS_USER_ROOT, _Gate((_WINDOWS_PROFILE_DIRECTORIES,))),
    (UNC_USER_ROOT, _Gate((_WINDOWS_PROFILE_DIRECTORIES,))),
    (WINDOWS_COMPAT_USER_ROOT, _Gate((_WINDOWS_PROFILE_DIRECTORIES,))),
    (POSIX_USER_ROOT, _Gate((_POSIX_HOME_DIRECTORIES,))),
    (FILE_URI_USER_ROOT, _Gate((("file:",), _POSIX_HOME_DIRECTORIES))),
)
# Joined from two pieces because a quoted `/root` in this source would itself read as
# a root home directory to the audit that guards the packaged copy of this file.
_ROOT_DIRECTORY = "/" + "root"
ROOT_HOME_GATES: tuple[tuple[re.Pattern[str], _Gate], ...] = (
    (ROOT_HOME, _Gate(((_ROOT_DIRECTORY,),))),
    (FILE_URI_ROOT_HOME, _Gate((("file:",), (_ROOT_DIRECTORY,)))),
)
# Both expressions read a relative path that climbs out, and neither can without `../`.
# They run on the text as written, which no folding changes for a needle of punctuation.
CLIMBING_PATH_NEEDLE = "../"
# One needle per alternative of AI_VENDOR; the `agent` of "ai agent" and "coding agent"
# is the only part of those two names that every spelling shares.
AI_VENDOR_NEEDLES = (
    "claude",
    "codex",
    "chatgpt",
    "copilot",
    "gemini",
    "cursor",
    "windsurf",
    "devin",
    "agent",
)
AI_ATTRIBUTION_GATES: tuple[_Gate, ...] = (
    _Gate(
        (
            ("co-authored-by:", "generated-by:", "reviewed-by:", "tested-by:"),
            AI_VENDOR_NEEDLES,
        )
    ),
    _Gate(
        (
            ("generated", "written", "reviewed", "tested", "created", "assisted"),
            AI_VENDOR_NEEDLES,
        )
    ),
)
INTERNAL_PLANNING_GATES: tuple[_Gate, ...] = (
    _Gate((("card",), (":",))),
    _Gate((("card",), ("#",))),
    _Gate((("<!--",), ("kb:",))),
)
MACHINE_OBSERVATION_GATES: tuple[_Gate, ...] = (
    _Gate((("workstation",),)),
    _Gate((("workstation", "installation"),)),
)


def _wrapped_declared_name(prepared: _Prepared, names: Sequence[str]) -> bool:
    # Wrapping only reflows whitespace, so a value hidden by it still has every one of
    # its words in the text; the collapsed copy is built only for a text that has them.
    return any(
        _folded_value(name) not in prepared.folded
        and all(word in prepared.folded for word in _folded_value(name).split())
        and _collapsed_value(name) in prepared.collapsed
        for name in names
        if len(name.split()) > 1
    )


def contains_wrapped_declared_name(text: str, names: Sequence[str]) -> bool:
    """Whether whitespace wrapping hides a declared value from raw matching."""
    return _wrapped_declared_name(_prepare(text), names)


def _contains_declared_name(text: str, names: Sequence[str]) -> bool:
    """Match owner values even when prose wrapping changes the whitespace.

    Owner policy entries are deliberately one value per line. Markdown formatters
    may wrap a multi-word value across lines without changing what a reader sees,
    so a raw substring check alone lets the same private value evade the gate.
    Collapsing whitespace preserves word boundaries and does not turn separated
    tokens into a match.
    """
    return _declared_name(_prepare(text), names)


def _declared_name(prepared: _Prepared, names: Sequence[str]) -> bool:
    return any(_folded_value(name) in prepared.folded for name in names) or (
        _wrapped_declared_name(prepared, names)
    )


def _provider_surface(
    prepared: _Prepared,
    *,
    relative_path: str,
    providers: dict[str, Sequence[str]],
) -> str:
    # The public configuration is the declaration of the provider contract, not a
    # product surface that consumes provider data. Requiring adopters to allow the
    # configuration path would make every valid declaration reject itself.
    normalized_path = _normalized_value(relative_path)
    if normalized_path == "relkit.toml":
        return ""
    for provider, surfaces in providers.items():
        if _declared_name(prepared, (provider,)) and not any(
            fnmatch(normalized_path, _normalized_value(surface)) for surface in surfaces
        ):
            return f"provider {provider} is outside its declared surfaces"
    return ""


def provider_surface_finding(
    text: str,
    *,
    relative_path: str,
    providers: dict[str, Sequence[str]],
) -> str:
    return _provider_surface(_prepare(text), relative_path=relative_path, providers=providers)


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
    allowed = DEFAULT_ALLOWED_USERS | {_folded_value(user) for user in allowed_users}
    found: dict[str, str] = {}
    # One pass over the file, reused by every declared name, workflow and provider.
    prepared = _prepare(text)
    searchable = prepared.normalized
    path_text = searchable.replace("\\/", "/")
    scan = prepared.scan
    path_scan = scan.replace("\\/", "/")
    for pattern, gate in USER_ROOT_GATES:
        if gate.admits(path_scan) and any(
            not _is_placeholder(match.group(1), allowed) for match in pattern.finditer(path_text)
        ):
            found[HOME_DIRECTORY] = ""
            break
    if any(
        gate.admits(path_scan) and pattern.search(path_text) for pattern, gate in ROOT_HOME_GATES
    ):
        found[HOME_DIRECTORY] = ""

    checks_relative_paths = (
        posixpath.splitext(relative_path)[1].lower() not in PROSE_SUFFIXES
        and relative_path != "relkit.toml"
    )
    if checks_relative_paths and CLIMBING_PATH_NEEDLE in searchable:
        directory = posixpath.dirname(relative_path)
        if any(
            _escapes(match.group(1), "") for match in VARIABLE_ANCHORED_PATH.finditer(searchable)
        ) or any(
            _escapes(match.group(1), directory) for match in FILE_RELATIVE_PATH.finditer(searchable)
        ):
            found[ESCAPES_REPOSITORY] = ""

    if _declared_name(prepared, names):
        found[DECLARED_NAME] = ""
    if _declared_name(prepared, owner_workflows):
        found[OWNER_WORKFLOW] = "declared by the private owner policy"
    for pattern in private_patterns:
        if re.search(pattern.expression, searchable):
            found.setdefault(pattern.kind, "matched a private owner rule")
    if forbid_internal_planning and any(
        gate.admits(scan) and pattern.search(searchable)
        for pattern, gate in zip(INTERNAL_PLANNING_PATTERNS, INTERNAL_PLANNING_GATES, strict=True)
    ):
        found[INTERNAL_PLANNING] = "internal card or knowledge-base reference"
    if forbid_ai_attribution and any(
        gate.admits(scan) and pattern.search(searchable)
        for pattern, gate in zip(AI_ATTRIBUTION_PATTERNS, AI_ATTRIBUTION_GATES, strict=True)
    ):
        found[AI_ATTRIBUTION] = "machine authorship or review attribution"
    if forbid_machine_observations and any(
        gate.admits(scan) and pattern.search(searchable)
        for pattern, gate in zip(
            MACHINE_OBSERVATION_PATTERNS, MACHINE_OBSERVATION_GATES, strict=True
        )
    ):
        found[MACHINE_OBSERVATION] = "observation about the owner's workstation"

    if detail := _provider_surface(
        prepared, relative_path=relative_path, providers=providers or {}
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


def is_owner_policy_path(relative: str) -> bool:
    """The two private policy filenames are unconditional publication exclusions."""
    return PurePosixPath(_normalized_value(relative)).name.casefold() in DEFAULT_PRIVATE_FILES


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
