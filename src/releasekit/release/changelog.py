"""Read the entry a changelog already carries for a version.

The release notes a project publishes are the entry a person wrote and committed, not
a fresh render of the commit log. Re-rendering at publish time throws away the editing
pass that makes a changelog readable, and it lets a release describe a version the
repository never wrote down. So this reads; it does not generate. Generation is the
changelog tool's job, and it happens before the commit, not during the release.
"""

from __future__ import annotations

import re


def normalize(version: str) -> str:
    """A tag and a changelog heading differ by a leading v and nothing else."""
    return version[1:] if version[:1].lower() == "v" and version[1:2].isdigit() else version


def entry_for(changelog: str, version: str) -> str | None:
    """The section for one version, up to the next heading, or None when absent."""
    heading = re.escape(normalize(version))
    # Keep-a-Changelog brackets the version whether or not it links anywhere, so the
    # bracket is the stable part of the heading across linked and unlinked entries.
    pattern = re.compile(rf"^## \[{heading}\].*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(changelog)
    if match is None:
        return None
    entry = match.group(0).strip()
    return entry or None
