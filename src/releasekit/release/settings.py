"""Portable release contract and explicit delivery adapters; projects own build logic."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from string import Formatter

from ..paths import reserved_stem


class SettingsError(ValueError):
    """Invalid coordinator configuration."""


def relative(value: str, *, from_tree: bool = False) -> str:
    from ..config import _portable_repository_path

    # ls-tree supplies exact blob names, not pathspecs. Brackets are ordinary
    # portable characters (for example a dynamic route), never glob expansion.
    _portable_repository_path(value, "release path", allow_glob=from_tree)
    # Beyond the shared rule: an exact blob name never carries a glob, an empty
    # segment or Git's own directory, and it is used verbatim rather than normalized.
    if any(character in value for character in "*?") or any(
        part.casefold() in {"", ".", "..", ".git"} for part in value.split("/")
    ):
        raise ValueError(f"release path must be portable and repository-relative: {value!r}")
    return value


def filename(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}", value) or value.endswith("."):
        raise ValueError(f"invalid exact release asset filename: {value!r}")
    if reserved_stem(value):
        raise ValueError("release asset filename is reserved on Windows")
    return value


@dataclass(frozen=True)
class Settings:
    version_file: str
    version_pattern: str
    assets: list[str]
    checks: list[list[str]]
    smoke: list[list[str]]
    smoke_platforms: list[str]
    repository: str = ""
    workflow: str = ""
    required_jobs: list[str] = field(default_factory=list)
    publisher: str = "directory"
    build: list[list[str]] = field(default_factory=list)
    directory: str = ".cache/releases"
    remote: str = "origin"
    branch: str = ""
    changelog: str = "CHANGELOG.md"
    checksum_file: str = ""
    require_guard: bool = False
    owner_audit: bool = False
    # Build provenance is not something every repository can produce, and the
    # capability is a platform policy rather than a fact about the artifacts. The
    # adopter declares it; nothing here infers it from visibility or billing. Parse
    # preserves the legacy Actions default and selects false for local preparation.
    require_provenance: bool = True
    timeout: int = 1800
    command_timeout: int = 600
    candidate_jobs: list[str] = field(default_factory=list)
    candidate_artifact: str = "release-candidate"


def parse(raw: object) -> Settings:
    if not isinstance(raw, dict):
        raise SettingsError("[release] must be a table")
    unknown = set(raw) - {item.name for item in fields(Settings)}
    if unknown:
        raise ValueError(f"unknown [release] keys: {', '.join(sorted(unknown))}")
    try:
        # Existing configurations explicitly selected a workflow. Preserve their
        # publisher and signature policy; new configurations need no hosted CI.
        raw = dict(raw)
        raw.setdefault("publisher", "github-actions" if raw.get("workflow") else "directory")
        raw.setdefault("require_provenance", raw["publisher"] == "github-actions")
        value = Settings(**raw)
    except TypeError as error:
        raise ValueError(f"incomplete [release] settings: {error}") from error
    for key in (
        "repository",
        "workflow",
        "version_file",
        "version_pattern",
        "remote",
        "branch",
        "changelog",
        "checksum_file",
        "candidate_artifact",
        "publisher",
        "directory",
    ):
        if not isinstance(getattr(value, key), str):
            raise SettingsError(f"release.{key} must be a string")
    if value.publisher not in {"directory", "github", "github-actions"}:
        raise SettingsError("release.publisher must be directory, github or github-actions")
    hosted = value.publisher == "github-actions"
    if value.publisher != "directory" and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value.repository
    ):
        raise ValueError("release.repository must be a GitHub OWNER/REPO")
    if hosted and not re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", value.workflow):
        raise ValueError("release.workflow must name an exact .github/workflows YAML file")
    if not hosted and (
        value.workflow or value.required_jobs or value.candidate_jobs or value.require_provenance
    ):
        raise SettingsError(
            "local preparation requires no workflow, CI jobs or hosted build provenance; use publisher=github-actions explicitly for those capabilities"
        )
    if value.publisher == "directory" and (value.repository or value.branch):
        raise SettingsError("directory publication has no hosting repository or branch push")
    relative(value.directory)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value.remote):
        raise ValueError("release.remote must name a configured Git remote")
    relative(value.version_file)
    relative(value.changelog)
    filename(value.candidate_artifact)
    if (
        not isinstance(value.candidate_jobs, list)
        or any(
            not isinstance(item, str) or not item.strip() or item != item.strip()
            for item in value.candidate_jobs
        )
        or len(set(value.candidate_jobs)) != len(value.candidate_jobs)
    ):
        raise SettingsError("release.candidate_jobs must be a list of unique job names")
    try:
        if re.compile(value.version_pattern, re.MULTILINE).groups != 1:
            raise ValueError("release.version_pattern needs exactly one capturing group")
    except re.error as error:
        raise ValueError(f"invalid release.version_pattern: {error}") from error
    for key in (("required_jobs",) if hosted else ()) + ("assets", "smoke_platforms"):
        items = getattr(value, key)
        if (
            not isinstance(items, list)
            or (not items and key != "assets")
            or not all(isinstance(item, str) and item.strip() == item and item for item in items)
            or len(items) != len(set(items))
        ):
            raise ValueError(
                "release.assets must be a list of unique strings; empty publishes no files"
                if key == "assets"
                else f"release.{key} must be a nonempty list of unique strings"
            )
    # A release without files publishes the annotated tag, the committed notes and
    # the source tree at that tag. Nothing is built, so a build command would have
    # no output to produce and a checksum manifest would have nothing to list.
    if not value.assets and value.build:
        raise SettingsError("release.build must be empty when release.assets is empty")
    if not value.assets and value.checksum_file:
        raise SettingsError("release.checksum_file needs a nonempty release.assets")
    if set(value.smoke_platforms) - {"linux", "darwin", "win32"}:
        raise ValueError("release.smoke_platforms supports linux, darwin, win32")
    for template in value.assets + ([value.checksum_file] if value.checksum_file else []):
        try:
            for _, field, spec, conversion in Formatter().parse(template):
                if field is not None and (field not in {"version", "tag"} or spec or conversion):
                    raise ValueError("asset templates only support {version} and {tag}")
            filename(template.format(version="1.2.3", tag="v1.2.3"))
        except (KeyError, IndexError) as error:
            raise ValueError("invalid release asset template") from error
    for key in ("checks", "smoke") + (() if hosted or not value.assets else ("build",)):
        commands = getattr(value, key)
        if (
            not isinstance(commands, list)
            or not commands
            or any(
                not isinstance(command, list)
                or not command
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)
                for command in commands
            )
        ):
            raise ValueError(f"release.{key} must be a nonempty list of argv arrays")
        if len({tuple(command) for command in commands}) != len(commands):
            raise ValueError(f"release.{key} contains a duplicate command in the same phase")
        for command in commands:
            for arg in command:
                for _, field, spec, conversion in Formatter().parse(arg):
                    if field is not None and (
                        field
                        not in {"python", "source", "assets", "temp", "version", "tag", "commit"}
                        or spec
                        or conversion
                    ):
                        raise ValueError(
                            "unknown release command placeholder; escape literal braces as {{ }}"
                        )
    for key in ("require_guard", "owner_audit", "require_provenance"):
        if type(getattr(value, key)) is not bool:
            raise ValueError(f"release.{key} must be true or false")
    if value.owner_audit and not value.require_guard:
        raise ValueError("release.owner_audit requires require_guard = true")
    for key in ("timeout", "command_timeout"):
        if type(getattr(value, key)) is not int or not 1 <= getattr(value, key) <= 86400:
            raise ValueError(f"release.{key} must be 1..86400 seconds")
    return value


def local(value: dict | Settings) -> bool:
    """Old receipts predate publisher selection and retain Actions semantics."""
    return (
        value.get("publisher", "github-actions") if isinstance(value, dict) else value.publisher
    ) != "github-actions"
