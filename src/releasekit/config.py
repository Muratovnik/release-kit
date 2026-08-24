"""Public repository policy, containing no private locations or forbidden names."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "relkit.toml"
EXPOSURE_KEYS = frozenset(
    {
        "baseline",
        "exclude",
        "private_paths",
        "private_files",
        "private_suffixes",
        "required_ignores",
        "forbidden_suffixes",
        "allowed_users",
        "check_secrets",
        "check_links",
        "betterleaks_config",
        "allowed_identities",
        "forbid_png_metadata",
        "include_candidates",
        "forbid_ai_attribution",
        "forbid_internal_planning",
        "forbid_machine_observations",
        "inspect_archives",
        "providers",
        "provenance_required",
        "provenance",
    }
)

PROVIDER_ROLES = frozenset({"product-data-provider"})
PROVENANCE_KINDS = frozenset({"synthetic", "anonymized", "machine-derived"})


class ConfigError(Exception):
    """The configuration is missing or malformed; the caller decides how loudly."""


def _string(section: dict[str, object], key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value


def _strings(section: dict[str, object], key: str) -> list[str]:
    value = section.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{key} must be a list of strings")
    return list(value)


def _boolean(section: dict[str, object], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false")
    return value


@dataclass(frozen=True)
class ProviderConfig:
    role: str
    allowed_surfaces: list[str]


def _providers(section: dict[str, object]) -> dict[str, ProviderConfig]:
    raw = section.get("providers", {})
    if not isinstance(raw, dict):
        raise ConfigError("[exposure.providers] must be a table")
    providers: dict[str, ProviderConfig] = {}
    for name, value in raw.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or "\n" in name
            or "\r" in name
            or not isinstance(value, dict)
        ):
            raise ConfigError("[exposure.providers] must map provider names to tables")
        unknown = sorted(set(value) - {"role", "allowed_surfaces"})
        if unknown:
            raise ConfigError(f"unknown [exposure.providers.{name}] key(s): {', '.join(unknown)}")
        role = _string(value, "role", "")
        if role not in PROVIDER_ROLES:
            choices = ", ".join(sorted(PROVIDER_ROLES))
            raise ConfigError(f"provider {name} role must be one of: {choices}")
        surfaces = _strings(value, "allowed_surfaces")
        if not surfaces or any(not surface.strip() for surface in surfaces):
            raise ConfigError(f"provider {name} allowed_surfaces must not be empty")
        providers[name] = ProviderConfig(role=role, allowed_surfaces=surfaces)
    return providers


def _provenance(section: dict[str, object]) -> dict[str, str]:
    raw = section.get("provenance", {})
    if not isinstance(raw, dict) or not all(
        isinstance(pattern, str) and isinstance(kind, str) for pattern, kind in raw.items()
    ):
        raise ConfigError("[exposure.provenance] must map path patterns to strings")
    for pattern, kind in raw.items():
        if not pattern.strip():
            raise ConfigError("[exposure.provenance] path patterns must not be empty")
        if kind not in PROVENANCE_KINDS:
            choices = ", ".join(sorted(PROVENANCE_KINDS))
            raise ConfigError(f"provenance for {pattern} must be one of: {choices}")
    return dict(raw)


@dataclass(frozen=True)
class ExposureConfig:
    baseline: dict[str, list[str]] = field(default_factory=dict)
    # Paths the rules do not apply to, as glob patterns. This is not the baseline and
    # must not be used as one: the baseline records debt that has to shrink, while an
    # exclusion says the rules were never meaningful there - a test fixture that has
    # to contain the very thing the rule detects. Anything broader hollows out the
    # gate while still reporting that it passed.
    exclude: list[str] = field(default_factory=list)
    # Surfaces that must never be tracked, whatever they contain. This is the blunt
    # instrument and the one that actually keeps a workflow out of a repository:
    # judging file by file cannot, because the next file added to the surface is
    # judged from scratch.
    private_paths: list[str] = field(default_factory=list)
    private_files: list[str] = field(default_factory=list)
    private_suffixes: list[str] = field(default_factory=list)
    # Paths the repository must actually ignore. A surface removed from the index but
    # left unignored comes back with the next `git add -A`.
    required_ignores: list[str] = field(default_factory=list)
    forbidden_suffixes: list[str] = field(default_factory=list)
    allowed_users: list[str] = field(default_factory=list)
    # The parsers for secrets and Markdown links belong to maintained specialist
    # tools. release-kit owns their pinned distribution and invocation, not another
    # implementation of either parser.
    check_secrets: bool = True
    check_links: bool = True
    betterleaks_config: str = ".betterleaks.toml"
    # Full-history publication checks also constrain Git identities. Entries use the
    # stable ``Name <email>`` spelling printed by Git.
    allowed_identities: list[str] = field(default_factory=list)
    # PNG fixtures are the one binary portability rule currently needed by adopters.
    # It is policy rather than secret/link parsing and is therefore kept here once.
    forbid_png_metadata: bool = False
    # A file that is neither tracked nor ignored is not safe, only uncommitted.
    include_candidates: bool = True
    # Semantic rules are opt-in because the adopting repository owns whether card
    # references, agent attribution, or workstation observations are product facts or
    # owner-only process. Once enabled, the same rule covers the tree, commit messages,
    # reachable history, and supported archives.
    forbid_ai_attribution: bool = False
    forbid_internal_planning: bool = False
    forbid_machine_observations: bool = False
    # ZIP-family publication artifacts are themselves a publication surface. They are
    # inspected by default; an adopter can still exclude a deliberate binary fixture.
    inspect_archives: bool = True
    # A named provider is public product vocabulary only on its declared surfaces.
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    # Test data and screenshots can be required to declare their origin. Synthetic and
    # explicitly anonymized material pass; machine-derived material is a finding.
    provenance_required: list[str] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    root: Path
    exposure: ExposureConfig = field(default_factory=ExposureConfig)


def load(root: Path, *, required: bool = True) -> Config:
    path = root / CONFIG_NAME
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise ConfigError(f"{CONFIG_NAME} is missing from {root}") from None
        raw = {}
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{CONFIG_NAME} could not be read: {error}") from error

    unknown_top_level = sorted(set(raw) - {"exposure"})
    if unknown_top_level:
        raise ConfigError(f"unknown top-level key(s): {', '.join(unknown_top_level)}")
    section = raw.get("exposure", {})
    if not isinstance(section, dict):
        raise ConfigError("[exposure] must be a table")
    unknown_exposure = sorted(set(section) - EXPOSURE_KEYS)
    if unknown_exposure:
        raise ConfigError(f"unknown [exposure] key(s): {', '.join(unknown_exposure)}")
    baseline = section.get("baseline", {})
    if not isinstance(baseline, dict) or not all(
        isinstance(key, str)
        and isinstance(value, list)
        and all(isinstance(kind, str) for kind in value)
        for key, value in baseline.items()
    ):
        raise ConfigError(
            "[exposure.baseline] must map a string path to a list of string finding kinds"
        )
    return Config(
        root=root,
        exposure=ExposureConfig(
            baseline={key: list(value) for key, value in baseline.items()},
            exclude=_strings(section, "exclude"),
            private_paths=_strings(section, "private_paths"),
            private_files=_strings(section, "private_files"),
            private_suffixes=_strings(section, "private_suffixes"),
            required_ignores=_strings(section, "required_ignores"),
            forbidden_suffixes=_strings(section, "forbidden_suffixes"),
            allowed_users=_strings(section, "allowed_users"),
            check_secrets=_boolean(section, "check_secrets", True),
            check_links=_boolean(section, "check_links", True),
            betterleaks_config=_string(section, "betterleaks_config", ".betterleaks.toml"),
            allowed_identities=_strings(section, "allowed_identities"),
            forbid_png_metadata=_boolean(section, "forbid_png_metadata", False),
            include_candidates=_boolean(section, "include_candidates", True),
            forbid_ai_attribution=_boolean(section, "forbid_ai_attribution", False),
            forbid_internal_planning=_boolean(section, "forbid_internal_planning", False),
            forbid_machine_observations=_boolean(section, "forbid_machine_observations", False),
            inspect_archives=_boolean(section, "inspect_archives", True),
            providers=_providers(section),
            provenance_required=_strings(section, "provenance_required"),
            provenance=_provenance(section),
        ),
    )
