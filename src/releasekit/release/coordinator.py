"""Release lifecycle with portable preparation and explicitly selected delivery."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .. import __version__, canonical, config, owner, processes, protection, publication, storage
from ..result import Result
from . import changelog, settings, versions
from .backend import (
    CommandError,
    GitHub,
    Pending,
    ReleaseError,
    Runner,
    ancestor,
    clean,
    local_tags,
    merged_tags,
    remote_identity,
    remote_refs,
    repository,
    tag_commits,
)


def publisher(runner, release, supplied=None):
    if release.publisher == "directory":
        from .directory import Directory

        return Directory(runner, release)
    return supplied or GitHub(runner, release.repository)


def publication_refs(runner, release):
    return (
        local_tags(runner)
        if release.publisher == "directory"
        else remote_refs(runner, release.remote)
    )


STABLE_TAG = re.compile(r"refs/tags/v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")

CI_POLL_SECONDS = 5
CI_POLL_CEILING_SECONDS = 30.0
CI_RECONCILE_SECONDS = 60.0


def fingerprint(value: object) -> str:
    return canonical.fingerprint(value)


def version_tag(value: str) -> tuple[str, str]:
    version = changelog.normalize(value)
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ReleaseError("release coordination currently supports stable X.Y.Z / vX.Y.Z only")
    return version, "v" + version


def source(runner: Runner, sha: str, path: str) -> str:
    relative = settings.relative(path)
    mode = runner.git("ls-tree", sha, "--", relative).split(" ", 1)[0]
    if mode not in {"100644", "100755"}:
        raise ReleaseError(f"release input must be an ordinary committed file: {relative}")
    return runner.call(["git", "show", f"{sha}:{relative}"])


def source_tree(runner: Runner, sha: str) -> list[tuple[str, str, str]]:
    result = []
    for record in runner.call(["git", "ls-tree", "-r", "-z", "--full-tree", sha]).split("\0"):
        if not record:
            continue
        metadata, name = record.split("\t", 1)
        mode, kind, oid = metadata.split(" ")
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ReleaseError(
                "release source snapshot requires regular files; links and submodules need owner handling"
            )
        result.append((mode, oid, settings.relative(name, from_tree=True)))
    if len({name.casefold() for _, _, name in result}) != len(result):
        raise ReleaseError("source filenames collide on case-insensitive hosts")
    return result


def previous_tag(
    runner: Runner, refs: dict[str, str], version: str, sha: str, first: str, target: str
) -> dict | None:
    # Only the stable release line is this command's business. A personal local tag
    # or somebody else's remote tag is not a reason to refuse to plan a release.
    local = {ref: oid for ref, oid in local_tags(runner).items() if STABLE_TAG.fullmatch(ref)}
    remote = {ref: oid for ref, oid in refs.items() if STABLE_TAG.fullmatch(ref)}
    local.pop(f"refs/tags/{target}", None)
    remote.pop(f"refs/tags/{target}", None)
    if local != remote:
        differing = sorted(
            ref.removeprefix("refs/tags/")
            for ref in local.keys() | remote.keys()
            if local.get(ref) != remote.get(ref)
        )
        raise ReleaseError(
            "local and remote stable release tags disagree ("
            + ", ".join(differing)
            + "); review and fetch explicitly (no automatic repair)"
        )
    candidates = []
    current = tuple(map(int, version.split(".")))
    commits = tag_commits(runner) if local else {}
    reachable = merged_tags(runner, sha) if local else set()
    for ref, oid in local.items():
        tag = ref.removeprefix("refs/tags/")
        number = tuple(map(int, tag[1:].split(".")))
        if ref not in reachable:
            raise ReleaseError(
                f"stable tag {tag} is outside this release ancestry; explicit reconciliation is required"
            )
        if number >= current:
            raise ReleaseError(f"version must advance the existing stable tag {tag}")
        candidates.append((number, {"tag": tag, "oid": oid, "sha": commits[ref]}))
    if not candidates:
        if changelog.normalize(first) != version:
            raise ReleaseError(
                "no previous stable tag: declare changelog.first_version explicitly, never infer first release"
            )
        return None
    if changelog.normalize(first) == version:
        raise ReleaseError("declared first release already has a stable predecessor")
    return max(candidates, key=lambda item: item[0])[1]


def notes_for(
    runner: Runner,
    policy: config.Config,
    release: settings.Settings,
    version: str,
    tag: str,
    sha: str,
    previous: dict | None,
) -> str:
    profile = "strict" if policy.changelog.profile == "legacy" else policy.changelog.profile
    entry = changelog.entry_for(
        source(runner, sha, release.changelog),
        version,
        profile=profile,
        first_version=policy.changelog.first_version,
    )
    if entry is None:
        raise ReleaseError(f"no changelog entry for {version}")
    lines = changelog._visible_lines(entry)
    base = f"https://github.com/{release.repository}"
    expected = (
        f"{base}/compare/{previous['tag']}...{tag}" if previous else f"{base}/releases/tag/{tag}"
    )
    urls = re.findall(r"\]\((https?://[^\s)]+)\)", lines[0])
    portable = release.publisher == "directory"
    if (
        portable
        and previous
        and urls
        and not all(url.endswith(f"/{previous['tag']}...{tag}") for url in urls)
    ):
        raise ReleaseError("changelog comparison must use the published version boundary")
    if not portable and previous and urls != [expected]:
        raise ReleaseError(f"changelog heading must compare the actual Git boundary: {expected}")
    if not portable and not previous and urls and urls != [expected]:
        raise ReleaseError("first release heading points to the wrong tag/repository")
    section = ""
    included: set[str] | None = None
    for line in lines[1:]:
        if line.startswith("### "):
            section = line[4:].strip()
        if section in changelog._EDITORIAL:
            continue
        for match in changelog._COMMIT_LINK.finditer(line):
            parts = urlsplit(match[2])
            path = unquote(parts.path)
            target = (
                re.search(r"/commit/([a-fA-F0-9]{7,64})$", path)
                if portable
                else re.fullmatch(
                    rf"/{re.escape(release.repository)}/commit/([a-fA-F0-9]{{7,64}})", path
                )
            )
            if (
                parts.scheme != "https"
                or (not portable and parts.netloc != "github.com")
                or parts.query
                or parts.fragment
                or not target
            ):
                raise ReleaseError("changelog commit link must belong to the configured repository")
            commit = runner.git("rev-parse", "--verify", f"{target[1]}^{{commit}}")
            if not commit.startswith(match[1].lower()):
                raise ReleaseError("changelog references a commit outside the release")
            if included is None and previous:
                # One walk decides every link in the entry: two merge-base processes
                # per referenced commit is the slowest part of planning a release.
                included = set(runner.git("rev-list", sha, "--not", previous["sha"]).split())
            if included is None or commit not in included:
                if not ancestor(runner, commit, sha):
                    raise ReleaseError("changelog references a commit outside the release")
                if previous and ancestor(runner, commit, previous["sha"]):
                    raise ReleaseError(
                        "changelog change was already included in the previous release"
                    )
    return entry.replace("\r\n", "\n").rstrip("\n")


_JOBS_HEADER = re.compile(r"jobs:[ \t]*(?:#.*)?")
_MAPPING_KEY = re.compile(r"( *)([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(?:#.*)?")
_NAME_KEY = re.compile(r"( *)name:[ \t]*(.*)")


def _literal_scalar(raw: str) -> str | None:
    """A plain or quoted YAML scalar; None for expressions, blocks, anchors, flows."""
    value = raw.strip()
    if value[:1] in {"'", '"'}:
        quote = value[0]
        end = value.find(quote, 1)
        while quote == "'" and end != -1 and value[end + 1 : end + 2] == "'":
            end = value.find(quote, end + 2)
        if end == -1:
            return None
        value = value[1:end].replace("''", "'") if quote == "'" else value[1:end]
    else:
        value = value.split(" #", 1)[0].rstrip()
    if not value or "${{" in value or value[0] in "|>&*!{[":
        return None
    return value


def workflow_jobs(text: str) -> dict[str, str | None] | None:
    """Job ids and display names of a block-style top-level `jobs:` mapping.

    A job without `name:` is displayed under its id; an expression name is None.
    Returns None when the mapping cannot be read without a YAML engine (flow style,
    anchors, tabs or a missing key), so the caller reports that instead of guessing.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    start = next((i for i, line in enumerate(lines) if _JOBS_HEADER.fullmatch(line)), None)
    if start is None:
        return None
    jobs: dict[str, str | None] = {}
    indent = child_indent = None
    current = None
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" "):
            break
        width = len(line) - len(line.lstrip(" "))
        if line[width : width + 1] == "\t":
            return None
        indent = width if indent is None else indent
        if width < indent:
            return None
        if width == indent:
            match = _MAPPING_KEY.fullmatch(line)
            if not match or match[2] in jobs:
                return None
            current = match[2]
            jobs[current] = current
            child_indent = None
            continue
        if current is None:
            return None
        child_indent = width if child_indent is None else child_indent
        if width == child_indent and (match := _NAME_KEY.fullmatch(line)):
            jobs[current] = _literal_scalar(match[2])
    return jobs or None


def job_matches(displayed: str, required: str) -> bool:
    """Whether a finished job is one the plan required, matrix legs included.

    GitHub displays a matrix job as `name (values)`, and `job_coverage` accepts that
    prefix before a tag exists. A finished-run check that demanded an exact name
    refused precisely the jobs it had already approved, and only once a tag existed.
    """
    return displayed == required or displayed.startswith(f"{required} (")


def job_coverage(required: list[str], jobs: dict[str, str | None] | None) -> dict:
    """Static evidence that each required CI job exists before any tag is pushed.

    GitHub displays matrix jobs as `name (values)`; the prefix before ` (` is the
    declared id or literal name. Jobs with expression names cannot be refuted here.
    """
    if jobs is None:
        return {"declared": None, "missing": [], "unverified": list(required), "optional": []}
    known = {name for name in jobs.values() if name}
    bases = {name: name.split(" (", 1)[0] for name in required}
    unmatched = [name for name in required if name not in known and bases[name] not in known]
    dynamic = any(name is None for name in jobs.values())
    covered = set(required) | set(bases.values())
    return {
        "declared": jobs,
        "missing": [] if dynamic else unmatched,
        "unverified": unmatched if dynamic else [],
        "optional": [
            job
            for job, name in jobs.items()
            if name is not None and job not in covered and name not in covered
        ],
    }


def plan(
    runner: Runner, value: str, *, github: GitHub | None = None, candidate_receipt: bool = True
) -> dict:
    repository(runner)
    clean(runner)
    version, tag = version_tag(value)
    policy = config.load(runner.root)
    release = policy.release
    if release is None:
        raise ReleaseError("configure the opt-in [release] contract before planning")
    if release.require_guard and (problem := protection.problem(runner.root)):
        raise ReleaseError(f"protect check failed: {problem}")
    if sys.platform not in release.smoke_platforms:
        raise ReleaseError(
            f"no declared downloaded-application smoke for this host ({sys.platform})"
        )
    if release.publisher != "directory":
        remote_identity(runner, release.remote, release.repository)
    sha = runner.git("rev-parse", "HEAD")
    if (
        config.CONFIG_NAME
        not in runner.git("ls-tree", "--name-only", sha, "--", config.CONFIG_NAME).splitlines()
    ):
        raise ReleaseError("relkit.toml must be committed")
    source_tree(runner, sha)
    observed = re.findall(
        release.version_pattern,
        source(runner, sha, release.version_file).replace("\r\n", "\n"),
        re.MULTILINE,
    )
    if observed != [version] and observed != [tag]:
        raise ReleaseError("version_file must contain exactly one matching requested version")
    coverage = job_coverage(
        list(dict.fromkeys(release.required_jobs + release.candidate_jobs)),
        workflow_jobs(source(runner, sha, release.workflow)) if release.workflow else {},
    )
    if coverage["missing"]:
        raise ReleaseError(
            f"required_jobs are not declared by {release.workflow}: "
            + ", ".join(coverage["missing"])
            + "; fix the name or the workflow before a tag exists"
        )
    refs = publication_refs(runner, release)
    if f"refs/heads/{tag}" in refs or runner.git(
        "for-each-ref", "--format=%(refname)", f"refs/heads/{tag}"
    ):
        raise ReleaseError(
            "a branch shares the release tag name; CI ref identity would be ambiguous"
        )
    if f"refs/tags/{tag}" in refs or f"refs/tags/{tag}" in local_tags(runner):
        raise ReleaseError(
            "target tag already exists; use the recorded release resume, never rewrite it"
        )
    github = publisher(runner, release, github)
    previous = versions.predecessor(
        runner, github, refs, version, sha, policy.changelog.first_version
    )
    notes = notes_for(runner, policy, release, version, tag, sha, previous)
    versions.check_carried_changes(
        runner, source(runner, sha, release.changelog), notes, version, previous
    )
    pushes = []
    if release.branch:
        runner.git("check-ref-format", f"refs/heads/{release.branch}")
        if runner.git("rev-parse", f"refs/heads/{release.branch}") != sha:
            raise ReleaseError("the configured publication branch must point to the release commit")
        old = refs.get(f"refs/heads/{release.branch}")
        if old and not ancestor(runner, old, sha):
            raise ReleaseError(
                "publication branch is not a proven fast-forward; fetch/reconcile explicitly"
            )
        pushes.append(f"{sha}:refs/heads/{release.branch}")
    if release.publisher != "directory":
        pushes.append(f"refs/tags/{tag}:refs/tags/{tag}")
    identity = github.identity()
    if identity["full_name"].casefold() != release.repository.casefold():
        raise ReleaseError("GitHub redirected the repository; review its current identity")
    if github.release(tag) is not None:
        raise ReleaseError("target release/draft already exists without an owned run")
    workflow = (
        github.api(f"/actions/workflows/{quote(release.workflow.rsplit('/', 1)[-1], safe='')}")
        if not settings.local(release)
        else {"id": None}
    )
    if not settings.local(release) and (
        workflow["path"] != release.workflow or workflow["state"] != "active"
    ):
        raise ReleaseError("configured release workflow is not active at the expected path")
    if settings.local(release):
        github.preflight()
    if release.publisher != "directory":
        gh_version = runner.call(["gh", "--version"])
        match = re.search(r"gh version (\d+)\.(\d+)\.(\d+)", gh_version)
        if not match or tuple(map(int, match.groups())) < (2, 98, 0):
            raise ReleaseError(
                "GitHub delivery requires GitHub CLI >= 2.98.0; directory delivery needs no hosting CLI"
            )
    assets = [settings.filename(item.format(version=version, tag=tag)) for item in release.assets]
    if len({name.casefold() for name in assets}) != len(assets):
        raise ReleaseError("release assets collide after template expansion or case folding")
    checksum_file = release.checksum_file.format(version=version, tag=tag)
    if checksum_file and checksum_file not in assets:
        raise ReleaseError("checksum_file must be included in the exact asset set")
    value = {
        "schema": 1,
        "tool_version": __version__,
        "root": str(runner.root),
        "repository_id": identity["id"],
        "settings": asdict(release),
        "sha": sha,
        "version": version,
        "tag": tag,
        "previous": previous,
        "previous_source": "published-release",
        "workflow_id": workflow["id"],
        "notes": notes,
        "assets": assets,
        "checksum_file": checksum_file,
        "workflow_jobs": {key: coverage[key] for key in ("declared", "unverified", "optional")},
        "host": sys.platform,
        "pushes": pushes,
        "source_config_sha256": hashlib.sha256(
            source(runner, sha, config.CONFIG_NAME).encode()
        ).hexdigest(),
    }

    if candidate_receipt and release.candidate_jobs:
        from . import candidate

        if proof := candidate.ready(runner, value):
            value["candidate"] = proof
    elif candidate_receipt and settings.local(release):
        from . import local

        if proof := local.ready(runner, value):
            value["candidate"] = proof
    return value


def required_provenance(value: dict) -> bool:
    """Whether this plan verifies build provenance, including for an older receipt.

    A receipt written before the setting existed was made when provenance was
    unconditional, so a missing key reads as required. Reading it as waived would let
    an older attempt finish with less verification than it was authorized under.
    """
    return bool(value["settings"].get("require_provenance", True))


def caveats(value: dict) -> list[str]:
    """Operator-facing limits of this plan that the JSON alone does not spell out."""
    if settings.local(value["settings"]):
        return [
            "Build, checks and smoke run locally; no hosted CI or paid build-provenance service is required.",
            "The exact prepared files are verified before publication; local checks cover only this host.",
            "Directory delivery is portable and needs no hosting account. GitHub delivery is an optional adapter with immutable-release signature verification.",
        ]
    jobs = value.get("workflow_jobs") or {}
    lines = [
        (
            f"CI must publish exactly the {len(value['assets'])} planned asset(s); the set is "
            "checked against GitHub's signed release attestation only after the immutable "
            "release exists, and cannot be corrected afterwards"
        )
    ]
    if value["settings"].get("candidate_jobs"):
        lines[0] = (
            "Candidate files are checked before tagging; the CI draft gate must compare the full uploaded set before publication. GitHub's release signature is verified afterwards."
        )
    if value["checksum_file"]:
        lines.append(
            f"{value['checksum_file']} must list every other planned asset; a second "
            "manifest cannot be a published asset"
        )
    if "declared" in jobs and jobs["declared"] is None:
        lines.append(
            f"{value['settings']['workflow']} jobs could not be read statically; "
            "required_jobs are checked only against the finished CI run"
        )
    elif jobs.get("unverified"):
        lines.append(
            "required_jobs matched no literal job id or name and rely on expression names: "
            + ", ".join(jobs["unverified"])
        )
    if jobs.get("optional"):
        lines.append(
            "workflow jobs outside required_jobs do not gate publication: "
            + ", ".join(jobs["optional"])
        )
    lines.append(
        (
            "CI must produce build-provenance attestations for every planned asset; a "
            "repository that cannot fails after the tag exists"
        )
        if required_provenance(value)
        else (
            "build provenance is not verified for this release: the published set and its "
            "digests still come from GitHub's signed release attestation, but nothing proves "
            "which workflow run produced those bytes"
        )
    )
    lines.append(
        f"local checks run on {value.get('host', 'this host')} only; a green local run "
        "is not the CI platform matrix"
    )
    return lines


def described_plan(value: dict) -> dict:
    return {
        "plan_sha256": fingerprint(value),
        **value,
        "actions": [
            "project checks",
            "audit worktree + history",
            "protect check if configured",
            "annotated tag + tag metadata audit",
            (
                "export to the configured directory (needs --publish)"
                if value["settings"].get("publisher") == "directory"
                else "atomic exact-ref push (needs --publish)"
            ),
            (
                "publish the prepared files with the selected adapter"
                if settings.local(value["settings"])
                else "observe tag CI; CI alone publishes"
            ),
            (
                "verify exported manifest, notes and exact assets"
                if value["settings"].get("publisher") == "directory"
                else "verify immutable release, notes, assets, signatures"
            ),
            "smoke downloaded files from pinned source",
            "cleanup inventoried temporary files",
        ],
        "caveats": caveats(value),
    }


def show_plan(value: dict, *, human: bool = False) -> None:
    described = described_plan(value)
    if human:
        print(f"relkit release: plan {value['tag']} @ {value['sha']}")
        print(f"  Publisher: {value['settings'].get('publisher', 'github-actions')}")
        print(
            f"  Destination: {value['settings']['repository'] or value['settings'].get('directory', '')}"
        )
        previous = value.get("previous")
        print(f"  Previous tag: {previous['tag'] if previous else 'first release'}")
        print(f"  Plan SHA-256: {described['plan_sha256']}")
        for number, action in enumerate(described["actions"], 1):
            print(f"  {number}. {action}")
        prepared = bool(value.get("candidate"))
        print(f"  Candidate: {'prepared and bound to this plan' if prepared else 'not prepared'}")
        print(
            "relkit release: plan only; no commands executed or publication performed by this command"
        )
        if (
            value["settings"].get("candidate_jobs") or settings.local(value["settings"])
        ) and not prepared:
            print(
                f"relkit release: first: relkit release prepare {value['tag']}"
                + ("" if settings.local(value["settings"]) else " --ci-run ID")
                + ", then plan again"
            )
        else:
            print(
                f"relkit release: next: relkit release run {value['tag']} --publish "
                f'--plan-hash {described["plan_sha256"]} --root "{value["root"]}"'
            )
    else:
        print(json.dumps(described, indent=2, sort_keys=True))
    # The JSON is data; this block is what an operator must read before --publish.
    print(
        f"relkit release: exact asset set for {value['tag']} ({len(value['assets'])} file(s)):",
        file=sys.stderr,
    )
    for name in value["assets"]:
        print(f"  {name}", file=sys.stderr)
    for line in described["caveats"]:
        print(f"relkit release: note: {line}", file=sys.stderr)


def _state_path(root: Path, tag: str) -> Path:
    return storage.service_root(root) / "releases" / tag / "state.json"


def _save(path: Path, state: dict) -> None:
    storage.atomic_json(path, state)


def read_state(runner: Runner, path: Path, tag: str, *, same_version: bool) -> dict:
    path = storage.inside(runner.root, path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ReleaseError("saved release state exceeds the read limit")
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or saved.get("schema") != 1:
        raise ReleaseError("unsupported saved release state schema")
    value = saved["plan"]
    if (
        saved.get("plan_sha256") != fingerprint(value)
        or value["root"] != str(runner.root)
        or value["tag"] != tag
        or value["schema"] != 1
        or not isinstance(value["tool_version"], str)
        or (same_version and value["tool_version"] != __version__)
    ):
        raise ReleaseError(
            "saved plan identity/version is invalid; use the same release-kit version and checkout"
        )
    settings.parse(value["settings"])
    if (
        version_tag(value["version"])[1] != tag
        or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value["sha"])
        or saved["publication"] not in {"not-pushed", "draft", "published"}
        or saved["verification"] not in {"not-run", "running", "failed", "passed"}
        or saved["cleanup"]
        not in {"not-run", "passed", "retained-unowned-or-changed-files", "blocked-unsafe-path"}
    ):
        raise ReleaseError("malformed saved release state")
    for temporary in [saved.get("temporary"), *saved.get("retained_temporaries", [])]:
        if temporary:
            storage.inside(runner.root, Path(temporary))
    return saved


def record_result(result: Result, state: dict, path: Path, *, saved: bool = False) -> None:
    from . import feedback

    value = state["plan"]
    result.data["release"] = {
        "observation": "local-receipt" if saved else "current-run",
        "receipt": str(path),
        "log": str(path.parent / "run.log"),
        "tag": value["tag"],
        "sha": value["sha"],
        "plan_sha256": state["plan_sha256"],
        "plan": described_plan(value),
        "tool_version": value["tool_version"],
        "resume_version_matches": value["tool_version"] == __version__,
        **feedback.facts(state),
        **{
            key: state.get(key)
            for key in (
                "publication",
                "verification",
                "cleanup",
                "process_cleanup",
                "stage",
                "stages",
                "ci",
                "release_id",
                "artifacts",
                "verified_at",
                "smoke_platform",
                "local_changes",
                "temporary",
                "retained_temporaries",
                "error",
                "outcome",
                "verifier_version",
                "ci_problems",
            )
        },
    }
    result.next_action = feedback.next_action(state)


def _stage(path: Path, state: dict, name: str, status: str = "running") -> None:
    state["stage"] = name
    state.setdefault("stages", {})[name] = status
    _save(path, state)


def validate_saved_plan(runner: Runner, value: dict) -> None:
    """A receipt digest detects corruption; it is not authority to invent commands/refspecs."""
    raw_text = source(runner, value["sha"], config.CONFIG_NAME)
    raw = tomllib.loads(raw_text)
    release = settings.parse(raw.get("release"))
    if (
        asdict(release) != asdict(settings.parse(value["settings"]))
        or hashlib.sha256(raw_text.encode()).hexdigest() != value["source_config_sha256"]
    ):
        raise ReleaseError("saved release settings differ from the pinned committed configuration")
    expected = [f"refs/tags/{value['tag']}:refs/tags/{value['tag']}"]
    if release.publisher == "directory":
        expected = []
    if release.branch:
        runner.git("check-ref-format", f"refs/heads/{release.branch}")
        expected.insert(0, f"{value['sha']}:refs/heads/{release.branch}")
    if value["pushes"] != expected:
        raise ReleaseError("saved refspecs are not the canonical exact-ref publication plan")
    assets = [
        settings.filename(name.format(version=value["version"], tag=value["tag"]))
        for name in release.assets
    ]
    if value["assets"] != assets or value["checksum_file"] != release.checksum_file.format(
        version=value["version"], tag=value["tag"]
    ):
        raise ReleaseError("saved assets differ from the pinned configuration")
    policy = config.Config(runner.root, changelog=config._changelog(raw), release=release)
    if value["notes"] != notes_for(
        runner, policy, release, value["version"], value["tag"], value["sha"], value["previous"]
    ):
        raise ReleaseError("saved notes differ from the pinned changelog")


def _tag_message(value: dict) -> str:
    message = f"Release {value['version']}\n\nRelease plan SHA-256: {fingerprint(value)}"
    if proof := value.get("candidate"):
        message += (
            f"\nRelease candidate: local/{proof['attempt']} {proof['sha256']}"
            if proof.get("kind") == "local"
            else (
                f"\nRelease candidate: {proof['ci']['id']}/{proof['ci']['attempt']} {proof['sha256']}"
            )
        )
    return message


def _owned_tag(runner: Runner, state: dict) -> str | None:
    value = state["plan"]
    ref = f"refs/tags/{value['tag']}"
    oid = local_tags(runner).get(ref)
    if oid is None:
        if state.get("tag_oid"):
            raise ReleaseError(
                "owned local tag is missing; restore it explicitly from the verified remote"
            )
        return None
    if state.get("tag_oid") and oid != state["tag_oid"]:
        raise ReleaseError("owned local tag object changed")
    if (
        runner.git("cat-file", "-t", oid) != "tag"
        or runner.git("rev-parse", f"{ref}^{{commit}}") != value["sha"]
    ):
        raise ReleaseError("existing tag is not the planned annotated tag")
    message = runner.git("cat-file", "tag", oid).split("\n\n", 1)[1]
    message = re.split(r"\n-----BEGIN (?:PGP|SSH) SIGNATURE-----", message)[0].strip()
    if not state.get("tag_intent") or message != _tag_message(value):
        raise ReleaseError("existing tag is not owned by this release plan")
    return oid


def _reconcile(runner: Runner, github: GitHub, state: dict) -> tuple[bool, dict | None]:
    value = state["plan"]
    release = settings.parse(value["settings"])
    if release.publisher != "directory":
        remote_identity(runner, release.remote, release.repository)
    identity = github.identity()
    if (
        identity["id"] != value["repository_id"]
        or identity["full_name"].casefold() != release.repository.casefold()
    ):
        raise ReleaseError("repository identity changed since planning")
    refs = publication_refs(runner, release)
    if previous := value["previous"]:
        if "release_id" in previous:
            observed = github.release(previous["tag"])
            if (
                not observed
                or observed["id"] != previous["release_id"]
                or observed["draft"]
                or observed["prerelease"]
            ):
                raise ReleaseError("previous published release disappeared or changed identity")
        ref = f"refs/tags/{previous['tag']}"
        if (
            refs.get(ref) != previous["oid"]
            or runner.git("rev-parse", f"{previous['oid']}^{{commit}}") != previous["sha"]
        ):
            raise ReleaseError("previous release tag identity changed since planning")
    if f"refs/heads/{value['tag']}" in refs:
        raise ReleaseError("remote branch now collides with the release tag")
    ref = f"refs/tags/{value['tag']}"
    tag_oid = _owned_tag(runner, state)
    pushed = bool(state.get("export_started")) if release.publisher == "directory" else ref in refs
    if pushed and (
        not state.get("push_started")
        or refs[ref] != tag_oid
        or (release.publisher != "directory" and refs.get(ref + "^{}") != value["sha"])
    ):
        raise ReleaseError("remote tag differs from this run's annotated tag and commit")
    if not pushed and state.get("pushed"):
        raise ReleaseError("previously pushed tag disappeared; no automatic recreation")
    if pushed and release.branch:
        branch = refs.get(f"refs/heads/{release.branch}")
        if branch != value["sha"] and (not branch or not ancestor(runner, value["sha"], branch)):
            raise ReleaseError(
                "planned publication branch is missing or its advancement cannot be proven"
            )
    # Once this run knows the release it created, read that resource rather than
    # searching a list that may not have caught up with the write.
    identifier = state.get("release_id")
    published = github.release_by_id(identifier) if identifier else github.release(value["tag"])
    if published is not None:
        if not pushed or published["tag_name"] != value["tag"]:
            raise ReleaseError("release exists without this run's verified pushed tag")
        if state.get("release_id") and state["release_id"] != published["id"]:
            raise ReleaseError("release identity changed since observation")
        state["release_id"] = published["id"]
        state["publication"] = "draft" if published["draft"] else "published"
    elif state.get("release_id"):
        raise ReleaseError("previously observed release disappeared")
    state["tag_oid"] = tag_oid
    state["pushed"] = pushed
    return pushed, published


def _commands(
    runner: Runner,
    commands: list[list[str]],
    value: dict,
    snapshot: Path,
    assets: Path,
    temporary: Path,
) -> None:
    substitutions = {
        "python": sys.executable,
        "source": str(snapshot),
        "assets": str(assets),
        "temp": str(temporary),
        "version": value["version"],
        "tag": value["tag"],
        "commit": value["sha"],
    }
    previous = runner.temporary
    runner.temporary = temporary
    try:
        for index, command in enumerate(commands, start=1):
            print(f"relkit release: project command {index}/{len(commands)}", flush=True)
            runner.call(
                [arg.format(**substitutions) for arg in command],
                cwd=snapshot,
                timeout=value["settings"]["command_timeout"],
            )
    finally:
        runner.temporary = previous


def _audit(runner: Runner, release: settings.Settings, *, history: bool, no_download: bool) -> None:
    assert runner.log is not None
    with (
        storage.checked(runner.log).open("a", encoding="utf-8") as stream,
        contextlib.redirect_stdout(stream),
        contextlib.redirect_stderr(stream),
    ):
        status = publication.run(
            runner.root,
            history=history,
            staged=False,
            strict=False,
            owner_mode=release.owner_audit,
            require_overlay=False,
            allow_download=not no_download,
        )
    if status:
        raise ReleaseError(
            f"{'history' if history else 'worktree'} publication audit failed; see the release log"
        )


def _local_checks(runner, value, workspace, no_download, path=None, state=None):
    def stage(name, status="running"):
        if path is not None:
            _stage(path, state, name, status)

    release = settings.parse(value["settings"])
    clean(runner, value["sha"])
    if release.require_guard and (problem := protection.problem(runner.root)):
        raise ReleaseError(f"protect check failed: {problem}")
    # No reuse of local command success across invocations: ignored dependencies,
    # tools, environment and hooks may have changed even if HEAD did not.
    print("relkit release: local checks", flush=True)
    stage("local-checks")
    _commands(
        runner,
        release.checks,
        value,
        runner.root,
        workspace.path / "assets",
        workspace.path / "project-temp",
    )
    clean(runner, value["sha"])
    if release.require_guard and (problem := protection.problem(runner.root)):
        raise ReleaseError(f"protect check failed: {problem}")
    stage("local-checks", "passed")
    stage("worktree-audit")
    _audit(runner, release, history=False, no_download=no_download)
    clean(runner, value["sha"])
    stage("worktree-audit", "passed")
    stage("history-audit")
    _audit(runner, release, history=True, no_download=no_download)
    clean(runner, value["sha"])
    stage("history-audit", "passed")


def _prepare(
    runner: Runner,
    github: GitHub,
    state: dict,
    path: Path,
    workspace: storage.Workspace,
    no_download: bool,
) -> None:
    value = state["plan"]
    release = settings.parse(value["settings"])
    if release.candidate_jobs:
        from . import candidate

        proof = value.get("candidate")
        if not proof:
            raise ReleaseError(
                "no matching prepared candidate; run release prepare VERSION --ci-run ID, then review plan again"
            )
        expected, _ = candidate.inputs(runner, value["version"])
        _local_checks(runner, value, workspace, no_download, path, state)
        record = candidate.download(
            runner,
            github,
            expected,
            release,
            proof["ci"]["id"],
            workspace.path / "candidate-assets",
        )
        workspace.remember(workspace.path / "candidate-assets")
        if (
            fingerprint(record) != proof["sha256"]
            or record["ci"] != proof["ci"]
            or record["files"] != proof["files"]
        ):
            raise ReleaseError(
                "prepared candidate changed; prepare and review again before tagging"
            )
    elif settings.local(release):
        from . import local

        local.load(runner, value)
        _local_checks(runner, value, workspace, no_download, path, state)
        local.load(runner, value)
        github.preflight()
    else:
        _local_checks(runner, value, workspace, no_download, path, state)
    refs = publication_refs(runner, release)
    policy = config.load(runner.root)
    if (
        asdict(policy.release) != value["settings"]
        or (
            versions.predecessor(
                runner, github, refs, value["version"], value["sha"], policy.changelog.first_version
            )
            if value.get("previous_source") == "published-release"
            else previous_tag(
                runner,
                refs,
                value["version"],
                value["sha"],
                policy.changelog.first_version,
                value["tag"],
            )
        )
        != value["previous"]
    ):
        raise ReleaseError("release inputs or previous tag changed since planning")
    tag_oid = _owned_tag(runner, state)
    _stage(path, state, "annotated-tag")
    if not tag_oid:
        state["tag_intent"] = True
        _save(path, state)
        runner.git(
            "tag", "--annotate", value["tag"], value["sha"], "--message", _tag_message(value)
        )
        state["tag_oid"] = _owned_tag(runner, state)
        _save(path, state)
        # An annotation adds a publication input even though the source SHA is unchanged.
        # This focused metadata check avoids re-running both complete engine scans.
        from ..exposure.audit import _reference_metadata_failures

        private = owner.discover(runner.root).load() if release.owner_audit else owner.OwnerRules()
        problems = _reference_metadata_failures(
            runner.root,
            names=private.private_values,
            owner_workflows=private.owner_workflows,
            private_patterns=private.patterns,
            allowed_users=policy.exposure.allowed_users,
            allowed_identities=policy.exposure.allowed_identities,
            forbid_ai_attribution=policy.exposure.forbid_ai_attribution,
            forbid_internal_planning=policy.exposure.forbid_internal_planning,
            forbid_machine_observations=policy.exposure.forbid_machine_observations,
        )
        if problems:
            raise ReleaseError("new tag metadata fails publication policy: " + "; ".join(problems))
    _stage(path, state, "annotated-tag", "passed")
    clean(runner, value["sha"])
    # Refuse a draft/tag created concurrently, before the authorized write.
    pushed, existing = _reconcile(runner, github, state)
    if pushed or existing:
        raise ReleaseError("remote release state changed during preparation; resume to reconcile")
    state["push_started"] = datetime.now(UTC).isoformat()
    if release.publisher == "directory":
        state["export_started"] = True
        state["pushed"] = True
        _save(path, state)
        return
    _save(path, state)
    print("relkit release: push planned refs", flush=True)
    _stage(path, state, "push")
    try:
        runner.git("push", "--atomic", release.remote, *value["pushes"])
    except (CommandError, Pending):
        # A lost response is not proof that the remote write failed.
        pushed, _ = _reconcile(runner, github, state)
        _save(path, state)
        if not pushed:
            raise
    pushed, _ = _reconcile(runner, github, state)
    if not pushed:
        raise Pending("push was not observed on the server")
    _stage(path, state, "push", "passed")


def _ci(
    runner: Runner,
    github: GitHub,
    state: dict,
    path: Path,
    deadline: float,
    accept_ci_attempt: int = 0,
    *,
    verify_only: bool = False,
) -> dict:
    value = state["plan"]
    print("relkit release: waiting for the planned tag workflow", flush=True)
    _stage(path, state, "ci")
    state["ci_verdict"] = "pending"
    state["ci_problems"] = []
    # Full reconciliation costs about a dozen Git and GitHub calls. It is the right
    # check on entry, on completion and periodically, and a waste on every poll of a
    # run that is still going: an hour of five-second reconciles is thousands of API
    # requests against GitHub's secondary rate limits.
    pushed, published = _reconcile(runner, github, state)
    reconciled = time.monotonic()
    delay = float(CI_POLL_SECONDS)
    while True:
        if not pushed:
            raise ReleaseError("tag disappeared while waiting for CI")
        matches = []
        for run in github.runs(value["workflow_id"], value["sha"]):
            if (
                run["head_sha"] == value["sha"]
                and run["head_branch"] == value["tag"]
                and run["event"] == "push"
                and run["workflow_id"] == value["workflow_id"]
                and run["path"] == value["settings"]["workflow"]
                and run["repository"]["id"] == value["repository_id"]
                and datetime.fromisoformat(run["created_at"])
                >= datetime.fromisoformat(state["push_started"]).replace(microsecond=0)
            ):
                matches.append(run)
        if len(matches) > 1:
            raise ReleaseError("multiple matching tag workflow runs; CI identity is ambiguous")
        if matches:
            run = matches[0]
            identity = {"id": run["id"], "attempt": run["run_attempt"]}
            if state.get("ci") and state["ci"] != identity:
                old = state["ci"]
                if (
                    identity["id"] != old["id"]
                    or identity["attempt"] != accept_ci_attempt
                    or accept_ci_attempt <= old["attempt"]
                ):
                    raise ReleaseError(
                        "CI run/attempt changed; review it and explicitly --accept-ci-attempt N on resume"
                    )
                state.setdefault("previous_ci", []).append(old)
            state["ci"] = identity
            _save(path, state)
            if run["status"] == "completed":
                pushed, published = _reconcile(runner, github, state)
                reconciled = time.monotonic()
                if not pushed:
                    raise ReleaseError("tag disappeared while waiting for CI")
                if run["conclusion"] != "success":
                    state["ci_problems"].append(
                        f"CI failed ({run['conclusion']}); release publication is {state['publication']}"
                    )
                jobs = github.jobs(run["id"], run["run_attempt"])
                for name in value["settings"]["required_jobs"]:
                    selected = [job for job in jobs if job_matches(job["name"], name)]
                    if not selected:
                        state["ci_problems"].append(
                            f"required CI job never ran in this run: {name}"
                        )
                    # Every matched leg, not the first: a matrix gates publication only
                    # if all of its legs passed on this exact commit.
                    failed = [
                        job["name"]
                        for job in selected
                        if job["status"] != "completed"
                        or job["conclusion"] != "success"
                        or job["head_sha"] != value["sha"]
                    ]
                    if failed:
                        state["ci_problems"].append(
                            "required CI job did not pass for this exact commit: "
                            + ", ".join(sorted(failed))
                        )
                state["ci_verdict"] = "failed" if state["ci_problems"] else "passed"
                if state["ci_problems"]:
                    _stage(path, state, "ci", "failed")
                    if not verify_only:
                        raise ReleaseError("; ".join(state["ci_problems"]))
                if published and not published["draft"]:
                    _stage(path, state, "ci", state["ci_verdict"])
                    return published
                if published:
                    raise Pending(
                        "CI finished but left a draft; the CI owner must resolve it, no duplicate release will be created"
                    )
        _save(path, state)
        if time.monotonic() >= deadline:
            raise Pending("tag CI/publication is still pending")
        time.sleep(min(delay, max(0, deadline - time.monotonic())))
        delay = min(CI_POLL_CEILING_SECONDS, delay * 1.5)
        if time.monotonic() - reconciled >= CI_RECONCILE_SECONDS:
            pushed, published = _reconcile(runner, github, state)
            reconciled = time.monotonic()


RELEASE_ATTESTATION_PREDICATE = "https://in-toto.io/attestation/release/v0.2"


def attested_assets(statement: dict, value: dict, release: dict, tag_oid: str) -> dict[str, str]:
    """Asset name to SHA-256, read only from a statement naming this exact release.

    The subject without a name is the release itself, and its SHA-1 is the ref as
    GitHub resolved it: for an annotated tag that is the tag object, not the commit.
    """
    predicate = statement.get("predicate")
    if statement.get("predicateType") != RELEASE_ATTESTATION_PREDICATE or not isinstance(
        predicate, dict
    ):
        raise ReleaseError("release attestation is not a GitHub release statement")
    if (
        str(predicate.get("repository", "")).casefold()
        != value["settings"]["repository"].casefold()
        or str(predicate.get("repositoryId")) != str(value["repository_id"])
        or str(predicate.get("databaseId")) != str(release["id"])
        or predicate.get("tag") != value["tag"]
    ):
        raise ReleaseError("release attestation names another repository, release or tag")
    assets: dict[str, str] = {}
    tagged = 0
    for subject in statement.get("subject") or []:
        if not isinstance(subject, dict) or not isinstance(subject.get("digest"), dict):
            raise ReleaseError("release attestation carries a malformed subject")
        digest = subject["digest"]
        if subject.get("name") is None:
            if digest.get("sha1") != tag_oid:
                raise ReleaseError("release attestation binds a different tag object")
            tagged += 1
            continue
        name = settings.filename(str(subject["name"]))
        if name in assets:
            raise ReleaseError("release attestation lists an asset twice")
        if not re.fullmatch(r"[a-f0-9]{64}", str(digest.get("sha256"))):
            raise ReleaseError(f"release attestation asset lacks a SHA-256 digest: {name}")
        assets[name] = str(digest["sha256"])
    if tagged != 1:
        raise ReleaseError("release attestation does not bind exactly one tag object")
    return assets


def _asset_identity(asset: dict) -> dict:
    if (
        type(asset.get("id")) is not int
        or type(asset.get("size")) is not int
        or asset["size"] < 0
        or asset.get("state") != "uploaded"
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(asset.get("digest", "")))
    ):
        raise ReleaseError("release asset lacks uploaded identity, size or SHA-256 digest")
    settings.filename(asset["name"])
    return {key: asset[key] for key in ("id", "name", "size", "digest")}


def asset_set(assets: list[dict], names: list[str]) -> list[dict]:
    """The same uploaded-inventory rule before and after publication."""
    identities = sorted(
        (_asset_identity(asset) for asset in assets), key=lambda asset: asset["name"]
    )
    if [asset["name"] for asset in identities] != sorted(names):
        raise ReleaseError("release assets differ from the planned exact file set")
    return identities


def _published_identity(github: GitHub, value: dict, release: dict, tag_oid: str) -> dict:
    directory = value["settings"].get("publisher") == "directory"
    if (
        release["draft"]
        or release["prerelease"]
        or (not directory and release.get("immutable") is not True)
    ):
        raise ReleaseError("release must be published, stable and immutable")
    if release["tag_name"] != value["tag"]:
        raise ReleaseError("published release names another tag")
    if (release.get("body") or "").replace("\r\n", "\n").rstrip("\n") != value["notes"]:
        raise ReleaseError("published notes differ from the committed changelog entry")
    assets = asset_set(github.assets(release["id"]), value["assets"])
    if directory:
        github.verify(value, release, tag_oid)
        github.selected_tag = value["tag"]
        return {
            "release_id": release["id"],
            "assets": assets,
            "notes_sha256": fingerprint(value["notes"]),
        }
    # Everything above came from an unsigned REST response. GitHub also signs a
    # statement about an immutable release and its exact asset set; require the two
    # to agree, so a rewritten API answer cannot decide what was published.
    attested = attested_assets(github.release_attestation(value["tag"]), value, release, tag_oid)
    if attested != {asset["name"]: asset["digest"].removeprefix("sha256:") for asset in assets}:
        raise ReleaseError(
            "GitHub's signed release attestation does not match the reported assets or digests"
        )
    return {
        "release_id": release["id"],
        "assets": assets,
        "notes_sha256": fingerprint(value["notes"]),
    }


def _checksums(value: dict, directory: Path, assets: list[dict]) -> None:
    name = value["checksum_file"]
    if not name:
        return
    expected = {asset["name"]: asset["digest"][7:] for asset in assets if asset["name"] != name}
    actual = {}
    for line in (directory / name).read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64}) [ *](.+)", line)
        if not match or match[2] in actual:
            raise ReleaseError("checksum manifest has an invalid or duplicate entry")
        actual[settings.filename(match[2])] = match[1].lower()
    if actual != expected:
        raise ReleaseError("checksum manifest does not match every other planned asset")


def _blob_sizes(runner: Runner, sha: str) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for record in runner.call(["git", "ls-tree", "-r", "-z", "--long", "--full-tree", sha]).split(
        "\0"
    ):
        if not record:
            continue
        fields = record.split("\t", 1)[0].split()
        if len(fields) == 4 and fields[1] == "blob":
            sizes[fields[2]] = int(fields[3])
    return sizes


def _snapshot(runner: Runner, value: dict, workspace: storage.Workspace) -> Path:
    from ..exposure.audit import _batch_objects, _history_batches

    snapshot = workspace.path / "source"
    snapshot.mkdir()
    targets: dict[str, list[tuple[Path, str]]] = {}
    for mode, oid, name in source_tree(runner, value["sha"]):
        target = storage.inside(snapshot, snapshot / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        targets.setdefault(oid, []).append((target, mode))
    sizes = _blob_sizes(runner, value["sha"])
    # Bypass archive attributes and checkout filters: smoke reads exact blobs. One
    # process per file is minutes of spawn overhead on a repository of any size, so
    # read them in batches bounded by bytes rather than by file count.
    for batch in _history_batches([(oid, sizes.get(oid, 0)) for oid in targets]):
        for oid, payload in _batch_objects(runner.root, batch, expected_type="blob"):
            for target, mode in targets[oid]:
                target.write_bytes(payload)
                target.chmod(0o755 if mode == "100755" else 0o644)
                # Identity is recorded once the file is final; cleanup compares it.
                workspace.remember(target)
    return snapshot


def _verify(
    runner: Runner,
    github: GitHub,
    state: dict,
    path: Path,
    release: dict,
    workspace: storage.Workspace,
) -> None:
    value = state["plan"]
    print(
        "relkit release: verifying exported files"
        if value["settings"].get("publisher") == "directory"
        else "relkit release: verifying publication and signatures",
        flush=True,
    )
    state["verification"] = "running"
    _stage(path, state, "publication-verification")
    identity = _published_identity(github, value, release, state["tag_oid"])
    if state.get("artifacts") and state["artifacts"] != identity:
        raise ReleaseError("published artifact identity changed across resume")
    state["artifacts"] = identity
    _save(path, state)
    directory = workspace.path / "assets"
    directory.mkdir()
    for asset in identity["assets"]:
        destination = directory / asset["name"]
        github.download(asset, destination)
        workspace.remember(destination)
        if (
            destination.stat().st_size != asset["size"]
            or storage.digest(destination) != asset["digest"][7:]
        ):
            raise ReleaseError(f"downloaded checksum/size mismatch: {asset['name']}")
    _checksums(value, directory, identity["assets"])
    if proof := value.get("candidate"):
        files = [
            {key: asset[key] for key in ("name", "size", "digest")} for asset in identity["assets"]
        ]
        if sorted(files, key=lambda item: item["name"]) != proof["files"]:
            raise ReleaseError("published files differ from the prepared candidate")
    if value["settings"].get("publisher") != "directory":
        github.signatures(
            value["tag"],
            value["sha"],
            value["settings"]["workflow"],
            [directory / asset["name"] for asset in identity["assets"]],
            ci=state.get("ci", {}),
            repository_id=value["repository_id"],
            provenance=required_provenance(value),
        )
    _stage(path, state, "publication-verification", "passed")
    snapshot = _snapshot(runner, value, workspace)
    print("relkit release: downloaded-application smoke from pinned source", flush=True)
    _stage(path, state, "application-smoke")
    _commands(
        runner,
        value["settings"]["smoke"],
        value,
        snapshot,
        directory,
        workspace.path / "project-temp",
    )
    # Smoke must not silently replace the bytes we just verified.
    for asset in identity["assets"]:
        if storage.digest(directory / asset["name"]) != asset["digest"][7:]:
            raise ReleaseError("downloaded artifact changed during smoke")
    _, latest = _reconcile(runner, github, state)
    if latest is None or _published_identity(github, value, latest, state["tag_oid"]) != identity:
        raise ReleaseError(
            "publication changed during verification (notes remain editable even on immutable releases)"
        )
    # Last, because `remember` inventories once per path: an earlier call recorded
    # the digests `gh` then changed on the reconcile above, so cleanup correctly
    # refused to sweep its own tool's cache and reported it retained on every
    # successful release. Project scratch lives in `project-temp` and stays unknown.
    workspace.remember(workspace.path / "runtime")
    state["verification"] = "passed"
    state["verified_at"] = datetime.now(UTC).isoformat()
    state["smoke_platform"] = sys.platform
    _stage(path, state, "application-smoke", "passed")


def _process_alive(pid: int) -> bool | None:
    """Best-effort liveness that never signals the process (Windows os.kill terminates)."""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return {5: True, 87: False}.get(ctypes.get_last_error())
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _lock_owner(lock: Path) -> str:
    try:
        owner_record = json.loads(lock.read_text(encoding="utf-8"))
        pid, tag, root = owner_record["pid"], owner_record["tag"], owner_record["root"]
    except (OSError, ValueError, KeyError, TypeError):
        return "its recorded owner is unreadable"
    alive = _process_alive(pid) if type(pid) is int else None
    liveness = {
        True: "a process with this PID is running",
        False: "no process with this PID is running",
        None: "PID liveness could not be checked",
    }[alive]
    return f"pid {pid} ({liveness}), tag {tag}, root {root}"


def _acquire_lock(root: Path, tag: str) -> Path:
    lock = storage.service_root(root) / "release.lock"
    storage.checked(lock).parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "tag": tag, "root": str(root)}))
    except FileExistsError as error:
        raise ReleaseError(
            f"another release owns {lock}: {_lock_owner(lock)}; after a crash verify all "
            "owned commands and descendants have stopped before manually removing this exact "
            "lock; a stopped PID alone is insufficient"
        ) from error
    return lock


def _abandon(runner: Runner, github: GitHub | None, state: dict, path: Path, reason: str) -> None:
    """Record that an unpublished attempt ends here, without touching any ref."""
    value = state["plan"]
    tag = value["tag"]
    if outcome := state.get("outcome"):
        raise ReleaseError(f"{tag} was already recorded as {outcome['status']} at {outcome['at']}")
    if state["publication"] == "published" or state["verification"] == "passed":
        raise ReleaseError("a published release cannot be abandoned; its version is taken")
    release = settings.parse(value["settings"])
    if release.publisher != "directory":
        remote_identity(runner, release.remote, release.repository)
    github = publisher(runner, release, github)
    ref = f"refs/tags/{tag}"
    if release.publisher != "directory" and ref in remote_refs(runner, release.remote):
        raise ReleaseError(
            f"{tag} still exists on {release.remote}; abandon records only an attempt whose "
            "remote tag is gone, and release-kit never deletes refs"
        )
    if github.release(tag) is not None:
        raise ReleaseError(
            f"a release or draft exists for {tag}; its owner must resolve it before the "
            "attempt can be abandoned"
        )
    state["outcome"] = {
        "status": "abandoned",
        "reason": reason,
        "at": datetime.now(UTC).isoformat(),
        "tool_version": __version__,
        "remote_tag": "absent",
        "release": "absent",
    }
    state["stage"] = "abandoned"
    state.setdefault("stages", {})["abandoned"] = "recorded"
    _save(path, state)
    if ref in local_tags(runner):
        print(
            f"relkit release: local tag {tag} still exists; delete it explicitly "
            f"(git tag -d {tag}) before a new run, release-kit never deletes refs",
            file=sys.stderr,
        )


def _archive_abandoned(runner: Runner, path: Path, tag: str) -> Path | None:
    """Move an abandoned receipt aside so the same version can be attempted again."""
    try:
        saved = read_state(runner, path, tag, same_version=False)
    except (ReleaseError, OSError, ValueError, KeyError, TypeError):
        return None
    if (saved.get("outcome") or {}).get("status") != "abandoned":
        return None
    directory = storage.checked(path.parent)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for suffix in ("", *(f"-{n}" for n in range(1, 100))):
        target = directory.with_name(f"{tag}.abandoned-{stamp}{suffix}")
        if not target.exists():
            break
    else:
        raise ReleaseError("too many archived attempts for one tag in one second")
    directory.rename(storage.checked(target))
    if runner.log is not None and Path(runner.log).is_relative_to(directory):
        runner.log = None  # the new attempt opens its own log once its receipt exists
    return target


def run(
    root: Path,
    action: str,
    version: str,
    *,
    publish: bool = False,
    bump: str = "",
    ci_run: int = 0,
    assets: str = "",
    plan_hash: str = "",
    no_download: bool = False,
    accept_ci_attempt: int = 0,
    reason: str = "",
    runner: Runner | None = None,
    github: GitHub | None = None,
    result: Result | None = None,
    human: bool = False,
) -> int:
    from . import feedback

    result = result or Result()
    original_log = runner.log if runner is not None else None
    state = None
    state_path = None
    lock = None
    lock_identity = None
    cleanup_unconfirmed = False
    workspace = None
    try:
        root = storage.checked(root)
        runner = runner or Runner(root)
        if action == "status":
            runner.log = None
        repository(runner)
        if action == "next":
            if (
                version
                or publish
                or plan_hash
                or no_download
                or accept_ci_attempt
                or reason
                or ci_run
                or assets
            ):
                raise ReleaseError("release next accepts only --bump and --root")
            policy = config.load(root)
            if policy.release is None:
                raise ReleaseError("configure the opt-in [release] contract first")
            if policy.release.publisher != "directory":
                remote_identity(runner, policy.release.remote, policy.release.repository)
            github = publisher(runner, policy.release, github)
            value = versions.next_version(
                runner, github, policy.release, policy.changelog.first_version, bump
            )
            result.data["next"] = value
            print(
                f"relkit release: next {value['tag']}; previous publication: {value['previous']['tag'] if value['previous'] else 'none'}; tag {value['tag_state']}"
            )
            if value["tag_state"] == "occupied":
                print(
                    "relkit release: the number is unchanged; inspect status/resume for the occupied tag before proceeding"
                )
            return 0
        if bump:
            raise ReleaseError("--bump is accepted only by release next")
        _, tag = version_tag(version)
        if accept_ci_attempt and (action != "resume" or accept_ci_attempt < 1):
            raise ReleaseError("--accept-ci-attempt is a positive explicit resume-only choice")
        if reason and action != "abandon":
            raise ReleaseError("--reason is accepted only by release abandon")
        if action == "verify" and (publish or no_download):
            raise ReleaseError("verify never publishes; --publish/--no-download are not accepted")
        if action == "abandon":
            if not reason.strip():
                raise ReleaseError(
                    "abandon requires a non-empty --reason that is kept in the receipt"
                )
            if publish or plan_hash or no_download:
                raise ReleaseError(
                    "abandon records a local outcome; publication flags are not accepted"
                )
        if action in {"bundle", "promote", "draft"}:
            from . import candidate

            if not assets or publish or ci_run or plan_hash or no_download:
                raise ReleaseError("CI helper requires --assets and accepts no publication flags")
            directory = storage.inside(root, root / assets)
            release = config.load(root).release
            if release is None:
                raise ReleaseError("configure release first")
            if settings.local(release):
                raise ReleaseError("CI helpers require publisher = github-actions")
            github = github or GitHub(runner, release.repository)
            data = (
                candidate.bundle(runner, version, directory)
                if action == "bundle"
                else getattr(candidate, action)(runner, github, version, directory)
            )
            result.data[action] = data
            print(f"relkit release: {action} {tag} passed")
            return 0
        if assets or (ci_run and action != "prepare"):
            raise ReleaseError("--assets is only for CI helpers; --ci-run is only for prepare")
        if action == "prepare":
            from . import candidate

            if publish or plan_hash:
                raise ReleaseError("prepare never publishes; review the resulting plan afterwards")
            lock = _acquire_lock(root, tag)
            lock_identity = storage.identity(lock)
            value = plan(runner, version, github=github, candidate_receipt=False)
            github = publisher(runner, settings.parse(value["settings"]), github)
            if settings.local(value["settings"]):
                from . import local

                if ci_run:
                    raise ReleaseError("local preparation needs no --ci-run")
                local.prepare(runner, github, value, no_download, result)
            else:
                candidate.prepare(runner, github, value, ci_run, no_download, result)
            return 0
        if action == "plan":
            value = plan(runner, version, github=github)
            result.data["plan"] = described_plan(value)
            show_plan(value, human=human)
            return 0
        if action == "status":
            if publish or plan_hash or no_download:
                raise ReleaseError(
                    "status only reads local state; mutation/verification flags are not accepted"
                )
            path = _state_path(root, tag)
            saved = read_state(runner, path, tag, same_version=False)
            validate_saved_plan(runner, saved["plan"])
            record_result(result, saved, path, saved=True)
            print(
                f"relkit release: recorded {tag}: {feedback.summary(saved)}; no remote check performed"
            )
            print(f"relkit release: receipt: {path}")
            return 0
        if action == "abandon":
            lock = _acquire_lock(root, tag)
            lock_identity = storage.identity(lock)
            path = storage.checked(_state_path(root, tag))
            saved = read_state(runner, path, tag, same_version=False)
            _abandon(runner, github, saved, path, reason.strip())
            record_result(result, saved, path, saved=True)
            print(
                f"relkit release: recorded {tag} as abandoned; a new run may reuse the "
                f"version once its local tag is gone; receipt: {path}"
            )
            return 0
        if action not in {"run", "resume", "verify"}:
            raise ReleaseError("unknown release action")
        if action != "verify" and not publish:
            raise ReleaseError(
                "run/resume require --publish after reviewing release plan; plan is read-only"
            )
        lock = _acquire_lock(root, tag)
        lock_identity = storage.identity(lock)
        state_path = storage.checked(_state_path(root, tag))
        if action in {"resume", "verify"}:
            saved = read_state(runner, state_path, tag, same_version=False)
            if outcome := saved.get("outcome"):
                raise ReleaseError(
                    f"{tag} was recorded as {outcome['status']} at {outcome['at']}; "
                    "start a new release run instead of resuming"
                )
            if action == "resume" and saved["plan"]["tool_version"] != __version__:
                raise ReleaseError(
                    "saved plan identity/version is invalid; use the same release-kit version and checkout"
                )
            value = saved["plan"]
            state = saved
            validate_saved_plan(runner, value)
        else:
            if state_path.exists():
                archived = _archive_abandoned(runner, state_path, tag)
                if archived is None:
                    raise ReleaseError(
                        f"a run for {tag} is already recorded; use release resume, or "
                        "release abandon --reason for an attempt that will never publish"
                    )
                result.data["archived_receipt"] = str(archived)
                print(f"relkit release: archived the abandoned attempt receipt: {archived}")
            value = plan(runner, version, github=github)
            if (
                value["settings"].get("candidate_jobs") or settings.local(value["settings"])
            ) and not value.get("candidate"):
                raise ReleaseError(
                    "no matching prepared candidate; run release prepare VERSION"
                    + ("" if settings.local(value["settings"]) else " --ci-run ID")
                    + ", then review plan again"
                )
            if plan_hash and fingerprint(value) != plan_hash:
                raise ReleaseError("reviewed plan is stale; plan again before publishing")
            state = {
                "schema": 1,
                "plan": value,
                "plan_sha256": fingerprint(value),
                "publication": "not-pushed",
                "verification": "not-run",
                "cleanup": "not-run",
            }
            _save(state_path, state)
        if action in {"resume", "verify"} and plan_hash and plan_hash != fingerprint(value):
            raise ReleaseError("supplied plan hash differs from the saved plan")
        if accept_ci_attempt and settings.local(value["settings"]):
            raise ReleaseError("local publication has no CI attempt to accept")
        if sys.platform not in value["settings"]["smoke_platforms"]:
            raise ReleaseError("resume host is not declared for the downloaded-application smoke")
        runner.log = state_path.parent / "run.log"
        storage.checked(runner.log).touch(exist_ok=True)
        runner.log.chmod(0o600)
        github = publisher(runner, settings.parse(value["settings"]), github)
        state["verification"] = "not-run"
        state.pop("process_cleanup", None)
        # Reconciliation precedes project commands, downloads, retries and cleanup.
        pushed, existing = _reconcile(runner, github, state)
        _save(state_path, state)
        if action == "verify" and (not pushed or not existing or existing["draft"]):
            raise ReleaseError("verify requires an existing published release for the saved plan")
        workspace = storage.Workspace(root, "release-")
        runner.temporary = workspace.path / "runtime"
        runner.temporary.mkdir()
        workspace.remember(runner.temporary)
        (workspace.path / "project-temp").mkdir()
        workspace.remember(workspace.path / "project-temp")
        workspace.scratch(workspace.path / "project-temp")
        if old_temporary := state.get("temporary"):
            old_path = storage.inside(root, Path(old_temporary))
            if old_path.exists() and old_temporary not in state.setdefault(
                "retained_temporaries", []
            ):
                state["retained_temporaries"].append(old_temporary)
        state["temporary"] = str(workspace.path)
        _save(state_path, state)
        if not pushed and action != "verify":
            _prepare(runner, github, state, state_path, workspace, no_download)
        deadline = time.monotonic() + value["settings"]["timeout"]
        if settings.local(value["settings"]):
            from . import local

            published = local.publish(
                runner, github, state, state_path, workspace, verify_only=action == "verify"
            )
        else:
            published = _ci(
                runner,
                github,
                state,
                state_path,
                deadline,
                accept_ci_attempt,
                verify_only=action == "verify",
            )
        state["verifier_version"] = __version__
        _verify(runner, github, state, state_path, published, workspace)
        if state.get("ci_verdict") not in {"passed", "not-required"}:
            raise ReleaseError(
                "artifacts verified; release acceptance incomplete: "
                + "; ".join(state["ci_problems"])
            )
        cleaned = workspace.cleanup(discard_scratch=True)
        workspace = None
        state["cleanup"] = "passed" if cleaned else "retained-unowned-or-changed-files"
        state.pop("error", None)
        state["local_changes"] = bool(
            runner.git("status", "--porcelain=v1", "--untracked-files=all")
        )
        _save(state_path, state)
        record_result(result, state, state_path)
        print(
            f"relkit release: published and verified {tag} @ {value['sha']}; receipt: {state_path}"
        )
        print(f"relkit release: {feedback.summary(state)}")
        if state["local_changes"]:
            print(
                "relkit release: new local changes are separate from the published result; left untouched"
            )
        if not cleaned:
            print(f"relkit release: cleanup retained changed/unowned files: {state['temporary']}")
        return 0
    except (
        processes.CleanupError,
        ReleaseError,
        config.ConfigError,
        storage.StorageError,
        changelog.ChangelogError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        KeyboardInterrupt,
    ) as error:
        cleanup_unconfirmed = isinstance(error, processes.CleanupError)
        if isinstance(error, KeyboardInterrupt):
            error = Pending("interrupted; resume to reconcile the actual remote state")
        result.error(
            "release_cleanup_unconfirmed"
            if cleanup_unconfirmed
            else "release_pending"
            if isinstance(error, Pending)
            else "release_error",
            error,
        )
        if cleanup_unconfirmed:
            result.data["process_cleanup"] = "unconfirmed"
            result.next_action = None
        if state is not None and state_path is not None:
            state["error"] = str(error)
            if cleanup_unconfirmed:
                state["process_cleanup"] = "unconfirmed"
                state["cleanup"] = "retained-process-cleanup-unconfirmed"
            if state["verification"] == "running":
                state["verification"] = "failed"
            if (stage := state.get("stage")) and state.get("stages", {}).get(stage) == "running":
                state["stages"][stage] = "pending" if isinstance(error, Pending) else "failed"
            if workspace is not None and not cleanup_unconfirmed:
                # Only known unchanged scratch bytes are disposable. Keep receipt/log
                # and any changed/unknown files, never recursively sweep a directory.
                try:
                    state["cleanup"] = (
                        "passed" if workspace.cleanup() else "retained-unowned-or-changed-files"
                    )
                except (OSError, storage.StorageError):
                    state["cleanup"] = "blocked-unsafe-path"
            try:
                _save(state_path, state)
            except (OSError, storage.StorageError):
                pass
            print(
                f"relkit release: {feedback.summary(state)}; {error}",
                file=sys.stderr,
            )
            print(
                f"relkit release: diagnostics: {state_path.parent}",
                file=sys.stderr,
            )
            if state.get("temporary") and state.get("cleanup") != "passed":
                print(
                    f"relkit release: retained scratch files: {state['temporary']}", file=sys.stderr
                )
            record_result(result, state, state_path)
            if result.next_action:
                print(
                    "relkit release: next: " + subprocess.list2cmdline(result.next_action),
                    file=sys.stderr,
                )
            if state.get("ci_problems"):
                print(
                    "relkit release: review the failed CI run before resuming; a newer attempt requires --accept-ci-attempt N",
                    file=sys.stderr,
                )
        else:
            print(f"relkit release: {error}", file=sys.stderr)
        return 3 if isinstance(error, Pending) else 2 if state is None else 1
    finally:
        if action == "status" and runner is not None:
            runner.log = original_log
        if lock is not None:
            try:
                if cleanup_unconfirmed or storage.identity(lock) != lock_identity:
                    raise storage.StorageError("release ownership must be retained")
                storage.checked(lock).unlink()
            except (OSError, storage.StorageError):
                result.warnings.append(
                    {
                        "code": "lock_retained",
                        "path": str(lock),
                        "message": "Verify owned commands and descendants before removing the retained release lock",
                    }
                )
                print(
                    f"relkit release: lock retained; verify owned commands and descendants before recovery: {lock}",
                    file=sys.stderr,
                )
