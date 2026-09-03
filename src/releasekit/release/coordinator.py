"""Resumable GitHub/tag publication: CI publishes; this command plans and verifies."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import time
import tomllib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .. import __version__, config, owner, protection, publication, storage
from ..result import Result
from . import changelog, settings
from .backend import (
    CommandError,
    GitHub,
    Pending,
    ReleaseError,
    Runner,
    ancestor,
    clean,
    local_tags,
    remote_identity,
    remote_refs,
    repository,
)


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
    local = local_tags(runner)
    remote = {
        ref: oid
        for ref, oid in refs.items()
        if ref.startswith("refs/tags/") and not ref.endswith("^{}")
    }
    local.pop(f"refs/tags/{target}", None)
    remote.pop(f"refs/tags/{target}", None)
    if local != remote:
        raise ReleaseError(
            "local/remote tags disagree; review and fetch explicitly (no automatic repair)"
        )
    candidates = []
    current = tuple(map(int, version.split(".")))
    for ref, oid in local.items():
        if not re.fullmatch(r"refs/tags/v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", ref):
            continue
        tag = ref.removeprefix("refs/tags/")
        number = tuple(map(int, tag[1:].split(".")))
        commit = runner.git("rev-parse", f"{ref}^{{commit}}")
        if not ancestor(runner, commit, sha):
            raise ReleaseError(
                f"stable tag {tag} is outside this release ancestry; explicit reconciliation is required"
            )
        if number >= current:
            raise ReleaseError(f"version must advance the existing stable tag {tag}")
        candidates.append((number, {"tag": tag, "oid": oid, "sha": commit}))
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
    if previous and urls != [expected]:
        raise ReleaseError(f"changelog heading must compare the actual Git boundary: {expected}")
    if not previous and urls and urls != [expected]:
        raise ReleaseError("first release heading points to the wrong tag/repository")
    section = ""
    for line in lines[1:]:
        if line.startswith("### "):
            section = line[4:].strip()
        if section in changelog._EDITORIAL:
            continue
        for match in changelog._COMMIT_LINK.finditer(line):
            parts = urlsplit(match[2])
            path = unquote(parts.path)
            target = re.fullmatch(
                rf"/{re.escape(release.repository)}/commit/([a-fA-F0-9]{{7,64}})", path
            )
            if (
                parts.scheme != "https"
                or parts.netloc != "github.com"
                or parts.query
                or parts.fragment
                or not target
            ):
                raise ReleaseError("changelog commit link must belong to the configured repository")
            commit = runner.git("rev-parse", "--verify", f"{target[1]}^{{commit}}")
            if not commit.startswith(match[1].lower()) or not ancestor(runner, commit, sha):
                raise ReleaseError("changelog references a commit outside the release")
            if previous and ancestor(runner, commit, previous["sha"]):
                raise ReleaseError("changelog change was already included in the previous release")
    return entry.replace("\r\n", "\n").rstrip("\n")


def plan(runner: Runner, value: str, *, github: GitHub | None = None) -> dict:
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
    source(runner, sha, release.workflow)
    refs = remote_refs(runner, release.remote)
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
    previous = previous_tag(runner, refs, version, sha, policy.changelog.first_version, tag)
    notes = notes_for(runner, policy, release, version, tag, sha, previous)
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
    pushes.append(f"refs/tags/{tag}:refs/tags/{tag}")
    github = github or GitHub(runner, release.repository)
    identity = github.api()
    if identity["full_name"].casefold() != release.repository.casefold():
        raise ReleaseError("GitHub redirected the repository; review its current identity")
    if github.release(tag) is not None:
        raise ReleaseError("target release/draft already exists without an owned run")
    workflow = github.api(
        f"/actions/workflows/{quote(release.workflow.rsplit('/', 1)[-1], safe='')}"
    )
    if workflow["path"] != release.workflow or workflow["state"] != "active":
        raise ReleaseError("configured release workflow is not active at the expected path")
    gh_version = runner.call(["gh", "--version"])
    match = re.search(r"gh version (\d+)\.(\d+)\.(\d+)", gh_version)
    if not match or tuple(map(int, match.groups())) < (2, 98, 0):
        raise ReleaseError(
            "release verification requires GitHub CLI >= 2.98.0; update it explicitly"
        )
    assets = [settings.filename(item.format(version=version, tag=tag)) for item in release.assets]
    if len({name.casefold() for name in assets}) != len(assets):
        raise ReleaseError("release assets collide after template expansion or case folding")
    checksum_file = release.checksum_file.format(version=version, tag=tag)
    if checksum_file and checksum_file not in assets:
        raise ReleaseError("checksum_file must be included in the exact asset set")
    return {
        "schema": 1,
        "tool_version": __version__,
        "root": str(runner.root),
        "repository_id": identity["id"],
        "settings": asdict(release),
        "sha": sha,
        "version": version,
        "tag": tag,
        "previous": previous,
        "workflow_id": workflow["id"],
        "notes": notes,
        "assets": assets,
        "checksum_file": checksum_file,
        "pushes": pushes,
        "source_config_sha256": hashlib.sha256(
            source(runner, sha, config.CONFIG_NAME).encode()
        ).hexdigest(),
    }


def described_plan(value: dict) -> dict:
    return {
        "plan_sha256": fingerprint(value),
        **value,
        "actions": [
            "project checks",
            "audit worktree + history",
            "protect check if configured",
            "annotated tag + tag metadata audit",
            "atomic exact-ref push (needs --publish)",
            "observe tag CI; CI alone publishes",
            "verify immutable release, notes, assets, signatures",
            "smoke downloaded files from pinned source",
            "cleanup inventoried temporary files",
        ],
    }


def show_plan(value: dict) -> None:
    print(json.dumps(described_plan(value), indent=2, sort_keys=True))


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
        **{
            key: state.get(key)
            for key in (
                "publication",
                "verification",
                "cleanup",
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
            )
        },
    }
    if state["verification"] != "passed":
        result.next_action = [
            "relkit",
            "release",
            "resume",
            value["tag"],
            "--publish",
            "--root",
            value["root"],
        ]


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
        asdict(release) != value["settings"]
        or hashlib.sha256(raw_text.encode()).hexdigest() != value["source_config_sha256"]
    ):
        raise ReleaseError("saved release settings differ from the pinned committed configuration")
    expected = [f"refs/tags/{value['tag']}:refs/tags/{value['tag']}"]
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
    return f"Release {value['version']}\n\nRelease plan SHA-256: {fingerprint(value)}"


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
    remote_identity(runner, release.remote, release.repository)
    identity = github.api()
    if (
        identity["id"] != value["repository_id"]
        or identity["full_name"].casefold() != release.repository.casefold()
    ):
        raise ReleaseError("repository identity changed since planning")
    refs = remote_refs(runner, release.remote)
    if previous := value["previous"]:
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
    pushed = ref in refs
    if pushed and (
        not state.get("push_started")
        or refs[ref] != tag_oid
        or refs.get(ref + "^{}") != value["sha"]
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
    published = github.release(value["tag"])
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
    clean(runner, value["sha"])
    if release.require_guard and (problem := protection.problem(runner.root)):
        raise ReleaseError(f"protect check failed: {problem}")
    # No reuse of local command success across invocations: ignored dependencies,
    # tools, environment and hooks may have changed even if HEAD did not.
    print("relkit release: local checks", flush=True)
    _stage(path, state, "local-checks")
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
    _stage(path, state, "local-checks", "passed")
    _stage(path, state, "worktree-audit")
    _audit(runner, release, history=False, no_download=no_download)
    clean(runner, value["sha"])
    _stage(path, state, "worktree-audit", "passed")
    _stage(path, state, "history-audit")
    _audit(runner, release, history=True, no_download=no_download)
    clean(runner, value["sha"])
    _stage(path, state, "history-audit", "passed")
    refs = remote_refs(runner, release.remote)
    policy = config.load(runner.root)
    if (
        asdict(policy.release) != value["settings"]
        or previous_tag(
            runner,
            refs,
            value["version"],
            value["sha"],
            policy.changelog.first_version,
            value["tag"],
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
) -> dict:
    value = state["plan"]
    print("relkit release: waiting for the planned tag workflow", flush=True)
    _stage(path, state, "ci")
    while True:
        pushed, published = _reconcile(runner, github, state)
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
                if run["conclusion"] != "success":
                    raise ReleaseError(
                        f"CI failed ({run['conclusion']}); release publication is {state['publication']}"
                    )
                jobs = github.jobs(run["id"], run["run_attempt"])
                for name in value["settings"]["required_jobs"]:
                    selected = [job for job in jobs if job["name"] == name]
                    if (
                        len(selected) != 1
                        or selected[0]["status"] != "completed"
                        or selected[0]["conclusion"] != "success"
                        or selected[0]["head_sha"] != value["sha"]
                    ):
                        raise ReleaseError(f"required CI job did not pass exactly once: {name}")
                if published and not published["draft"]:
                    _stage(path, state, "ci", "passed")
                    return published
                if published:
                    raise Pending(
                        "CI finished but left a draft; the CI owner must resolve it, no duplicate release will be created"
                    )
        _save(path, state)
        if time.monotonic() >= deadline:
            raise Pending("tag CI/publication is still pending")
        time.sleep(min(5, max(0, deadline - time.monotonic())))


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


def _published_identity(github: GitHub, value: dict, release: dict) -> dict:
    if release["draft"] or release["prerelease"] or release.get("immutable") is not True:
        raise ReleaseError("release must be published, stable and immutable")
    if release["tag_name"] != value["tag"]:
        raise ReleaseError("published release names another tag")
    if (release.get("body") or "").replace("\r\n", "\n").rstrip("\n") != value["notes"]:
        raise ReleaseError("published notes differ from the committed changelog entry")
    assets = sorted(
        (_asset_identity(asset) for asset in github.assets(release["id"])), key=lambda a: a["name"]
    )
    if [asset["name"] for asset in assets] != sorted(value["assets"]):
        raise ReleaseError("published assets differ from the planned exact file set")
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


def _snapshot(runner: Runner, value: dict, workspace: storage.Workspace) -> Path:
    snapshot = workspace.path / "source"
    snapshot.mkdir()
    for mode, oid, name in source_tree(runner, value["sha"]):
        target = storage.inside(snapshot, snapshot / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Bypass archive attributes and checkout filters: smoke reads exact blobs.
        runner.call(["git", "cat-file", "blob", oid], binary=True, output=target)
        target.chmod(0o755 if mode == "100755" else 0o644)
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
    print("relkit release: verifying publication and signatures", flush=True)
    state["verification"] = "running"
    _stage(path, state, "publication-verification")
    identity = _published_identity(github, value, release)
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
    github.signatures(
        value["tag"],
        value["sha"],
        value["settings"]["workflow"],
        [directory / asset["name"] for asset in identity["assets"]],
        ci=state["ci"],
        repository_id=value["repository_id"],
    )
    workspace.remember(workspace.path / "runtime")
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
    if latest is None or _published_identity(github, value, latest) != identity:
        raise ReleaseError(
            "publication changed during verification (notes remain editable even on immutable releases)"
        )
    state["verification"] = "passed"
    state["verified_at"] = datetime.now(UTC).isoformat()
    state["smoke_platform"] = sys.platform
    _stage(path, state, "application-smoke", "passed")


def run(
    root: Path,
    action: str,
    version: str,
    *,
    publish: bool = False,
    plan_hash: str = "",
    no_download: bool = False,
    accept_ci_attempt: int = 0,
    runner: Runner | None = None,
    github: GitHub | None = None,
    result: Result | None = None,
) -> int:
    result = result or Result()
    original_log = runner.log if runner is not None else None
    state = None
    state_path = None
    lock = None
    lock_owned = False
    workspace = None
    try:
        root = storage.checked(root)
        runner = runner or Runner(root)
        if action == "status":
            runner.log = None
        repository(runner)
        _, tag = version_tag(version)
        if accept_ci_attempt and (action != "resume" or accept_ci_attempt < 1):
            raise ReleaseError("--accept-ci-attempt is a positive explicit resume-only choice")
        if action == "plan":
            value = plan(runner, version, github=github)
            result.data["plan"] = described_plan(value)
            show_plan(value)
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
                f"relkit release: recorded {tag}: publication={saved['publication']}, verification={saved['verification']}, cleanup={saved['cleanup']}; no remote check performed"
            )
            print(f"relkit release: receipt: {path}")
            return 0
        if action not in {"run", "resume"}:
            raise ReleaseError("unknown release action")
        if not publish:
            raise ReleaseError(
                "run/resume require --publish after reviewing release plan; plan is read-only"
            )
        lock = storage.service_root(root) / "release.lock"
        storage.checked(lock).parent.mkdir(parents=True, exist_ok=True)
        try:
            with lock.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps({"pid": os.getpid(), "tag": tag, "root": str(root)}))
        except FileExistsError as error:
            raise ReleaseError(
                f"another release owns {lock}; after a crash verify its PID is stopped before manually removing this exact lock"
            ) from error
        lock_owned = True
        state_path = storage.checked(_state_path(root, tag))
        if action == "resume":
            saved = read_state(runner, state_path, tag, same_version=True)
            value = saved["plan"]
            state = saved
            validate_saved_plan(runner, value)
        else:
            if state_path.exists():
                raise ReleaseError(f"a run for {tag} is already recorded; use release resume")
            value = plan(runner, version, github=github)
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
        if plan_hash and plan_hash != fingerprint(value):
            raise ReleaseError("supplied plan hash differs from the saved plan")
        if sys.platform not in value["settings"]["smoke_platforms"]:
            raise ReleaseError("resume host is not declared for the downloaded-application smoke")
        runner.log = state_path.parent / "run.log"
        storage.checked(runner.log).touch(exist_ok=True)
        runner.log.chmod(0o600)
        github = github or GitHub(runner, value["settings"]["repository"])
        state["verification"] = "not-run"
        # Reconciliation precedes project commands, downloads, retries and cleanup.
        pushed, _ = _reconcile(runner, github, state)
        _save(state_path, state)
        workspace = storage.Workspace(root, "release-")
        runner.temporary = workspace.path / "runtime"
        runner.temporary.mkdir()
        workspace.remember(runner.temporary)
        (workspace.path / "project-temp").mkdir()
        workspace.remember(workspace.path / "project-temp")
        if old_temporary := state.get("temporary"):
            old_path = storage.inside(root, Path(old_temporary))
            if old_path.exists() and old_temporary not in state.setdefault(
                "retained_temporaries", []
            ):
                state["retained_temporaries"].append(old_temporary)
        state["temporary"] = str(workspace.path)
        _save(state_path, state)
        if not pushed:
            _prepare(runner, github, state, state_path, workspace, no_download)
        deadline = time.monotonic() + value["settings"]["timeout"]
        published = _ci(runner, github, state, state_path, deadline, accept_ci_attempt)
        _verify(runner, github, state, state_path, published, workspace)
        cleaned = workspace.cleanup()
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
        if state["local_changes"]:
            print(
                "relkit release: new local changes are separate from the published result; left untouched"
            )
        if not cleaned:
            print(f"relkit release: cleanup retained changed/unowned files: {state['temporary']}")
        return 0
    except (
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
        if isinstance(error, KeyboardInterrupt):
            error = Pending("interrupted; resume to reconcile the actual remote state")
        result.error("release_pending" if isinstance(error, Pending) else "release_error", error)
        if state is not None and state_path is not None:
            state["error"] = str(error)
            if state["verification"] == "running":
                state["verification"] = "failed"
            if (stage := state.get("stage")) and state.get("stages", {}).get(stage) == "running":
                state["stages"][stage] = "pending" if isinstance(error, Pending) else "failed"
            if workspace is not None:
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
                f"relkit release: publication={state['publication']}, verification={state['verification']}; {error}",
                file=sys.stderr,
            )
            print(
                f'relkit release: diagnostics: {state_path.parent}; resume: relkit release resume {tag} --publish --root "{root}"',
                file=sys.stderr,
            )
            if state.get("temporary") and state.get("cleanup") != "passed":
                print(
                    f"relkit release: retained scratch files: {state['temporary']}", file=sys.stderr
                )
            record_result(result, state, state_path)
        else:
            print(f"relkit release: {error}", file=sys.stderr)
        return 3 if isinstance(error, Pending) else 2 if state is None else 1
    finally:
        if action == "status" and runner is not None:
            runner.log = original_log
        if lock_owned and lock is not None:
            try:
                storage.checked(lock).unlink()
            except (OSError, storage.StorageError):
                result.warnings.append(
                    {
                        "code": "lock_retained",
                        "path": str(lock),
                        "message": "Inspect the retained release lock",
                    }
                )
                print(
                    f"relkit release: lock changed or could not be removed; inspect {lock}",
                    file=sys.stderr,
                )
