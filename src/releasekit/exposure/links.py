"""Documentation links that will not survive publication.

A link is checked for where it lands, not for how it is spelled. Three ways it fails
a reader who is not the author: it points at an absolute location that exists on one
machine, it climbs out of the repository into something that was never published, or
it names a file that is not there. All three read as working links to whoever wrote
them, because on that machine they are.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

LINK_ESCAPES = "link-escapes-repository"
BROKEN_LINK = "broken-link"

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})


def findings(text: str, relative_path: str, root: Path) -> list[tuple[str, str]]:
    """(kind, detail) for each link that would not resolve in a fresh clone."""
    found: list[tuple[str, str]] = []
    source = (root / relative_path).parent
    resolved_root = root.resolve()
    for match in MARKDOWN_LINK.finditer(text):
        target = match.group(1).strip().strip("<>")
        if not target or target.startswith("#"):
            continue
        # A Windows drive letter is tested before the scheme, because `C:/x` satisfies
        # the syntax of a URL scheme and would otherwise be waved through as one.
        if WINDOWS_ABSOLUTE.match(target):
            found.append((LINK_ESCAPES, f"{relative_path} -> {target} (absolute)"))
            continue
        if SCHEME.match(target):
            continue
        # Markdown allows a title after the target; the path is the first field, and
        # percent-escapes have to come off before the filesystem sees it.
        target = unquote(target.split(maxsplit=1)[0]).split("#", maxsplit=1)[0]
        if not target:
            continue
        if target.startswith("/"):
            found.append((LINK_ESCAPES, f"{relative_path} -> {target} (absolute)"))
            continue
        destination = (source / target).resolve()
        try:
            destination.relative_to(resolved_root)
        except ValueError:
            found.append((LINK_ESCAPES, f"{relative_path} -> {target}"))
            continue
        if not destination.exists():
            found.append((BROKEN_LINK, f"{relative_path} -> {target}"))
    return found
