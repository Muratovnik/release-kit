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


class ConfigError(Exception):
    """The configuration is missing or malformed; the caller decides how loudly."""


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
    check_links: bool = True
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

    section = raw.get("exposure", {})
    if not isinstance(section, dict):
        raise ConfigError("[exposure] must be a table")
    baseline = section.get("baseline", {})
    if not isinstance(baseline, dict) or not all(
        isinstance(value, list) for value in baseline.values()
    ):
        raise ConfigError("[exposure.baseline] must map a path to a list of finding kinds")
    return Config(
        root=root,
        exposure=ExposureConfig(
            names_file=str(section.get("names_file", ExposureConfig.names_file)),
            baseline={str(key): [str(kind) for kind in value] for key, value in baseline.items()},
            exclude=[str(item) for item in section.get("exclude", [])],
            private_paths=[str(item) for item in section.get("private_paths", [])],
            private_files=[str(item) for item in section.get("private_files", [])],
            private_suffixes=[str(item) for item in section.get("private_suffixes", [])],
            required_ignores=[str(item) for item in section.get("required_ignores", [])],
            forbidden_suffixes=[str(item) for item in section.get("forbidden_suffixes", [])],
            allowed_users=[str(item) for item in section.get("allowed_users", [])],
            check_links=bool(section.get("check_links", True)),
            include_candidates=bool(section.get("include_candidates", True)),
        ),
    )
