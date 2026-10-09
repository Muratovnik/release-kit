"""Semantic Versioning 2.0.0 identity and precedence, without a runtime dependency.

Version identity retains build metadata; precedence deliberately does not. Callers
choose the comparison they need instead of treating a new build label as an upgrade.
Git tag prefixes and Python packaging version normalization belong at their own
boundaries, outside this parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @property
    def core(self) -> tuple[int, int, int]:
        return self.major, self.minor, self.patch

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def precedence_key(self) -> tuple[int, int, int, bool, tuple[tuple[int, int | str], ...]]:
        identifiers = tuple(
            (0, int(part)) if part.isdigit() else (1, part) for part in self.prerelease
        )
        return *self.core, not self.is_prerelease, identifiers

    def __str__(self) -> str:
        text = ".".join(map(str, self.core))
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += "+" + ".".join(self.build)
        return text


def parse(value: str) -> Version:
    """Parse an unprefixed SemVer; reject whitespace and non-ASCII identifiers."""
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"invalid SemVer version: {value!r}")
    prerelease = tuple(match[4].split(".")) if match[4] else ()
    if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease):
        raise ValueError(
            f"numeric SemVer prerelease identifiers cannot have leading zeroes: {value!r}"
        )
    return Version(
        *map(int, match.group(1, 2, 3)),
        prerelease=prerelease,
        build=tuple(match[5].split(".")) if match[5] else (),
    )
