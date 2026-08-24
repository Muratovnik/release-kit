"""Private owner policy discovered without publishing its location or contents."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .exposure import rules

PRIVATE_ROOT_ENV = "RELKIT_PRIVATE_ROOT"
PRIVATE_VALUES_FILE = ".publication-private-values"
MANIFEST_FILE = "install.conf.yaml"
SEMANTIC_POLICY_FILE = ".publication-owner.toml"


class OwnerPolicyError(Exception):
    """The private policy required for an owner-side verdict is unavailable."""


@dataclass(frozen=True)
class OwnerRules:
    private_values: tuple[str, ...] = ()
    owner_workflows: tuple[str, ...] = ()
    patterns: tuple[rules.PrivatePattern, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.private_values or self.owner_workflows or self.patterns)


@dataclass(frozen=True)
class OwnerPolicy:
    root: Path
    values_path: Path
    manifest_path: Path
    semantic_path: Path

    def _values(self, *, require_nonempty: bool) -> tuple[str, ...]:
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
        if require_nonempty and not values:
            raise OwnerPolicyError(f"owner private-value policy is empty: {self.values_path}")
        return values

    def values(self) -> tuple[str, ...]:
        return self._values(require_nonempty=True)

    def load(self) -> OwnerRules:
        values: tuple[str, ...] = ()
        if self.values_path.is_file():
            values = self._values(require_nonempty=False)

        workflows: tuple[str, ...] = ()
        patterns: tuple[rules.PrivatePattern, ...] = ()
        if self.semantic_path.is_file():
            try:
                raw = tomllib.loads(self.semantic_path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as error:
                raise OwnerPolicyError(
                    f"owner semantic policy could not be read: {self.semantic_path} ({error})"
                ) from error
            unknown = sorted(set(raw) - {"version", "owner_workflows", "patterns"})
            if unknown:
                raise OwnerPolicyError(
                    f"unknown owner semantic policy key(s): {', '.join(unknown)}"
                )
            if raw.get("version", 1) != 1:
                raise OwnerPolicyError("owner semantic policy version must be 1")
            workflow_value = raw.get("owner_workflows", [])
            if not isinstance(workflow_value, list) or not all(
                isinstance(item, str) and item.strip() and "\n" not in item and "\r" not in item
                for item in workflow_value
            ):
                raise OwnerPolicyError("owner_workflows must be a list of non-empty strings")
            workflows = tuple(dict.fromkeys(item.strip() for item in workflow_value))

            pattern_value = raw.get("patterns", [])
            if not isinstance(pattern_value, list):
                raise OwnerPolicyError("patterns must be an array of tables")
            parsed: list[rules.PrivatePattern] = []
            seen_names: set[str] = set()
            for item in pattern_value:
                if not isinstance(item, dict) or set(item) != {"name", "kind", "expression"}:
                    raise OwnerPolicyError(
                        "each owner pattern must contain name, kind, and expression"
                    )
                name = item["name"]
                kind = item["kind"]
                expression = item["expression"]
                if not all(isinstance(value, str) and value.strip() for value in item.values()):
                    raise OwnerPolicyError("owner pattern fields must be non-empty strings")
                if "\n" in name or "\r" in name or "\n" in kind or "\r" in kind:
                    raise OwnerPolicyError("owner pattern name and kind must be single-line")
                if name in seen_names:
                    raise OwnerPolicyError(f"duplicate owner pattern name: {name}")
                if kind not in rules.PRIVATE_PATTERN_KINDS:
                    choices = ", ".join(sorted(rules.PRIVATE_PATTERN_KINDS))
                    raise OwnerPolicyError(f"owner pattern {name} kind must be one of: {choices}")
                try:
                    re.compile(expression)
                except re.error as error:
                    raise OwnerPolicyError(f"owner pattern {name} is invalid: {error}") from error
                seen_names.add(name)
                parsed.append(rules.PrivatePattern(name=name, kind=kind, expression=expression))
            patterns = tuple(parsed)

        answer = OwnerRules(values, workflows, patterns)
        if answer.empty:
            raise OwnerPolicyError(
                "owner policy is empty; add private values or a semantic owner policy"
            )
        return answer


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
        semantic_path=private_root / SEMANTIC_POLICY_FILE,
    )
