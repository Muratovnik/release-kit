"""Local candidate lifecycle; hosting adapters deliver the same verified files."""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import UTC, datetime
from uuid import uuid4

from .. import canonical, storage
from . import candidate, coordinator
from .backend import ReleaseError, clean


def _base(value):
    return {key: item for key, item in value.items() if key != "candidate"}


def _read(path):
    path = storage.checked(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ReleaseError("candidate receipt exceeds the read limit")
    return json.loads(path.read_text(encoding="utf-8"))


def ready(runner, value):
    path = candidate.receipt_path(runner.root, value["tag"])
    if not path.exists():
        return None
    state = _read(path)
    if state.get("status") != "passed" or state.get("plan") != value:
        return None
    record = state["candidate"]
    if state.get("plan_sha256") != canonical.fingerprint(value) or state.get(
        "candidate_sha256"
    ) != canonical.fingerprint(record):
        raise ReleaseError("local candidate receipt fingerprint differs")
    return {
        "kind": "local",
        "attempt": state["attempt"],
        "sha256": canonical.fingerprint(record),
        "files": record["files"],
    }


def load(runner, value):
    proof = value.get("candidate") or {}
    attempt = proof.get("attempt", "")
    if proof.get("kind") != "local" or not re.fullmatch(r"[a-f0-9]{32}", attempt):
        raise ReleaseError("no matching local candidate; run release prepare VERSION, then plan")
    parent = candidate.receipt_path(runner.root, value["tag"]).parent
    state = _read(parent / f"{attempt}.json")
    base = _base(value)
    record = state.get("candidate", {})
    if (
        state.get("status") != "passed"
        or state.get("plan") != base
        or state.get("plan_sha256") != canonical.fingerprint(base)
        or state.get("attempt") != attempt
        or canonical.fingerprint(record) != proof["sha256"]
        or record.get("plan_sha256") != canonical.fingerprint(base)
        or record.get("files") != proof["files"]
    ):
        raise ReleaseError("local candidate does not match the reviewed plan")
    directory = storage.inside(runner.root, parent / attempt / "assets")
    if candidate.inventory(value, directory) != proof["files"]:
        raise ReleaseError("local candidate files changed; prepare and review again before tagging")
    return directory


def prepare(runner, store, value, no_download, result):
    attempt = uuid4().hex
    parent = candidate.receipt_path(runner.root, value["tag"]).parent
    path = storage.inside(runner.root, parent / f"{attempt}.json")
    directory = storage.inside(runner.root, parent / attempt / "assets")
    state = {
        "schema": 1,
        "attempt": attempt,
        "plan": value,
        "plan_sha256": canonical.fingerprint(value),
        "status": "running",
        "log": str(path.with_suffix(".log")),
        "started_at": datetime.now(UTC).isoformat(),
    }
    storage.atomic_json(path, state)
    previous_log, previous_temp = runner.log, runner.temporary
    runner.log = path.with_suffix(".log")
    runner.log.touch()
    print(f"relkit release: local preparation {value['tag']}; log: {runner.log}", flush=True)
    try:
        with storage.temporary(runner.root, "candidate-") as workspace:
            runner.temporary = workspace.path / "runtime"
            runner.temporary.mkdir()
            temporary = workspace.path / "project-temp"
            temporary.mkdir()
            try:
                coordinator._local_checks(runner, value, workspace, no_download)
                snapshot = coordinator._snapshot(runner, value, workspace)
                directory.parent.mkdir(parents=True)
                coordinator._commands(
                    runner, value["settings"]["build"], value, snapshot, directory, temporary
                )
                files = candidate.inventory(value, directory)
                coordinator._commands(
                    runner, value["settings"]["smoke"], value, snapshot, directory, temporary
                )
                if candidate.inventory(value, directory) != files:
                    raise ReleaseError("candidate files changed during smoke")
                clean(runner, value["sha"])
                if (
                    coordinator.plan(
                        runner, value["version"], github=store, candidate_receipt=False
                    )
                    != value
                ):
                    raise ReleaseError("release plan changed during preparation")
                record = {
                    "schema": 1,
                    "kind": "local",
                    "attempt": attempt,
                    "plan_sha256": canonical.fingerprint(value),
                    "host": sys.platform,
                    "files": files,
                }
                state.update(
                    status="passed",
                    candidate=record,
                    candidate_sha256=canonical.fingerprint(record),
                )
            finally:
                workspace.remember(runner.temporary)
        storage.atomic_json(path, state)
        storage.atomic_json(candidate.receipt_path(runner.root, value["tag"]), state)
        result.data["candidate"] = {**state, "receipt": str(path), "assets": str(directory)}
        print(
            f"relkit release: prepared {value['tag']} locally; no tag or hosting write; assets: {directory}"
        )
    except BaseException as error:
        state.update(status="failed", error=str(error))
        storage.atomic_json(path, state)
        result.data["candidate"] = {**state, "receipt": str(path)}
        print(
            f"relkit release: preparation failed; retry the same version; receipt: {path}; log: {runner.log}",
            file=sys.stderr,
        )
        raise
    finally:
        runner.log, runner.temporary = previous_log, previous_temp


def _draft(store, state):
    value = state["plan"]
    release = store.release(value["tag"])
    if (
        not release
        or not release["draft"]
        or release["prerelease"]
        or not state.get("draft_intent")
        or release.get("name") != state["draft_title"]
        or release.get("target_commitish") != value["sha"]
        or release["id"] != state.get("release_id", release["id"])
        or (release.get("body") or "").replace("\r\n", "\n").rstrip("\n") != value["notes"]
    ):
        raise ReleaseError("draft is not owned by this exact release plan or its notes changed")
    return release


def publish(runner, store, state, path, workspace, *, verify_only=False):
    value = state["plan"]
    state["ci_verdict"] = "not-required"
    _, existing = coordinator._reconcile(runner, store, state)
    if existing and not existing["draft"]:
        if not state.get("publish_intent"):
            raise ReleaseError("publication was not initiated by this run")
        return existing
    if verify_only:
        raise ReleaseError("verify needs a published release")
    source = load(runner, value)
    directory = workspace.path / "prepared-assets"
    shutil.copytree(source, directory)
    workspace.remember(directory)
    if candidate.inventory(value, directory) != value["candidate"]["files"]:
        raise ReleaseError("candidate changed while copying for publication")
    if value["settings"]["publisher"] == "directory":
        state["publish_intent"] = True
        coordinator._save(path, state)
        release = store.export(value, directory, state["tag_oid"])
    else:
        store.preflight()
        if existing is None:
            state["draft_intent"] = True
            state["draft_title"] = f"{value['tag']} [relkit:{state['plan_sha256']}]"
            coordinator._save(path, state)
            notes = workspace.path / "release-notes.md"
            notes.write_text(value["notes"], encoding="utf-8", newline="\n")
            workspace.remember(notes)
            store.create_draft(value["tag"], value["sha"], state["draft_title"], notes)
        release = _draft(store, state)
        state["release_id"] = release["id"]
        state["publication"] = "draft"
        coordinator._save(path, state)
        expected = {item["name"]: item for item in value["candidate"]["files"]}
        for name in expected:
            _draft(store, state)
            observed = store.assets(release["id"])
            # Partial drafts may only contain already verified members. A changed
            # file or an extra member is a conflict, never a reason to clobber.
            known = coordinator.asset_set(observed, [item["name"] for item in observed])
            for item in known:
                if {key: item[key] for key in ("name", "size", "digest")} != expected.get(
                    item["name"]
                ):
                    raise ReleaseError(
                        "draft contains unexpected or changed files; nothing was overwritten"
                    )
            if name not in {item["name"] for item in known}:
                store.upload(value["tag"], directory / name)
        coordinator._reconcile(runner, store, state)
        _draft(store, state)
        actual = coordinator.asset_set(store.assets(release["id"]), value["assets"])
        if [{key: item[key] for key in ("name", "size", "digest")} for item in actual] != value[
            "candidate"
        ]["files"]:
            raise ReleaseError("complete draft differs from the prepared candidate")
        state["publish_intent"] = True
        coordinator._save(path, state)
        store.publish(value["tag"])
        release = store.release(value["tag"])
    if not release or release["draft"]:
        raise ReleaseError("publication has not completed; resume to reconcile")
    state["publication"] = "published"
    state["release_id"] = release["id"]
    coordinator._save(path, state)
    return release
