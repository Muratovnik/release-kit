"""Read one Dotbot manifest as data, never as a partially recognized line format.

JSON uses the standard library. YAML is optional and uses PyYAML's safe loader;
missing YAML support is an actionable refusal, not a fallback parser. Resolution-
changing Dotbot options remain unsupported rather than producing a partial verdict.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from pathlib import Path


class ManifestError(Exception):
    """The manifest cannot be read completely under the supported mount contract."""


@dataclass(frozen=True)
class Mount:
    link: str
    target: str

    def link_path(self, private_root: Path) -> Path:
        return Path(posixpath.normpath(str(private_root / self.link).replace("\\", "/")))

    def target_path(self, private_root: Path) -> Path:
        return private_root / self.target


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ManifestError("manifest mappings require unique string keys")
        result[key] = value
    return result


def _yaml(text: str):
    try:
        import yaml
    except ImportError as error:
        raise ManifestError(
            "YAML manifests require PyYAML in the executing Python environment; "
            "install the overlay-yaml extra from reviewed release-kit source in an isolated "
            "environment, or review "
            "a conversion of this same Dotbot manifest to JSON. No mounts were verified."
        ) from error

    class UniqueSafeLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        # Native parsing handles quoting, comments, flow style and aliases. Reject
        # duplicate/overriding keys instead of allowing a later key to hide a mount.
        loader.flatten_mapping(node)
        return _unique(
            (loader.construct_object(key, deep=True), loader.construct_object(value, deep=True))
            for key, value in node.value
        )

    UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        return yaml.load(text, Loader=UniqueSafeLoader)
    except yaml.YAMLError as error:
        # The parser's error text can include private source lines. Report location,
        # not the manifest contents, to callers that may write publication logs.
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ManifestError("invalid or unsupported YAML" + location) from error


def read(path: Path) -> tuple[Mount, ...]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise ManifestError(f"the manifest could not be read: {path.name}") from error
    # JSON is valid YAML too: a reviewed in-place conversion needs neither a new
    # mount list nor a change to the existing install.conf.yaml discovery path.
    try:
        document = json.loads(text, object_pairs_hook=_unique)
    except json.JSONDecodeError as error:
        if path.suffix.lower() == ".json":
            raise ManifestError("invalid JSON manifest; no mounts were verified") from error
        document = _yaml(text)
    if not isinstance(document, list):
        raise ManifestError("a Dotbot manifest must be a list of directive mappings")
    mounts = []
    for directive in document:
        if not isinstance(directive, dict):
            raise ManifestError("every Dotbot directive must be a mapping")
        defaults = directive.get("defaults", {})
        if not isinstance(defaults, dict):
            raise ManifestError("Dotbot defaults must be a mapping")
        options = defaults.get("link", {})
        if not isinstance(options, dict):
            raise ManifestError("Dotbot link defaults must be a mapping")
        # These options change the mount population, target, or condition. The
        # verifier accepts explicit unconditional pairs, not Dotbot execution.
        if any(options.get(key) for key in ("glob", "if", "prefix", "exclude")):
            raise ManifestError("conditional/expanded Dotbot links are not supported")
        if "link" not in directive:
            continue
        links = directive["link"]
        if not isinstance(links, dict):
            raise ManifestError("Dotbot link must map explicit destinations to source paths")
        for link, target in links.items():
            if not isinstance(link, str) or not link or not isinstance(target, str) or not target:
                raise ManifestError(
                    "Dotbot links require nonempty string paths; extended/implicit forms "
                    "are not supported and would otherwise be skipped and never verified"
                )
            if any(char in link + target for char in "\0\n\r$~"):
                raise ManifestError("Dotbot links must use literal, unexpanded paths")
            mounts.append(Mount(link, target))
    if not mounts:
        raise ManifestError(f"{path.name} declares no links")
    return tuple(mounts)
