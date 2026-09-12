"""Published stable versions and occupied refs are independent observations."""

from __future__ import annotations

import json
import re

from .. import canonical, storage
from .backend import ReleaseError, local_tags, merged_tags, remote_refs, tag_commits

STABLE = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def published(runner, github, refs, sha):
    releases = [
        release
        for release in github.releases()
        if not release["draft"] and not release["prerelease"]
    ]
    candidates = []
    observed = {(release["tag_name"], release["id"]) for release in releases}
    receipts = storage.service_root(runner.root) / "releases"
    current = github.identity()
    if receipts.exists():
        for path in storage.checked(receipts).glob("*/state.json"):
            path = storage.inside(runner.root, path)
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ReleaseError("saved release state exceeds the read limit")
            state = json.loads(path.read_text(encoding="utf-8"))
            plan = state.get("plan", {})
            if plan.get("root") != str(runner.root) or state.get(
                "plan_sha256"
            ) != canonical.fingerprint(plan):
                raise ReleaseError("invalid local publication receipt; reconcile it explicitly")
            # Receipts from another destination are not that store's history.
            if "repository_id" in plan and plan["repository_id"] != current["id"]:
                continue
            known = []
            if state.get("publication") == "published":
                known.append((plan["tag"], state["release_id"]))
            if (previous := plan.get("previous")) and "release_id" in previous:
                known.append((previous["tag"], previous["release_id"]))
            if any(item not in observed for item in known):
                raise ReleaseError(
                    "previously recorded published release disappeared or changed identity; reconcile explicitly"
                )
    local = local_tags(runner)
    commits = tag_commits(runner)
    reachable = merged_tags(runner, sha)
    seen = set()
    for release in releases:
        tag = release["tag_name"]
        match = STABLE.fullmatch(tag)
        if not match:
            continue
        if tag in seen:
            raise ReleaseError(f"multiple published releases claim {tag}")
        seen.add(tag)
        ref = f"refs/tags/{tag}"
        if not refs.get(ref) or local.get(ref) != refs[ref]:
            raise ReleaseError(
                f"published tag {tag} is missing or differs locally/remotely; review and fetch explicitly"
            )
        if ref not in reachable:
            raise ReleaseError(
                f"published tag {tag} is outside this release ancestry; reconcile release lines explicitly"
            )
        candidates.append(
            (
                tuple(map(int, match.groups())),
                {
                    "tag": tag,
                    "oid": refs[ref],
                    "sha": commits[ref],
                    "release_id": release["id"],
                },
            )
        )
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def predecessor(runner, github, refs, version, sha, first):
    previous = published(runner, github, refs, sha)
    if previous:
        if tuple(map(int, version.split("."))) <= tuple(map(int, previous["tag"][1:].split("."))):
            raise ReleaseError(f"version must advance published release {previous['tag']}")
    elif first.removeprefix("v") != version:
        raise ReleaseError(
            "no published stable release: declare changelog.first_version explicitly"
        )
    return previous


def next_version(runner, github, release, first, bump):
    if bump not in {"patch", "minor", "major"}:
        raise ReleaseError("release next requires --bump patch|minor|major")
    refs = (
        local_tags(runner)
        if release.publisher == "directory"
        else remote_refs(runner, release.remote)
    )
    previous = published(runner, github, refs, runner.git("rev-parse", "HEAD"))
    if previous:
        number = list(map(int, previous["tag"][1:].split(".")))
        index = {"major": 0, "minor": 1, "patch": 2}[bump]
        number[index] += 1
        number[index + 1 :] = [0] * (2 - index)
        version = ".".join(map(str, number))
    else:
        version = first.removeprefix("v")
        if not STABLE.fullmatch("v" + version):
            raise ReleaseError(
                "no published stable release: declare changelog.first_version explicitly"
            )
    tag = "v" + version
    local = local_tags(runner)
    occupied = f"refs/tags/{tag}" in refs or f"refs/tags/{tag}" in local
    return {
        "version": version,
        "tag": tag,
        "previous": previous,
        "bump": bump,
        "tag_state": "occupied" if occupied else "available",
    }


def check_carried_changes(runner, text, notes, version, previous):
    """Require curated commit links from skipped candidates in the new entry."""
    from . import changelog
    from .backend import ancestor

    current = tuple(map(int, version.split(".")))
    floor = tuple(map(int, previous["tag"][1:].split("."))) if previous else (-1, -1, -1)
    included = {
        runner.git("rev-parse", f"{match[1]}^{{commit}}")
        for match in changelog._COMMIT_LINK.finditer("\n".join(changelog._visible_lines(notes)))
    }
    skipped = False
    for line in changelog._visible_lines(text):
        if heading := changelog._HEADING.match(line):
            match = STABLE.fullmatch("v" + changelog.normalize(heading[1] or heading[2]))
            skipped = bool(match and floor < tuple(map(int, match.groups())) < current)
        if not skipped:
            continue
        for match in changelog._COMMIT_LINK.finditer(line):
            commit = runner.git("rev-parse", f"{match[1]}^{{commit}}")
            if commit not in included and not (
                previous and ancestor(runner, commit, previous["sha"])
            ):
                raise ReleaseError(
                    f"changelog omits change {match[1]} from an unpublished candidate; carry it into {version}"
                )
