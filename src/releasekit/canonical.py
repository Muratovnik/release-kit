"""One canonical JSON encoding for every fingerprint that crosses a process boundary."""

from __future__ import annotations

import hashlib
import json


def dumps(value: object) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace, ASCII only."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint(value: object) -> str:
    """The reviewed-plan digest. CLI and adapter must agree byte for byte."""
    return hashlib.sha256(dumps(value).encode()).hexdigest()
