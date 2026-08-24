"""Private owner policy discovered without publishing its location or contents."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PRIVATE_ROOT_ENV = "RELKIT_PRIVATE_ROOT"
PRIVATE_VALUES_FILE = ".publication-private-values"
MANIFEST_FILE = "install.conf.yaml"


class OwnerPolicyError(Exception):
    """The private policy required for an owner-side verdict is unavailable."""


@dataclass(frozen=True)
class OwnerPolicy:
    root: Path
    values_path: Path
    manifest_path: Path

    def values(self) -> tuple[str, ...]:
        try:
            lines = self.values_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise OwnerPolicyError(
                f"owner private-value policy is unavailable: {self.values_path} ({error})"
            ) from error
        values = tuple(
            stripped
            for line in lines
            if (stripped := line.strip()) and not stripped.startswith("#")
        )
        if not values:
            raise OwnerPolicyError(f"owner private-value policy is empty: {self.values_path}")
        return values


def discover(public_root: Path) -> OwnerPolicy:
    """Resolve the private sibling, with an environment override for unusual layouts."""
    override = os.environ.get(PRIVATE_ROOT_ENV, "").strip()
    if override:
        candidate = Path(override).expanduser()
        private_root = (
            candidate if candidate.is_absolute() else public_root.parent / candidate
        ).resolve()
    else:
        private_root = (public_root.parent / f"{public_root.name}-private").resolve()
    if not private_root.is_dir():
        raise OwnerPolicyError(
            f"private owner root is unavailable: {private_root}; "
            f"place it beside the public checkout or set {PRIVATE_ROOT_ENV}"
        )
    return OwnerPolicy(
        root=private_root,
        values_path=private_root / PRIVATE_VALUES_FILE,
        manifest_path=private_root / MANIFEST_FILE,
    )
