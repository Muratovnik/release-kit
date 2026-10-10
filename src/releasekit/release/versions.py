"""Published version boundaries and occupied refs are independent observations."""

from __future__ import annotations

import json
import re

from .. import canonical, semver, storage
from .backend import ReleaseError, local_tags, merged_tags, remote_refs, tag_commits


def _records(runner, github):
    releases = [release for release in github.releases() if not release["draft"]]
    observed = {(release["tag_name"], release["id"]): release for release in releases}
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
            if "repository_id" in plan and plan["repository_id"] != current["id"]:
                continue
            known = []
            if state.get("publication") == "published":
                known.append((plan["tag"], state["release_id"]))
            if (previous := plan.get("previous")) and "release_id" in previous:
                known.append((previous["tag"], previous["release_id"]))
            for tag, identifier in known:
                release = observed.get((tag, identifier))
                if release is None:
                    raise ReleaseError(
                        "previously recorded published release disappeared or changed identity; reconcile explicitly"
                    )
                if release["prerelease"] != semver.parse(tag.removeprefix("v")).is_prerelease:
                    raise ReleaseError(
                        "previously recorded published release changed prerelease status"
                    )
    records = []
    for release in releases:
        tag = release["tag_name"]
        if not tag.startswith("v"):
            continue
        try:
            number = semver.parse(tag[1:])
        except ValueError:
            continue
        # A stable-looking tag can be marked as a preview by an external publisher.
        # It has never been a stable boundary, and supplies no SemVer preview lane.
        if release["prerelease"] and not number.is_prerelease:
            continue
        records.append((number, release))
    return records


def _validated(runner, refs, sha, records, prerelease_core=None):
    local = local_tags(runner)
    commits = tag_commits(runner)
    reachable = merged_tags(runner, sha)
    candidates = []
    seen = set()
    for number, release in records:
        if number.is_prerelease and number.core != prerelease_core:
            continue
        tag = release["tag_name"]
        if release["prerelease"] != number.is_prerelease:
            raise ReleaseError(f"published prerelease {tag} is not marked as a prerelease")
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
                number,
                {"tag": tag, "oid": refs[ref], "sha": commits[ref], "release_id": release["id"]},
            )
        )
    return candidates


def _latest(candidates):
    if not candidates:
        return None
    highest = max(number.precedence_key for number, _ in candidates)
    selected = [value for number, value in candidates if number.precedence_key == highest]
    if len(selected) != 1:
        raise ReleaseError(
            "published versions have equal SemVer precedence; reconcile their build identities explicitly"
        )
    return selected[0]


def published(runner, github, refs, sha):
    """The latest stable publication; prereleases never advance a stable boundary."""
    return _latest(_validated(runner, refs, sha, _records(runner, github)))


def _first(first):
    from .changelog import normalize

    try:
        return semver.parse(normalize(first))
    except ValueError as error:
        raise ReleaseError(
            "no published stable release: declare changelog.first_version explicitly"
        ) from error


def predecessor(runner, github, refs, version, sha, first):
    current = semver.parse(version)
    candidates = _validated(
        runner,
        refs,
        sha,
        _records(runner, github),
        current.core if current.is_prerelease else None,
    )
    previous = _latest(candidates)
    if previous:
        if current.precedence_key <= semver.parse(previous["tag"][1:]).precedence_key:
            raise ReleaseError(f"version must advance published release {previous['tag']}")
    else:
        initial = _first(first)
        if current.core != initial.core or (
            initial.is_prerelease and current.precedence_key < initial.precedence_key
        ):
            raise ReleaseError(
                "no published release: declare changelog.first_version for this initial release line"
            )
    return previous


def next_version(runner, github, release, first, bump, prerelease=""):
    if bump not in {"patch", "minor", "major"}:
        raise ReleaseError("release next requires --bump patch|minor|major")
    if prerelease and (not re.fullmatch(r"[0-9A-Za-z-]+", prerelease) or prerelease.isdigit()):
        raise ReleaseError("--prerelease must be one nonnumeric ASCII SemVer identifier")
    refs = (
        local_tags(runner)
        if release.publisher == "directory"
        else remote_refs(runner, release.remote)
    )
    records = _records(runner, github)
    stable = _latest([(number, item) for number, item in records if not number.is_prerelease])
    initial = None
    if stable:
        core = list(semver.parse(stable["tag_name"][1:]).core)
        index = {"major": 0, "minor": 1, "patch": 2}[bump]
        core[index] += 1
        core[index + 1 :] = [0] * (2 - index)
    else:
        initial = _first(first)
        core = list(initial.core)
    candidates = _validated(
        runner,
        refs,
        runner.git("rev-parse", "HEAD"),
        records,
        tuple(core) if prerelease else None,
    )
    previous = _latest(candidates)
    number = semver.Version(*core)
    if prerelease:
        counters = [
            int(item.prerelease[1])
            for item, _ in candidates
            if item.core == tuple(core)
            and len(item.prerelease) == 2
            and item.prerelease[0] == prerelease
            and item.prerelease[1].isdigit()
        ]
        counter = max(counters, default=0) + 1
        if (
            initial
            and len(initial.prerelease) == 2
            and initial.prerelease[0] == prerelease
            and initial.prerelease[1].isdigit()
        ):
            counter = max(counter, int(initial.prerelease[1]))
        number = semver.Version(*core, prerelease=(prerelease, str(counter)))
        floor = semver.parse(previous["tag"][1:]) if previous else initial
        if (
            floor
            and floor.is_prerelease
            and (
                number.precedence_key < floor.precedence_key
                or (previous and number.precedence_key == floor.precedence_key)
            )
        ):
            raise ReleaseError(
                "requested prerelease channel cannot advance the published/declared version; choose a later channel or an explicit version"
            )
    version = str(number)
    tag = "v" + version
    local = local_tags(runner)
    occupied = f"refs/tags/{tag}" in refs or f"refs/tags/{tag}" in local
    return {
        "version": version,
        "tag": tag,
        "previous": previous,
        "bump": bump,
        "prerelease": prerelease,
        "tag_state": "occupied" if occupied else "available",
    }


def check_carried_changes(runner, text, notes, version, previous):
    """Carry linked changes since the selected boundary, including RCs in a final release."""
    from . import changelog
    from .backend import ancestor

    current = semver.parse(version).precedence_key
    floor = semver.parse(previous["tag"][1:]).precedence_key if previous else None
    included = {
        runner.git("rev-parse", f"{match[1]}^{{commit}}")
        for match in changelog._COMMIT_LINK.finditer("\n".join(changelog._visible_lines(notes)))
    }
    skipped = False
    for line in changelog._visible_lines(text):
        if heading := changelog._HEADING.match(line):
            try:
                number = semver.parse(changelog.normalize(heading[1] or heading[2])).precedence_key
            except ValueError:
                skipped = False
            else:
                skipped = (floor is None or floor < number) and number < current
        if not skipped:
            continue
        for match in changelog._COMMIT_LINK.finditer(line):
            commit = runner.git("rev-parse", f"{match[1]}^{{commit}}")
            if commit not in included and not (
                previous and ancestor(runner, commit, previous["sha"])
            ):
                raise ReleaseError(
                    f"changelog omits change {match[1]} from an earlier candidate; carry it into {version}"
                )
