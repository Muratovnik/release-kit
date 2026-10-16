"""Local candidate lifecycle; hosting adapters deliver the same verified files."""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import UTC, datetime
from uuid import uuid4

from .. import canonical, processes, semver, storage
from . import candidate, coordinator, stagelog
from .backend import ReleaseError, clean


def _base(value):
    return {key: item for key, item in value.items() if key != "candidate"}


def _read(path):
    path = storage.checked(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ReleaseError(
            f"candidate receipt {path} exceeds the read limit; delete it and run "
            "`relkit release prepare VERSION` again"
        )
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
        raise ReleaseError(
            f"local candidate receipt {path} fingerprint differs from its plan; do not edit "
            f"receipts, run `relkit release prepare {value['tag']}` again"
        )
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
        raise ReleaseError(
            f"no matching local candidate for {value['tag']}; run "
            f"`relkit release prepare {value['tag']}`, then plan"
        )
    parent = candidate.receipt_path(runner.root, value["tag"]).parent
    receipt = parent / f"{attempt}.json"
    state = _read(receipt)
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
        raise ReleaseError(
            f"local candidate {receipt} does not match the reviewed plan for {value['tag']}; "
            f"run `relkit release prepare {value['tag']}`, then plan again"
        )
    directory = storage.inside(runner.root, parent / attempt / "assets")
    if candidate.inventory(value, directory) != proof["files"]:
        raise ReleaseError(
            f"local candidate files changed under {directory}; run "
            f"`relkit release prepare {value['tag']}` and review the plan again before tagging"
        )
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
            state["temporary"] = str(workspace.path)
            storage.atomic_json(path, state)
            runner.temporary = workspace.path / "runtime"
            runner.temporary.mkdir()
            temporary = workspace.path / "project-temp"
            temporary.mkdir()
            workspace.remember(temporary)
            workspace.scratch(temporary)
            try:
                coordinator._local_checks(runner, value, workspace, no_download)
                snapshot = coordinator._snapshot(runner, value, workspace)
                directory.parent.mkdir(parents=True)
                if value["settings"]["build"]:
                    coordinator._commands(
                        runner,
                        value["settings"]["build"],
                        value,
                        snapshot,
                        directory,
                        temporary,
                        stage="build",
                    )
                else:
                    # A release without files: the inventory below still records
                    # that this exact, empty set is what gets published.
                    directory.mkdir()
                files = candidate.inventory(value, directory)
                coordinator._commands(
                    runner,
                    value["settings"]["smoke"],
                    value,
                    snapshot,
                    directory,
                    temporary,
                    stage="candidate-smoke",
                )
                if candidate.inventory(value, directory) != files:
                    raise ReleaseError("candidate files changed during smoke")
                clean(runner, value["sha"])
                with stagelog.stage(runner, "preflight"):
                    replanned = coordinator.plan(
                        runner, value["version"], github=store, candidate_receipt=False
                    )
                if replanned != value:
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
            except processes.CleanupError:
                state["process_cleanup"] = "unconfirmed"
                raise
            finally:
                if state.get("process_cleanup") != "unconfirmed":
                    workspace.remember(runner.temporary)
        storage.atomic_json(path, state)
        storage.atomic_json(candidate.receipt_path(runner.root, value["tag"]), state)
        result.data["candidate"] = {**state, "receipt": str(path), "assets": str(directory)}
        print(
            f"relkit release: prepared {value['tag']} locally; no tag or hosting write; assets: {directory}"
        )
    except BaseException as error:
        unconfirmed = isinstance(error, processes.CleanupError)
        state.update(status="cleanup-unconfirmed" if unconfirmed else "failed", error=str(error))
        storage.atomic_json(path, state)
        result.data["candidate"] = {**state, "receipt": str(path)}
        print(
            "relkit release: preparation failed; "
            + ("verify owned commands before recovery" if unconfirmed else "retry the same version")
            + f"; receipt: {path}; log: {runner.log}",
            file=sys.stderr,
        )
        raise
    finally:
        runner.log, runner.temporary = previous_log, previous_temp


def _draft(store, state):
    value = state["plan"]
    identifier = state.get("release_id")
    release = store.release_by_id(identifier) if identifier else store.release(value["tag"])
    if (
        not release
        or release.get("tag_name") != value["tag"]
        or not release["draft"]
        or release["prerelease"] != semver.parse(value["version"]).is_prerelease
        or not state.get("draft_intent")
        or release.get("name") != state["draft_title"]
        or release.get("target_commitish") != value["sha"]
        or release["id"] != state.get("release_id", release["id"])
        or (release.get("body") or "").replace("\r\n", "\n").rstrip("\n") != value["notes"]
    ):
        raise ReleaseError(
            f"the draft for {value['tag']} is not owned by this exact release plan or its "
            "notes changed; inspect it on the hosting side, nothing was overwritten"
        )
    return release


def publish(runner, store, state, path, workspace, *, verify_only=False):
    value = state["plan"]
    state["ci_verdict"] = "not-required"
    _, existing = coordinator._reconcile(runner, store, state)
    if existing and not existing["draft"]:
        if not state.get("publish_intent"):
            raise ReleaseError(
                f"publication of {value['tag']} was not initiated by this run; inspect it before "
                "resuming, release verify only checks a publication this run made"
            )
        return existing
    if verify_only:
        raise ReleaseError(
            f"{value['tag']} has no published release to verify; publish it first with "
            f"`relkit release run {value['tag']} --publish`"
        )
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
            created = store.create_draft(
                value["tag"],
                value["sha"],
                state["draft_title"],
                notes,
                tag_oid=state["tag_oid"],
            )
            state["release_id"] = created["id"]
            coordinator._save(path, state)
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
        raise ReleaseError(
            f"publication of {value['tag']} has not completed; run "
            f"`relkit release resume {value['tag']} --publish` to reconcile"
        )
    state["publication"] = "published"
    state["release_id"] = release["id"]
    coordinator._save(path, state)
    return release
