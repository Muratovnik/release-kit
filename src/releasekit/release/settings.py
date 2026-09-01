"""The opt-in GitHub/tag coordinator contract; build logic belongs to the project."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import PurePosixPath
from string import Formatter


class SettingsError(ValueError):
    """Invalid coordinator configuration."""


def relative(value: str) -> str:
    from ..config import _portable_repository_path

    _portable_repository_path(value, "release path")
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or any(ord(c) < 32 for c in value)
        or PurePosixPath(value).is_absolute()
        or any(
            part.casefold() in {".", "..", ".git"} or ":" in part or part.endswith((".", " "))
            for part in value.split("/")
        )
    ):
        raise ValueError(f"release path must be portable and repository-relative: {value!r}")
    return value


def filename(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}", value) or value.endswith("."):
        raise ValueError(f"invalid exact release asset filename: {value!r}")
    if value.split(".")[0].casefold() in {"con", "prn", "aux", "nul"} | {
        f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
    }:
        raise ValueError("release asset filename is reserved on Windows")
    return value


@dataclass(frozen=True)
class Settings:
    repository: str
    workflow: str
    required_jobs: list[str]
    version_file: str
    version_pattern: str
    assets: list[str]
    checks: list[list[str]]
    smoke: list[list[str]]
    smoke_platforms: list[str]
    remote: str = "origin"
    branch: str = ""
    changelog: str = "CHANGELOG.md"
    checksum_file: str = ""
    require_guard: bool = False
    owner_audit: bool = False
    timeout: int = 1800
    command_timeout: int = 600


def parse(raw: object) -> Settings:
    if not isinstance(raw, dict):
        raise SettingsError("[release] must be a table")
    unknown = set(raw) - {item.name for item in fields(Settings)}
    if unknown:
        raise ValueError(f"unknown [release] keys: {', '.join(sorted(unknown))}")
    try:
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
    ):
        if not isinstance(getattr(value, key), str):
            raise SettingsError(f"release.{key} must be a string")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value.repository):
        raise ValueError("release.repository must be a GitHub OWNER/REPO")
    if not re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", value.workflow):
        raise ValueError("release.workflow must name an exact .github/workflows YAML file")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value.remote):
        raise ValueError("release.remote must name a configured Git remote")
    relative(value.version_file)
    relative(value.changelog)
    try:
        if re.compile(value.version_pattern, re.MULTILINE).groups != 1:
            raise ValueError("release.version_pattern needs exactly one capturing group")
    except re.error as error:
        raise ValueError(f"invalid release.version_pattern: {error}") from error
    for key in ("required_jobs", "assets", "smoke_platforms"):
        items = getattr(value, key)
        if (
            not isinstance(items, list)
            or not items
            or not all(isinstance(item, str) and item.strip() == item and item for item in items)
            or len(items) != len(set(items))
        ):
            raise ValueError(f"release.{key} must be a nonempty list of unique strings")
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
    for key in ("checks", "smoke"):
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
    for key in ("require_guard", "owner_audit"):
        if type(getattr(value, key)) is not bool:
            raise ValueError(f"release.{key} must be true or false")
    if value.owner_audit and not value.require_guard:
        raise ValueError("release.owner_audit requires require_guard = true")
    for key in ("timeout", "command_timeout"):
        if type(getattr(value, key)) is not int or not 1 <= getattr(value, key) <= 86400:
            raise ValueError(f"release.{key} must be 1..86400 seconds")
    return value
