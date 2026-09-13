"""Tagless CI candidates; publication consumes the exact reviewed artifact bytes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from uuid import uuid4

from .. import canonical, config, processes, storage
from . import settings
from .backend import Pending, ReleaseError, clean

MANIFEST = "relkit-candidate.json"


def inputs(runner, version):
    from .coordinator import source, version_tag

    version, tag = version_tag(version)
    release = config.load(runner.root).release
    if release is None or not release.candidate_jobs:
        raise ReleaseError("configure release.candidate_jobs and a tagless release workflow first")
    sha = runner.git("rev-parse", "HEAD")
    clean(runner, sha)
    observed = re.findall(
        release.version_pattern,
        source(runner, sha, release.version_file).replace("\r\n", "\n"),
        re.MULTILINE,
    )
    if observed not in ([version], [tag]):
        raise ReleaseError("candidate version differs from the committed version_file")
    assets = [settings.filename(name.format(version=version, tag=tag)) for name in release.assets]
    if MANIFEST in assets or len({name.casefold() for name in assets}) != len(assets):
        raise ReleaseError("candidate assets collide with each other or the candidate manifest")
    return {
        "schema": 1,
        "sha": sha,
        "version": version,
        "tag": tag,
        "repository": release.repository,
        "workflow": release.workflow,
        "workflow_sha256": hashlib.sha256(
            source(runner, sha, release.workflow).encode()
        ).hexdigest(),
        "assets": assets,
        "checksum_file": release.checksum_file.format(version=version, tag=tag),
    }, release


def inventory(value, directory, *, manifest=False):
    from .coordinator import _checksums

    directory = storage.checked(directory)
    expected = set(value["assets"]) | ({MANIFEST} if manifest else set())
    if {path.name for path in directory.iterdir()} != expected:
        raise ReleaseError(
            "candidate/draft directory does not contain the exact configured asset set"
        )
    assets = []
    for name in sorted(value["assets"]):
        path = storage.inside(directory, directory / name)
        if not path.is_file():
            raise ReleaseError(f"release asset is not an ordinary file: {name}")
        assets.append(
            {"name": name, "size": path.stat().st_size, "digest": "sha256:" + storage.digest(path)}
        )
    _checksums(value, directory, assets)
    return assets


def bundle(runner, version, directory):
    value, _ = inputs(runner, version)
    if (
        os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or os.environ.get("GITHUB_SHA") != value["sha"]
        or os.environ.get("GITHUB_REPOSITORY", "").casefold() != value["repository"].casefold()
    ):
        raise ReleaseError("bundle runs only in this commit's workflow_dispatch CI")
    ci = {"id": int(os.environ["GITHUB_RUN_ID"]), "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])}
    if min(ci.values()) < 1:
        raise ReleaseError("invalid candidate CI identity")
    record = {**value, "ci": ci, "files": inventory(value, directory)}
    path = storage.inside(runner.root, directory / MANIFEST)
    if path.exists():
        raise ReleaseError("candidate manifest already exists; use a fresh output directory")
    storage.atomic_json(path, record)
    return record


def ci_identity(github, value, release, run_id):
    from .coordinator import job_matches

    run = github.api(f"/actions/runs/{run_id}")
    repository = github.api()
    if (
        run["id"] != run_id
        or run["event"] != "workflow_dispatch"
        or run["head_sha"] != value["sha"]
        or run["path"] != release.workflow
        or run["repository"]["id"] != repository["id"]
        or repository["full_name"].casefold() != release.repository.casefold()
    ):
        raise ReleaseError(
            "candidate CI must be the configured workflow_dispatch in this repository at the exact source SHA"
        )
    if run["status"] != "completed":
        raise Pending("candidate CI is still running; repeat prepare with the same --ci-run")
    if run["conclusion"] != "success":
        raise ReleaseError(
            "candidate CI failed; repair and retry the same version before creating a tag"
        )
    jobs = github.jobs(run_id, run["run_attempt"])
    for required in release.candidate_jobs:
        found = [job for job in jobs if job_matches(job["name"], required)]
        if not found or any(
            job["head_sha"] != value["sha"]
            or job["status"] != "completed"
            or job["conclusion"] != "success"
            for job in found
        ):
            raise ReleaseError(f"candidate required job did not pass: {required}")
    return {"id": run_id, "attempt": run["run_attempt"]}


def download(runner, github, value, release, run_id, directory):
    ci = ci_identity(github, value, release, run_id)
    directory = storage.inside(runner.root, directory)
    if directory.exists() and any(directory.iterdir()):
        raise ReleaseError("candidate download directory must be empty")
    directory.mkdir(parents=True, exist_ok=True)
    runner.call(
        [
            "gh",
            "run",
            "download",
            str(run_id),
            "--repo",
            release.repository,
            "--name",
            release.candidate_artifact,
            "--dir",
            str(directory),
        ],
        timeout=release.command_timeout,
    )
    path = storage.inside(directory, directory / MANIFEST)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ReleaseError("candidate manifest exceeds the read limit")
    record = json.loads(path.read_text(encoding="utf-8"))
    expected = {**value, "ci": ci, "files": inventory(value, directory, manifest=True)}
    if record != expected:
        raise ReleaseError(
            "candidate manifest differs from source, version, workflow, CI attempt or asset digests"
        )
    if ci_identity(github, value, release, run_id) != ci:
        raise ReleaseError("candidate CI attempt changed during download")
    return record


def receipt_path(root, tag):
    return storage.service_root(root) / "candidates" / tag / "ready.json"


def prepare(runner, github, value, run_id, no_download, result):
    from . import coordinator

    expected, release = inputs(runner, value["version"])
    if run_id < 1:
        raise ReleaseError(
            "prepare requires --ci-run ID from the tagless release workflow; run gh workflow run with version input first"
        )
    attempt = uuid4().hex
    path = receipt_path(runner.root, value["tag"]).parent / f"{attempt}.json"
    state = {
        "schema": 1,
        "attempt": attempt,
        "plan": value,
        "plan_sha256": canonical.fingerprint(value),
        "status": "running",
        "log": str(path.with_suffix(".log")),
        "ci_run": run_id,
        "started_at": datetime.now(UTC).isoformat(),
    }
    storage.atomic_json(path, state)
    previous_log, previous_temp = runner.log, runner.temporary
    runner.log = path.with_suffix(".log")
    try:
        with storage.temporary(runner.root, "candidate-") as workspace:
            state["temporary"] = str(workspace.path)
            runner.temporary = workspace.path / "runtime"
            runner.temporary.mkdir()
            temporary = workspace.path / "project-temp"
            temporary.mkdir()
            ci_identity(github, expected, release, run_id)
            coordinator._local_checks(runner, value, workspace, no_download)
            directory = workspace.path / "assets"
            record = download(runner, github, expected, release, run_id, directory)
            workspace.remember(directory)
            snapshot = coordinator._snapshot(runner, value, workspace)
            coordinator._commands(runner, release.smoke, value, snapshot, directory, temporary)
            if inventory(expected, directory, manifest=True) != record["files"]:
                raise ReleaseError("candidate assets changed during smoke")
            clean(runner, value["sha"])
            if (
                coordinator.plan(runner, value["version"], github=github, candidate_receipt=False)
                != value
            ):
                raise ReleaseError("release plan changed during candidate preparation")
            workspace.remember(runner.temporary)
            state.update(
                status="passed",
                candidate=record,
                candidate_sha256=canonical.fingerprint(record),
                host=sys.platform,
            )
        storage.atomic_json(path, state)
        storage.atomic_json(receipt_path(runner.root, value["tag"]), state)
        result.data["candidate"] = {**state, "receipt": str(path)}
        print(
            f"relkit release: candidate {value['tag']} prepared at {value['sha']}; no tag created; receipt: {path}"
        )
    except BaseException as error:
        unconfirmed = isinstance(error, processes.CleanupError)
        state.update(
            status="cleanup-unconfirmed"
            if unconfirmed
            else "pending"
            if isinstance(error, Pending)
            else "failed",
            error=str(error),
        )
        if unconfirmed:
            state["process_cleanup"] = "unconfirmed"
        storage.atomic_json(path, state)
        result.data["candidate"] = {**state, "receipt": str(path)}
        print(
            f"relkit release: candidate {value['tag']} {state['status']}; receipt: {path}; log: {runner.log}",
            file=sys.stderr,
        )
        raise
    finally:
        runner.log, runner.temporary = previous_log, previous_temp


def ready(runner, value):
    path = storage.inside(runner.root, receipt_path(runner.root, value["tag"]))
    if not path.exists():
        return None
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ReleaseError("candidate receipt exceeds the read limit")
    record = json.loads(path.read_text(encoding="utf-8"))
    if (
        record.get("status") != "passed"
        or record.get("plan") != value
        or record.get("plan_sha256") != canonical.fingerprint(value)
    ):
        return None
    candidate = record["candidate"]
    if canonical.fingerprint(candidate) != record["candidate_sha256"]:
        raise ReleaseError("candidate receipt fingerprint differs")
    return {
        "ci": candidate["ci"],
        "sha256": record["candidate_sha256"],
        "files": candidate["files"],
    }


def tag_proof(runner, value):
    ref = f"refs/tags/{value['tag']}"
    if (
        runner.git("cat-file", "-t", ref) != "tag"
        or runner.git("rev-parse", ref + "^{commit}") != value["sha"]
    ):
        raise ReleaseError("promotion requires the annotated stable tag at this source SHA")
    message = runner.git("for-each-ref", "--format=%(contents)", ref)
    matches = re.findall(
        r"^Release candidate: ([1-9]\d*)/([1-9]\d*) ([a-f0-9]{64})$", message, re.MULTILINE
    )
    if len(matches) != 1:
        raise ReleaseError("tag has no unique prepared candidate identity")
    return matches[0]


def promote(runner, github, version, directory):
    value, release = inputs(runner, version)
    matches = [tag_proof(runner, value)]
    run_id, attempt, digest = matches[0]
    record = download(runner, github, value, release, int(run_id), directory)
    if record["ci"]["attempt"] != int(attempt) or canonical.fingerprint(record) != digest:
        raise ReleaseError("candidate no longer matches the reviewed tag annotation")
    storage.atomic_json(receipt_path(runner.root, value["tag"]).with_name("promoted.json"), record)
    storage.checked(directory / MANIFEST).unlink()
    return record


def draft(runner, github, version, directory):
    value, _ = inputs(runner, version)
    files = inventory(value, directory)
    path = storage.inside(
        runner.root, receipt_path(runner.root, value["tag"]).with_name("promoted.json")
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    run_id, attempt, digest = tag_proof(runner, value)
    if (
        record.get("ci") != {"id": int(run_id), "attempt": int(attempt)}
        or canonical.fingerprint(record) != digest
    ):
        raise ReleaseError("promoted candidate differs from the stable tag annotation")
    if record.get("files") != files or any(
        record.get(key) != expected for key, expected in value.items()
    ):
        raise ReleaseError("draft files differ from the promoted candidate")
    release = github.release(value["tag"])
    if (
        not release
        or not release["draft"]
        or release["prerelease"]
        or release["tag_name"] != value["tag"]
    ):
        raise ReleaseError("expected an unpublished stable draft")
    notes = config.load(runner.root).changelog
    from . import changelog
    from .coordinator import source

    expected_notes = changelog.entry_for(
        source(runner, value["sha"], config.load(runner.root).release.changelog),
        value["version"],
        profile="strict" if notes.profile == "legacy" else notes.profile,
        first_version=notes.first_version,
    )
    if (
        release.get("body", "").replace("\r\n", "\n").rstrip()
        != (expected_notes or "").replace("\r\n", "\n").rstrip()
    ):
        raise ReleaseError("draft notes differ from the committed curated entry")
    from .coordinator import asset_set

    remote = asset_set(github.assets(release["id"]), value["assets"])
    if (
        sorted(
            [{key: asset[key] for key in ("name", "size", "digest")} for asset in remote],
            key=lambda item: item["name"],
        )
        != files
    ):
        raise ReleaseError("draft assets differ from the exact local set, sizes or digests")
    return {
        "release_id": release["id"],
        "files": files,
        "publication": "draft",
        "verification": "passed",
    }
