"""Public repository policy, containing no private locations or forbidden names."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import paths
from .exposure.audit import _git_bytes
from .release import settings as release_settings
from .release.changelog import PROFILES, is_version

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
        "owner_identities",
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
WINDOWS_RESERVED_STEMS = paths.WINDOWS_RESERVED_STEMS


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


def _portable_repository_path(value: str, key: str, *, allow_glob: bool = False) -> str:
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ConfigError(f"{key} must be a non-empty portable repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() in {".", ".."} or ".." in path.parts:
        raise ConfigError(f"{key} must stay inside the guarded repository")
    if any(
        ":" in part
        or any(character in part for character in '<>"|')
        or part.endswith((" ", "."))
        or paths.reserved_stem(part)
        for part in path.parts
    ):
        raise ConfigError(f"{key} must be portable across supported filesystems")
    if not allow_glob and any(character in value for character in "*?["):
        raise ConfigError(f"{key} must name an exact repository path, not a glob")
    return path.as_posix()


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
        if not surfaces:
            raise ConfigError(f"provider {name} allowed_surfaces must not be empty")
        surfaces = [
            _portable_repository_path(
                surface,
                f"provider {name} allowed_surfaces",
                allow_glob=True,
            )
            for surface in surfaces
        ]
        providers[name] = ProviderConfig(role=role, allowed_surfaces=surfaces)
    return providers


def _provenance(section: dict[str, object]) -> dict[str, str]:
    raw = section.get("provenance", {})
    if not isinstance(raw, dict) or not all(
        isinstance(pattern, str) and isinstance(kind, str) for pattern, kind in raw.items()
    ):
        raise ConfigError("[exposure.provenance] must map path patterns to strings")
    normalized: dict[str, str] = {}
    for pattern, kind in raw.items():
        normalized_pattern = _portable_repository_path(
            pattern,
            "[exposure.provenance] path pattern",
            allow_glob=True,
        )
        if kind not in PROVENANCE_KINDS:
            choices = ", ".join(sorted(PROVENANCE_KINDS))
            raise ConfigError(f"provenance for {pattern} must be one of: {choices}")
        if normalized_pattern in normalized:
            raise ConfigError("[exposure.provenance] paths must be unique after normalization")
        normalized[normalized_pattern] = kind
    return normalized


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
    # stable ``Name <email>`` spelling printed by Git. This list is closed: every
    # author, committer and tagger must be on it, contributors and hosts included.
    allowed_identities: list[str] = field(default_factory=list)
    # The owners' own identities, in the same spelling. They scope the commit-level
    # attribution rule to the owners' commits and leave anyone else's unjudged.
    owner_identities: list[str] = field(default_factory=list)
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
class GeneratorConfig:
    """How a draft entry is produced. Never how a published entry is chosen."""

    engine: str = ""
    command: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChangelogConfig:
    profile: str = "legacy"
    first_version: str = ""
    generator: GeneratorConfig | None = None
    # The name the project actually wrote, when that name has since been renamed. The
    # profile above is already the current one, so nothing downstream has to know.
    deprecated_profile: str = ""


# Only a tool this project provisions and verifies may be named instead of spelled out.
# A short name for anything else would have to guess at the operator's environment —
# npx or a global install, which package manager, which version — and guessing is the
# thing this tool refuses to do everywhere else.
GENERATOR_ENGINES = frozenset({"git-cliff"})
# Profiles whose names described something other than the layout they validate.
RENAMED_PROFILES = {"vue-like": "conventional-changelog"}


def renamed_profile_notice(old: str) -> str:
    """What to tell a project still naming a profile by its former name.

    Refusing the old name closed this tool's own upgrade path. The installed CLI cannot
    read the new name, so the configuration cannot be fixed first; the candidate CLI
    could not read the old one, so `update` failed its post-update audit and rolled
    back. Every adopter on the former name was stranded between two releases. The name
    is therefore accepted and mapped, and the project is told to move on.
    """
    return (
        f"changelog.profile {old!r} is the former name of {RENAMED_PROFILES[old]!r} and is "
        "accepted for compatibility; a profile names the layout it validates, not a project "
        f"that happens to publish it. Set profile = {RENAMED_PROFILES[old]!r} in relkit.toml"
    )


def _generator(section: dict[str, object]) -> GeneratorConfig | None:
    raw = section.get("generator")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("[changelog.generator] must be a table")
    unknown = sorted(set(raw) - {"engine", "command"})
    if unknown:
        raise ConfigError(f"unknown [changelog.generator] key(s): {', '.join(unknown)}")
    if ("engine" in raw) == ("command" in raw):
        raise ConfigError(
            "[changelog.generator] needs exactly one of engine (a provisioned tool) or "
            "command (an exact argv this project supplies)"
        )
    if "engine" in raw:
        engine = _string(raw, "engine", "")
        if engine not in GENERATOR_ENGINES:
            raise ConfigError(
                "changelog.generator.engine must be one of: "
                f"{', '.join(sorted(GENERATOR_ENGINES))}; use command for any other tool"
            )
        return GeneratorConfig(engine=engine)
    command = _strings(raw, "command")
    if not command or not all(isinstance(word, str) and word for word in command):
        raise ConfigError("changelog.generator.command must be a nonempty argv of nonempty strings")
    return GeneratorConfig(command=tuple(command))


def _changelog(raw: dict[str, object]) -> ChangelogConfig:
    section = raw.get("changelog", {})
    if not isinstance(section, dict):
        raise ConfigError("[changelog] must be a table")
    unknown = sorted(set(section) - {"profile", "first_version", "generator"})
    if unknown:
        raise ConfigError(f"unknown [changelog] key(s): {', '.join(unknown)}")
    profile = _string(section, "profile", "legacy")
    deprecated = ""
    if profile in RENAMED_PROFILES:
        deprecated, profile = profile, RENAMED_PROFILES[profile]
    if profile not in PROFILES:
        raise ConfigError(f"changelog.profile must be one of: {', '.join(sorted(PROFILES))}")
    first_version = _string(section, "first_version", "")
    if "first_version" in section and not is_version(first_version):
        raise ConfigError("changelog.first_version must be a SemVer version or tag")
    return ChangelogConfig(
        profile=profile,
        first_version=first_version,
        generator=_generator(section),
        deprecated_profile=deprecated,
    )


@dataclass(frozen=True)
class Config:
    root: Path
    exposure: ExposureConfig = field(default_factory=ExposureConfig)
    changelog: ChangelogConfig = field(default_factory=ChangelogConfig)
    release: release_settings.Settings | None = None


def load(root: Path, *, required: bool = True, staged: bool = False) -> Config:
    path = root / CONFIG_NAME
    try:
        if staged:
            indexed = _git_bytes(root, ["show", f":{CONFIG_NAME}"])
            if indexed.returncode:
                raise ConfigError(f"{CONFIG_NAME} could not be read from the Git index")
            text = indexed.stdout.decode("utf-8")
        else:
            text = path.read_text(encoding="utf-8")
        raw = tomllib.loads(text)
    except FileNotFoundError:
        if required:
            raise ConfigError(f"{CONFIG_NAME} is missing from {root}") from None
        raw = {}
    except (OSError, UnicodeError, RuntimeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{CONFIG_NAME} could not be read: {error}") from error

    unknown_top_level = sorted(set(raw) - {"exposure", "changelog", "release"})
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
    normalized_baseline = {
        _portable_repository_path(key, "[exposure.baseline] path"): list(value)
        for key, value in baseline.items()
    }
    if len(normalized_baseline) != len(baseline):
        raise ConfigError("[exposure.baseline] paths must be unique after normalization")
    private_paths = [
        _portable_repository_path(value, "private_paths")
        for value in _strings(section, "private_paths")
    ]
    private_files = [
        _portable_repository_path(value, "private_files")
        for value in _strings(section, "private_files")
    ]
    required_ignores = [
        _portable_repository_path(value, "required_ignores")
        for value in _strings(section, "required_ignores")
    ]
    private_suffixes = _strings(section, "private_suffixes")
    forbidden_suffixes = _strings(section, "forbidden_suffixes")
    for key, values in (
        ("private_suffixes", private_suffixes),
        ("forbidden_suffixes", forbidden_suffixes),
    ):
        if any(
            not value
            or value != value.strip()
            or not value.startswith(".")
            or "/" in value
            or "\\" in value
            or any(character in value for character in "*?[")
            for value in values
        ):
            raise ConfigError(f"{key} entries must be non-empty file suffixes beginning with '.'")
    betterleaks_config = _portable_repository_path(
        _string(section, "betterleaks_config", ".betterleaks.toml"),
        "betterleaks_config",
    )
    owner_identities = _strings(section, "owner_identities")
    if owner_identities and not _boolean(section, "forbid_ai_attribution", False):
        raise ConfigError(
            "owner_identities scopes forbid_ai_attribution and has no effect without it"
        )
    try:
        release = release_settings.parse(raw["release"]) if "release" in raw else None
    except ValueError as error:
        raise ConfigError(str(error)) from error
    return Config(
        root=root,
        release=release,
        changelog=_changelog(raw),
        exposure=ExposureConfig(
            baseline=normalized_baseline,
            exclude=[
                _portable_repository_path(value, "exclude", allow_glob=True)
                for value in _strings(section, "exclude")
            ],
            private_paths=private_paths,
            private_files=private_files,
            private_suffixes=private_suffixes,
            required_ignores=required_ignores,
            forbidden_suffixes=forbidden_suffixes,
            allowed_users=_strings(section, "allowed_users"),
            check_secrets=_boolean(section, "check_secrets", True),
            check_links=_boolean(section, "check_links", True),
            betterleaks_config=betterleaks_config,
            allowed_identities=_strings(section, "allowed_identities"),
            owner_identities=owner_identities,
            forbid_png_metadata=_boolean(section, "forbid_png_metadata", False),
            include_candidates=_boolean(section, "include_candidates", True),
            forbid_ai_attribution=_boolean(section, "forbid_ai_attribution", False),
            forbid_internal_planning=_boolean(section, "forbid_internal_planning", False),
            forbid_machine_observations=_boolean(section, "forbid_machine_observations", False),
            inspect_archives=_boolean(section, "inspect_archives", True),
            providers=_providers(section),
            provenance_required=[
                _portable_repository_path(value, "provenance_required", allow_glob=True)
                for value in _strings(section, "provenance_required")
            ],
            provenance=_provenance(section),
        ),
    )
