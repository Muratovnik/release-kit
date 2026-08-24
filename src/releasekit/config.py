"""The project's own configuration. The tool knows nothing until this is read.

Everything specific to a repository - which names must not appear, what is already
known to be there, where the version lives - is stated by that repository in
`relkit.toml`. Nothing in this package names a project, a person, a board, a service
or a path outside the repository it is pointed at.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "relkit.toml"
EXPOSURE_KEYS = frozenset(
    {
        "names_file",
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
    }
)
OVERLAY_KEYS = frozenset({"private_root", "manifest"})


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
class ExposureConfig:
    # The file listing names that must not appear. It is deliberately a path rather
    # than a list: a list of what must not be published cannot itself be published,
    # so the file belongs outside the guarded repository's history.
    names_file: str = ".publication-names"
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

    def names(self, root: Path) -> tuple[str, ...]:
        """Declared names, or nothing when the file is absent - which a clone expects."""
        try:
            lines = (root / self.names_file).read_text(encoding="utf-8").splitlines()
        except OSError:
            return ()
        return tuple(
            stripped
            for line in lines
            if (stripped := line.strip()) and not stripped.startswith("#")
        )


@dataclass(frozen=True)
class OverlayConfig:
    """Where the private half lives. Empty private_root means the project has none."""

    private_root: str = ""
    # Defaults to install.conf.yaml beside the private root, which is where dotbot
    # looks when it is run with that root as its base directory.
    manifest: str = ""

    def manifest_path(self, root: Path) -> Path | None:
        if not self.private_root:
            return None
        if self.manifest:
            return (root / self.manifest).resolve()
        return (root / self.private_root / "install.conf.yaml").resolve()

    def private_path(self, root: Path) -> Path | None:
        return (root / self.private_root).resolve() if self.private_root else None


@dataclass(frozen=True)
class Config:
    root: Path
    exposure: ExposureConfig = field(default_factory=ExposureConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)


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

    unknown_top_level = sorted(set(raw) - {"exposure", "overlay"})
    if unknown_top_level:
        raise ConfigError(f"unknown top-level key(s): {', '.join(unknown_top_level)}")
    section = raw.get("exposure", {})
    if not isinstance(section, dict):
        raise ConfigError("[exposure] must be a table")
    unknown_exposure = sorted(set(section) - EXPOSURE_KEYS)
    if unknown_exposure:
        raise ConfigError(f"unknown [exposure] key(s): {', '.join(unknown_exposure)}")
    overlay_section = raw.get("overlay", {})
    if not isinstance(overlay_section, dict):
        raise ConfigError("[overlay] must be a table")
    unknown_overlay = sorted(set(overlay_section) - OVERLAY_KEYS)
    if unknown_overlay:
        raise ConfigError(f"unknown [overlay] key(s): {', '.join(unknown_overlay)}")
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
            names_file=_string(section, "names_file", ExposureConfig.names_file),
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
        ),
        overlay=OverlayConfig(
            private_root=_string(overlay_section, "private_root", ""),
            manifest=_string(overlay_section, "manifest", ""),
        ),
    )
