"""Read the entry a changelog already carries for a version.

The release notes a project publishes are the entry a person wrote and committed, not
a fresh render of the commit log. Re-rendering at publish time throws away the editing
pass that makes a changelog readable, and it lets a release describe a version the
repository never wrote down. So this reads; it does not generate. Generation is the
changelog tool's job, and it happens before the commit, not during the release.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from urllib.parse import unquote, urlsplit

from .. import semver

PROFILES = frozenset({"legacy", "strict", "conventional-changelog"})
_HEADING = re.compile(r"^ {0,3}##[ \t]+(?:\[([^\]]+)\]|([^\s]+))")
_CONVENTIONAL_HEADING = re.compile(
    r"^## \[([^\]]+)\](?:\(([^\s)]+)\))? \(([0-9]{4}-[0-9]{2}-[0-9]{2})\)[ \t]*$"
)
_SECTIONS = {
    "Highlights": "highlights",
    "Features": "features",
    "Bug Fixes": "fixes",
    "Performance Improvements": "performance",
    "Reverts": "reverts",
    "BREAKING CHANGES": "breaking",
    "Breaking Changes": "breaking",
}
SECTION_KINDS = frozenset(_SECTIONS.values())
EDITORIAL_KINDS = frozenset({"highlights", "breaking"})
_COMMIT_LINK = re.compile(r"(?<![!\\])\[([0-9a-fA-F]{7,64})\]\((https?://[^\s)]+)\)")


class ChangelogError(ValueError):
    """An invalid selected entry, located in the original changelog (one-based)."""

    def __init__(self, line: int, message: str) -> None:
        self.line = line
        super().__init__(message)


def normalize(version: str) -> str:
    """A tag and a changelog heading differ by a leading v and nothing else."""
    return version[1:] if version[:1].lower() == "v" and version[1:2].isdigit() else version


def is_version(version: str) -> bool:
    try:
        semver.parse(normalize(version))
    except ValueError:
        return False
    return True


def validate_section_aliases(aliases: Mapping[str, str]) -> None:
    if not isinstance(aliases, Mapping):
        raise TypeError("changelog.section_aliases must be a table")
    for title, kind in aliases.items():
        if (
            not isinstance(title, str)
            or not title
            or title.strip() != title
            or len(title.splitlines()) != 1
        ):
            raise ValueError(
                "changelog.section_aliases headings must be nonempty single-line names"
            )
        if not isinstance(kind, str) or kind not in SECTION_KINDS:
            raise ValueError(
                "changelog.section_aliases values must be one of: "
                + ", ".join(sorted(SECTION_KINDS))
            )
        if title in _SECTIONS and _SECTIONS[title] != kind:
            raise ValueError(f"changelog.section_aliases cannot redefine the meaning of {title!r}")


def section_kind(title: str, section_aliases: Mapping[str, str] | None = None) -> str | None:
    """Resolve a display heading without changing its original text."""
    return _SECTIONS.get(title) or (section_aliases or {}).get(title)


def _visible_lines(text: str) -> list[str]:
    """Mask comments and code for the supported changelog layout, retaining lines.

    This is a format policy, not a general Markdown renderer or link resolver.
    The original text is always used for export, never this inspection view.
    """

    tokens = re.compile(
        r"^ {0,3}(?P<fence>`{3,}|~{3,})[^\r\n]*"
        r"|^(?: {4}|\t)[^\r\n]*|<!--|(?P<ticks>(?<!`)`+(?!`))",
        re.MULTILINE,
    )
    result: list[str] = []
    position = 0
    while match := tokens.search(text, position):
        result.append(text[position : match.start()])
        end = match.end()
        if fence := match["fence"]:
            closing = re.compile(
                rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*\r?$",
                re.MULTILINE,
            ).search(text, end)
            end = closing.end() if closing else len(text)
        elif match[0] == "<!--":
            closing_position = text.find("-->", end)
            end = closing_position + 3 if closing_position >= 0 else len(text)
        elif ticks := match["ticks"]:
            # Code spans can wrap within a paragraph, but cannot consume another
            # section or fenced block. Process tokens in order so a comment marker
            # inside code (or backticks inside a comment) cannot hide later entries.
            boundary = re.compile(
                r"\r?\n[ \t]*\r?\n|^ {0,3}(?:#{1,6}[ \t]|`{3,}|~{3,})", re.MULTILINE
            ).search(text, end)
            closing = re.compile(rf"(?<!`){re.escape(ticks)}(?!`)").search(
                text, end, boundary.start() if boundary else len(text)
            )
            if not closing:
                result.append(match[0])
                position = end
                continue
            end = closing.end()
        result.append(re.sub(r"[^\r\n]", " ", text[match.start() : end]))
        position = end
    result.append(text[position:])
    return "".join(result).splitlines()


def _has_content(lines: list[str]) -> bool:
    return any(
        line.strip()
        and not re.match(r"^\s*(?:#{1,6}(?:\s|$)|[-*+]\s*$|[-*_]{3,}\s*$|\[[^]]+\]:)", line)
        for line in lines
    )


def _url_path(url: str) -> str:
    try:
        parts = urlsplit(url)
        if (
            parts.scheme in {"http", "https"}
            and parts.hostname
            and not parts.query
            and not parts.fragment
        ):
            return unquote(parts.path)
    except ValueError:
        pass
    return ""


def _validate_heading(heading: str, version: str, first_version: str, line: int) -> None:
    match = _CONVENTIONAL_HEADING.fullmatch(heading)
    if not match or not is_version(match[1]):
        raise ChangelogError(line, "expected ## [SemVer](compare URL) (YYYY-MM-DD)")
    try:
        date.fromisoformat(match[3])
    except ValueError:
        raise ChangelogError(line, "release date must be a valid YYYY-MM-DD date") from None
    path = _url_path(match[2] or "")
    comparison = re.search(r"/compare/([^/]+)\.\.\.([^/]+)$", path)
    if comparison:
        previous, target = comparison.groups()
        if is_version(previous) and normalize(previous) != version and normalize(target) == version:
            return
        raise ChangelogError(
            line, "compare link must join a different previous version to this version"
        )
    if (
        is_version(first_version)
        and semver.parse(normalize(first_version)).core == semver.parse(version).core
    ):
        tag = re.search(r"/releases/tag/([^/]+)$", path)
        if not match[2] or (tag and normalize(tag[1]) == version):
            return
        raise ChangelogError(line, "first release link must name this version's releases/tag URL")
    raise ChangelogError(
        line, "a compare link is required unless this is the declared first_version"
    )


def _has_commit_link(text: str) -> bool:
    for match in _COMMIT_LINK.finditer(text):
        commit = re.search(r"/commit/([0-9a-fA-F]{7,64})$", _url_path(match[2]))
        if commit and commit[1].lower().startswith(match[1].lower()):
            return True
    return False


def _validate_conventional(
    lines: list[str],
    start: int,
    end: int,
    version: str,
    first_version: str,
    section_aliases: Mapping[str, str] | None,
) -> None:
    _validate_heading(lines[start], version, first_version, start + 1)
    section = ""
    kind = None
    section_line = start + 1
    section_content: list[str] = []
    bullet_line = 0
    bullet: list[str] = []
    nested_detail = False

    def finish_bullet() -> None:
        if bullet_line and not _has_commit_link(bullet[0]):
            raise ChangelogError(
                bullet_line,
                "ordinary change needs a commit link with a matching hash label "
                "on its top-level bullet line",
            )

    def finish_section() -> None:
        finish_bullet()
        if section and not _has_content(section_content):
            raise ChangelogError(section_line, f"empty section: {section}")
        if section and kind not in EDITORIAL_KINDS and not bullet_line:
            raise ChangelogError(section_line, "ordinary sections require top-level change bullets")

    for index in range(start + 1, end):
        line = lines[index]
        if not line.strip():
            continue
        heading = re.fullmatch(r"### (.+?)\s*", line)
        if heading:
            finish_section()
            section = heading[1]
            kind = section_kind(section, section_aliases)
            section_line = index + 1
            section_content = []
            bullet_line = 0
            bullet = []
            nested_detail = False
            if kind is None:
                raise ChangelogError(
                    index + 1, f"unsupported conventional-changelog section: {section}"
                )
            continue
        if not section:
            raise ChangelogError(index + 1, "release content must be under a supported ### section")
        section_content.append(line)
        if kind in EDITORIAL_KINDS:
            continue
        if re.match(r"^[-*+]\s+", line):
            finish_bullet()
            bullet_line = index + 1
            bullet = [line]
            nested_detail = False
        elif re.match(r"^\s+[-*+]\s+", line):
            # A nested detail is not provenance for its parent change.
            nested_detail = True
        elif nested_detail:
            continue
        elif bullet_line and not re.match(r"^\s*#", line):
            bullet.append(line)
        else:
            raise ChangelogError(index + 1, "ordinary sections require top-level change bullets")
    finish_section()
    if not section:
        raise ChangelogError(start + 1, "release needs a supported ### section")


def entry_for(
    changelog: str,
    version: str,
    *,
    profile: str = "legacy",
    first_version: str = "",
    section_aliases: Mapping[str, str] | None = None,
) -> str | None:
    """Extract once and validate that exact entry; never regenerate it.

    Legacy preserves the original permissive API. Strict rejects duplicate and empty
    entries. The conventional-changelog profile additionally enforces the documented, opt-in layout. Errors
    carry source lines; a version absent from the document still returns None.
    """
    if profile not in PROFILES:
        raise ValueError(f"unknown changelog profile: {profile}")
    if section_aliases is not None:
        validate_section_aliases(section_aliases)
    if profile != "legacy":
        version = normalize(version)
        raw = changelog.splitlines(keepends=True)
        lines = _visible_lines(changelog)
        headings = [(i, match) for i, line in enumerate(lines) if (match := _HEADING.match(line))]
        matches = [i for i, match in headings if normalize(match[1] or match[2]) == version]
        if not matches:
            return None
        if len(matches) > 1:
            raise ChangelogError(matches[1] + 1, f"duplicate entry for {version}")
        start = matches[0]
        end = next((i for i, _ in headings if i > start), len(lines))
        if not _has_content(lines[start + 1 : end]):
            raise ChangelogError(start + 1, f"empty entry for {version}")
        if profile == "conventional-changelog":
            _validate_conventional(lines, start, end, version, first_version, section_aliases)
        return "".join(raw[start:end]).rstrip("\r\n")
    heading = re.escape(normalize(version))
    # Keep-a-Changelog brackets the version whether or not it links anywhere, so the
    # bracket is the stable part of the heading across linked and unlinked entries.
    pattern = re.compile(rf"^## \[{heading}\].*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(changelog)
    if match is None:
        return None
    entry = match.group(0).strip()
    return entry or None
