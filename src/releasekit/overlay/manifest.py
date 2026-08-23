"""Read the mount list out of a dotbot manifest.

The manifest is the single place that says which surfaces are linked in. Reading it
rather than restating the list is the whole point: a second copy of that list is a
second thing to forget, and the copies disagree silently.

The parser is deliberately narrow - the top-level `- link:` block and its `target:
source` lines, nothing else. It refuses a form it does not understand instead of
skipping it, because a mount that is quietly not parsed is a mount that is quietly
not verified, which is worse than no verification at all.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path


class ManifestError(Exception):
    """The manifest is missing, empty of links, or written in a form not supported."""


@dataclass(frozen=True)
class Mount:
    # Where the link lives, as written in the manifest, relative to the private root.
    link: str
    # What it points at, relative to the private root.
    target: str

    def link_path(self, private_root: Path) -> Path:
        return Path(posixpath.normpath(str(private_root / self.link).replace("\\", "/")))

    def target_path(self, private_root: Path) -> Path:
        return private_root / self.target


def read(path: Path) -> tuple[Mount, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ManifestError(f"the manifest could not be read: {error}") from error

    mounts: list[Mount] = []
    in_link = False
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not raw[0].isspace():
            # A new top-level directive. Only `- link:` carries mounts; `- defaults:`
            # and the rest are configuration this reader has no business in.
            in_link = stripped == "- link:"
            continue
        if not in_link:
            continue
        if ":" not in stripped:
            raise ManifestError(f"{path.name} line {number}: cannot read '{stripped}'")
        link, _, target = stripped.partition(":")
        link, target = link.strip(), target.strip()
        if not target:
            raise ManifestError(
                f"{path.name} line {number}: '{link}' uses dotbot's extended form, "
                "which this reader does not support; it would be skipped and therefore "
                "never verified"
            )
        mounts.append(Mount(link=link, target=target))
    if not mounts:
        raise ManifestError(f"{path.name} declares no links")
    return tuple(mounts)
