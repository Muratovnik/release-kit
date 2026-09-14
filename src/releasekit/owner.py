"""Private owner policy discovered without publishing its location or contents."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import storage
from .exposure import rules

PRIVATE_ROOT_ENV = "RELKIT_PRIVATE_ROOT"
PRIVATE_ROOT_CONFIG = "releasekit.privateRoot"
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


def _configured_private_root(public_root: Path) -> str | None:
    """Read one value from this checkout's private, non-included Git config."""
    if not os.path.lexists(public_root / ".git"):
        return None
    environment = dict(os.environ)
    for name in storage.GIT_LOCATION_OVERRIDES:
        environment.pop(name, None)
    try:
        result = subprocess.run(
            [
                "git",
                "config",
                "--local",
                "--no-includes",
                "--null",
                "--get-all",
                PRIVATE_ROOT_CONFIG,
            ],
            cwd=public_root,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="strict",
            env=environment,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        raise OwnerPolicyError(
            "repository-local owner policy configuration could not be read"
        ) from error
    if result.returncode == 1:
        return None
    if result.returncode:
        raise OwnerPolicyError("repository-local owner policy configuration could not be read")
    if not result.stdout.endswith("\0"):
        raise OwnerPolicyError(
            "repository-local owner policy configuration returned malformed data"
        )
    values = result.stdout[:-1].split("\0")
    if len(values) != 1 or not values[0].strip():
        raise OwnerPolicyError(
            f"repository-local {PRIVATE_ROOT_CONFIG} must have exactly one non-empty value"
        )
    value = values[0].strip()
    if "\n" in value or "\r" in value:
        raise OwnerPolicyError(f"repository-local {PRIVATE_ROOT_CONFIG} must be a single-line path")
    return value


def discover(public_root: Path) -> OwnerPolicy:
    """Resolve an explicit, repository-local, or conventional owner-policy root."""
    override = os.environ.get(PRIVATE_ROOT_ENV, "").strip()
    if override:
        candidate = Path(override).expanduser()
        private_root = (
            candidate if candidate.is_absolute() else public_root.parent / candidate
        ).resolve()
        selected_by = PRIVATE_ROOT_ENV
    elif configured := _configured_private_root(public_root):
        candidate = Path(configured).expanduser()
        private_root = (candidate if candidate.is_absolute() else public_root / candidate).resolve()
        selected_by = f"repository-local {PRIVATE_ROOT_CONFIG}"
    else:
        private_root = (public_root.parent / f"{public_root.name}-private").resolve()
        selected_by = "the sibling convention"
    if not private_root.is_dir():
        raise OwnerPolicyError(
            f"private owner root selected by {selected_by} is unavailable: {private_root}"
        )
    return OwnerPolicy(
        root=private_root,
        values_path=private_root / PRIVATE_VALUES_FILE,
        manifest_path=private_root / MANIFEST_FILE,
        semantic_path=private_root / SEMANTIC_POLICY_FILE,
    )
