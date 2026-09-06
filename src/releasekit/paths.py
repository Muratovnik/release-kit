"""Path facts shared by policy, the release contract and the adapter.

A leaf module on purpose: the configuration loader, the coordinator settings and the
MCP bridge all decide the same question, and a second copy of this set is how the
three answers drift apart.
"""

from __future__ import annotations

WINDOWS_RESERVED_STEMS = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


def reserved_stem(part: str) -> bool:
    """Whether one path segment names a Windows device, with or without a suffix."""
    return part.split(".", maxsplit=1)[0].casefold() in WINDOWS_RESERVED_STEMS
