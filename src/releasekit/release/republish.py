"""Republish the notes of a published release from a later committed changelog.

A release's notes start as the changelog entry at its tag commit. A maintainer who
rewrites that entry afterwards commits it and runs `release notes VERSION --publish`:
the hosted body is replaced, read back, and the receipt records a revision naming the
commit the entry came from. `release verify` then expects the entry at that commit,
extracted again from Git, so an unrecorded body change still fails as before.
"""

from __future__ import annotations

import re
import tomllib
from datetime import UTC, datetime

from .. import __version__, config, storage
from . import coordinator, settings, versions
from .backend import Pending, ReleaseError, ancestor, remote_identity

REVISION_KEYS = {"source", "notes_sha256", "previous_notes_sha256", "edited", "at", "tool_version"}
_SHA = re.compile(r"[a-f0-9]{40}|[a-f0-9]{64}")
_DIGEST = re.compile(r"[a-f0-9]{64}")


def check_revisions(state: dict) -> None:
    """Refuse a receipt whose recorded notes revisions are not the shape this writes."""
    revisions = state["notes_revisions"]
    if not isinstance(revisions, list) or not all(
        isinstance(item, dict)
        and set(item) == REVISION_KEYS
        and isinstance(item["source"], str)
        and _SHA.fullmatch(item["source"])
        and isinstance(item["notes_sha256"], str)
        and _DIGEST.fullmatch(item["notes_sha256"])
        and isinstance(item["previous_notes_sha256"], str)
        and _DIGEST.fullmatch(item["previous_notes_sha256"])
        and isinstance(item["edited"], bool)
        and isinstance(item["at"], str)
        and isinstance(item["tool_version"], str)
        for item in revisions
    ):
        raise ReleaseError("malformed saved release state: notes_revisions")


def entry_at(runner, value: dict, commit: str) -> str:
    """The entry for the planned version in the changelog at `commit`, validated.

    The checks are the ones publication applied at the tag: the `[changelog]` profile
    committed at `commit`, the pinned repository and the release's own commit range.
    """
    if not ancestor(runner, value["sha"], commit):
        raise ReleaseError(
            f"{commit[:12]} does not descend from the {value['tag']} commit "
            f"{value['sha'][:12]}; republish notes from a commit that contains the release"
        )
    release = settings.parse(value["settings"])
    raw = tomllib.loads(coordinator.source(runner, commit, config.CONFIG_NAME))
    policy = config.Config(runner.root, changelog=config._changelog(raw), release=release)
    notes = coordinator.notes_for(
        runner,
        policy,
        release,
        value["version"],
        value["tag"],
        value["sha"],
        value["previous"],
        changelog_at=commit,
    )
    versions.check_carried_changes(
        runner,
        coordinator.source(runner, commit, release.changelog),
        notes,
        value["version"],
        value["previous"],
    )
    return notes


def expected_notes(runner, state: dict) -> str:
    """The body a verified publication must carry.

    Without a recorded revision that is the plan's entry, exactly as before. With one,
    the entry at the latest revision's source commit, re-extracted from Git and
    required to match the digest the revision recorded.
    """
    revisions = state.get("notes_revisions") or []
    if not revisions:
        return state["plan"]["notes"]
    latest = revisions[-1]
    notes = entry_at(runner, state["plan"], latest["source"])
    if coordinator.fingerprint(notes) != latest["notes_sha256"]:
        raise ReleaseError(
            f"the changelog entry at {latest['source'][:12]} differs from the notes revision "
            "recorded for it; the receipt and the repository disagree"
        )
    return notes


def same_artifacts(state: dict, recorded: dict, observed: dict) -> bool:
    """Whether an observed identity is the recorded one, up to recorded notes revisions."""
    if recorded == observed:
        return True
    explained = {coordinator.fingerprint(state["plan"]["notes"])} | {
        item["notes_sha256"] for item in state.get("notes_revisions") or []
    }
    return (
        {**recorded, "notes_sha256": None} == {**observed, "notes_sha256": None}
        and recorded.get("notes_sha256") in explained
        and observed.get("notes_sha256") in explained
    )


def _recorded_sha256(state: dict) -> str:
    revisions = state.get("notes_revisions") or []
    return (
        revisions[-1]["notes_sha256"]
        if revisions
        else coordinator.fingerprint(state["plan"]["notes"])
    )


def _published(runner, github, state: dict) -> dict:
    value = state["plan"]
    release = settings.parse(value["settings"])
    remote_identity(runner, release.remote, release.repository)
    identity = github.identity()
    if (
        identity["id"] != value["repository_id"]
        or identity["full_name"].casefold() != release.repository.casefold()
    ):
        raise ReleaseError("repository identity changed since the release was published")
    identifier = state.get("release_id")
    published = github.release_by_id(identifier) if identifier else None
    if (
        published is None
        or published.get("id") != identifier
        or published.get("tag_name") != value["tag"]
        or published.get("draft")
    ):
        raise ReleaseError(
            f"the release recorded for {value['tag']} is no longer published under its "
            "recorded id; inspect it on the hosting side"
        )
    return published


def notes(runner, github, state: dict, path, *, publish: bool) -> dict:
    """Plan, or with `publish` perform, republishing the notes of a published release."""
    value = state["plan"]
    tag = value["tag"]
    if outcome := state.get("outcome"):
        raise ReleaseError(f"{tag} was recorded as {outcome['status']} at {outcome['at']}")
    if state["publication"] != "published":
        raise ReleaseError(
            f"the receipt for {tag} records publication {state['publication']!r}; notes are "
            "republished only for a release this checkout published (finish it with release "
            "resume first)"
        )
    release = settings.parse(value["settings"])
    if release.publisher == "directory":
        raise ReleaseError(
            "the directory publisher never rewrites a publication, notes included; its "
            "manifest identifies the release, so publish a new version instead"
        )
    relative = settings.relative(release.changelog)
    head = runner.git("rev-parse", "HEAD")
    if runner.git(
        "status", "--porcelain=v1", "--untracked-files=all", "--", f":(literal){relative}"
    ):
        raise ReleaseError(
            f"{relative} has uncommitted changes; commit the rewritten entry, then run again"
        )
    entry = entry_at(runner, value, head)
    published = _published(runner, github, state)
    hosted = coordinator.body_text(published)
    entry_sha256 = coordinator.fingerprint(entry)
    data = {
        "tag": tag,
        "release_id": published["id"],
        "source": head,
        "changelog": relative,
        "notes": entry,
        "notes_sha256": entry_sha256,
        "hosted_notes_sha256": coordinator.fingerprint(hosted),
        "differs": hosted != entry,
        "recorded": _recorded_sha256(state) == entry_sha256,
        "edited": False,
        "revision": None,
    }
    if not publish:
        return data
    if hosted != entry:
        with storage.temporary(runner.root, "notes-") as workspace:
            workspace.remember()
            try:
                github.edit_notes(published["id"], entry, workspace.path)
            except Pending as error:
                # The runner's own hint names `release resume`, which does not
                # reconcile a notes edit: this command reads the release back itself.
                raise Pending(
                    f"editing the notes of {tag} timed out and may have reached the "
                    f"host; run `relkit release notes {tag} --publish` again, which reads "
                    "the release back, edits it only if it still differs and records the "
                    "revision"
                ) from error
        data["edited"] = True
    observed = _published(runner, github, state)
    if coordinator.body_text(observed) != entry:
        raise ReleaseError(
            f"the hosted notes of {tag} differ from the committed entry after the edit; "
            "inspect the release, then run release notes again"
        )
    if not data["recorded"]:
        revision = {
            "source": head,
            "notes_sha256": entry_sha256,
            "previous_notes_sha256": data["hosted_notes_sha256"],
            "edited": data["edited"],
            "at": datetime.now(UTC).isoformat(),
            "tool_version": __version__,
        }
        state.setdefault("notes_revisions", []).append(revision)
        coordinator._save(path, state)
        data["revision"] = revision
        data["recorded"] = True
    return data
