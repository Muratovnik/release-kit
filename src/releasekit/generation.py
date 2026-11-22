"""Run a project's declared changelog generator to produce a draft for review.

A draft is not release notes. Publication reads the entry a person reviewed and
committed. The wrapper holds the draft to the same profile the published entry must
satisfy; a generator that produces an invalid layout is a misconfiguration, not a
reason to relax the check. On request it also composes the validated entry into the
changelog file, which still leaves the review and the commit to a person and
authorizes nothing: publication reads only what was committed.

Two adapters, and the difference between them is who vouches for what runs. A named
engine is provisioned and verified by release-kit, exactly like the scanners. Anything
else is an exact argv the project supplies, executed as written: a short name for a
third-party tool would have to guess at the operator's environment, and guessing is
what this tool refuses to do.

A draft's unit is a commit by default. A project whose changes reach its main line
through merges can make the unit the first-parent commit instead, so a merge stands
for its branch the way a pull request title does. git-cliff has no such mode, so its
own context is filtered to the first-parent line and rendered again by the same
template; what that unit hides is reported for review instead of being lost silently.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import processes, storage, toolchain
from .config import ChangelogConfig
from .release.changelog import first_entry_line, normalize

TIMEOUT = 300
# Where git-cliff itself looks for a project's configuration, in its own order. When any
# exists the project owns the template and nothing here applies.
CLIFF_CONFIG_NAMES = ("cliff.toml", ".cliff.toml", ".config/cliff.toml")
CLIFF_CONFIG_ENVIRONMENT = ("GIT_CLIFF_CONFIG", "GIT_CLIFF_CONFIG_URL")
# The template `conventional-changelog` validates, for a project that wrote none. It is
# the same text as examples/changelog/cliff.toml, which a test holds it to; the remote
# below is the only part that differs between projects.
DEFAULT_CLIFF = r'''[changelog]
body = """
{% if version %}## [{{ version | trim_start_matches(pat="v") }}]({{ self::remote_url() }}{% if previous.version %}/compare/{{ previous.version }}...{{ version }}{% else %}/releases/tag/{{ version }}{% endif %}) ({{ timestamp | date(format="%Y-%m-%d") }}){% else %}## [Unreleased]{% endif %}
{% for group, group_commits in commits | group_by(attribute="group") %}
### {{ group | striptags | trim }}

{% for commit in group_commits -%}
- {% if commit.scope %}**{{ commit.scope }}:** {% endif %}{{ commit.message }} ([{{ commit.id | truncate(length=7, end="") }}]({{ self::remote_url() }}/commit/{{ commit.id }}))
{% if commit.breaking_description and commit.breaking_description != commit.message %}
{{ "  " }}{{ commit.breaking_description | replace(from="\n", to="\n  ") }}

{% endif -%}
{% endfor %}
{%- endfor %}
{%- macro remote_url() -%}
  https://github.com/{{ remote.github.owner }}/{{ remote.github.repo }}
{%- endmacro -%}
"""
trim = true

[git]
conventional_commits = true
filter_unconventional = true
filter_commits = true
protect_breaking_commits = true
# Include the chosen public change types and every breaking change. Type names are
# case-insensitive under Conventional Commits; a longer prefix is a different type.
commit_parsers = [
    { field = "breaking", pattern = "^true$", group = "<!-- 0 -->BREAKING CHANGES" },
    { message = '(?i)^feat(\([^()]+\))?!?:', group = "<!-- 1 -->Features" },
    { message = '(?i)^fix(\([^()]+\))?!?:', group = "<!-- 2 -->Bug Fixes" },
    { message = '(?i)^perf(\([^()]+\))?!?:', group = "<!-- 3 -->Performance Improvements" },
    { message = '(?i)^revert(\([^()]+\))?!?:', group = "<!-- 4 -->Reverts" },
    { message = ".*", skip = true },
]
'''
_GITHUB_REMOTE = re.compile(
    r"(?:https?://(?:[^@/]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)"
    r"(?P<repository>[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*?)(?:\.git)?/?"
)
_DIAGNOSTIC_LIMIT = 4096
_ANSI_COLOR = re.compile(r"\x1b\[[0-9;:]*m")


class GenerationError(RuntimeError):
    """A draft could not be produced."""


def _failure_detail(stderr: bytes, stdout: bytes) -> str:
    """Keep the cause and final hint within a 4096-character diagnostic."""
    for payload in (stderr, stdout):
        detail = _ANSI_COLOR.sub("", payload.decode("utf-8", errors="replace")).strip()
        if detail:
            break
    if len(detail) <= _DIAGNOSTIC_LIMIT:
        return detail
    marker = "\n[... generator output truncated ...]\n"
    available = _DIAGNOSTIC_LIMIT - len(marker)
    head = (available + 1) // 2
    return detail[:head] + marker + detail[-(available - head) :]


def default_cliff_config(repository: str) -> str:
    """The built-in template, linking entries to this GitHub OWNER/REPO."""
    owner, name = repository.split("/", 1)
    return (
        "# release-kit's built-in template for the conventional-changelog profile.\n"
        "# Copy examples/changelog/cliff.toml to the project root to own and edit it.\n\n"
        f'{DEFAULT_CLIFF}\n[remote.github]\nowner = "{owner}"\nrepo = "{name}"\n'
    )


def uses_default_template(policy: ChangelogConfig, root: Path) -> bool:
    """Whether a draft is generated from the built-in template instead of the project's."""
    generator = policy.generator
    if generator is None or generator.engine != "git-cliff":
        return False
    if policy.profile != "conventional-changelog":
        return False
    if any(os.environ.get(name) for name in CLIFF_CONFIG_ENVIRONMENT):
        return False
    return not any(os.path.lexists(root / name) for name in CLIFF_CONFIG_NAMES)


def github_repository(root: Path, declared: str = "") -> str:
    """The GitHub OWNER/REPO a generated entry links to, from policy or the remote."""
    if declared:
        return declared
    try:
        result = processes.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and not result.returncode:
        remote = result.stdout.decode("utf-8", errors="replace").strip()
        if match := _GITHUB_REMOTE.fullmatch(remote):
            return match["repository"]
    raise GenerationError(
        "the built-in changelog template links entries to GitHub and could not tell which "
        "repository: add a GitHub `origin` remote, set `repository` in [release], or "
        "write your own cliff.toml (examples/changelog/cliff.toml is a starting point)"
    )


def command_for(
    policy: ChangelogConfig,
    version: str,
    *,
    root: Path,
    allow_download: bool,
    from_tag: str = "",
    config: Path | None = None,
    context: bool = False,
) -> list[str]:
    """The generator argv; `context` asks git-cliff for its JSON context instead."""
    generator = policy.generator
    if generator is None:
        raise GenerationError(
            "no [changelog.generator] is configured; declare an engine or a command"
        )
    if from_tag and generator.engine != "git-cliff":
        raise GenerationError(
            "--from-tag requires the git-cliff engine; custom commands run exactly as declared"
        )
    if generator.command:
        return list(generator.command)
    if from_tag:
        _check_from_tag(root, from_tag)
    try:
        executable = toolchain.resolve(generator.engine, root=root, allow_download=allow_download)
    except toolchain.ToolchainError as error:
        raise GenerationError(str(error)) from error
    # git-cliff reads its own configuration from the project, so the template stays the
    # project's to own. Without one it emits its default layout, which the profile then
    # refuses — a clearer failure than silently accepting a shape nobody chose. The
    # `conventional-changelog` profile is the exception: it names the layout, so the
    # built-in template for it is passed explicitly and nothing else is discovered. That
    # template reads no hosting metadata, so the engine is kept off the network: with a
    # `[remote.github]` section it would otherwise query the API and fail on a private
    # or not yet published repository.
    command = [str(executable), "--tag", f"v{normalize(version)}", "--output", "-", "--no-exec"]
    command.extend(_config_arguments(config))
    if context:
        command.append("--context")
    if from_tag:
        # Only this boundary remains a tag in the generator's view. Otherwise an
        # intermediate release candidate splits the requested range and loses its
        # changes when notes selects the final entry.
        command.extend(
            [
                "--tag-pattern",
                "^" + re.escape(from_tag) + "$",
                "--skip-tags",
                "$^",
                "--ignore-tags",
                "$^",
                "--",
                f"refs/tags/{from_tag}..HEAD",
            ]
        )
    else:
        command.append("--unreleased")
    return command


def _config_arguments(config: Path | None) -> list[str]:
    return [] if config is None else ["--config", str(config), "--offline"]


def _check_from_tag(root: Path, tag: str) -> None:
    reference = "refs/tags/" + tag
    try:
        for arguments in (
            ["check-ref-format", reference],
            ["rev-parse", "--verify", "--end-of-options", reference + "^{commit}"],
            ["merge-base", "--is-ancestor", reference, "HEAD"],
        ):
            result = processes.run(
                ["git", *arguments],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=TIMEOUT,
            )
            if result.returncode:
                raise GenerationError(
                    "--from-tag must name an existing local tag reachable from HEAD"
                )
        # git-cliff also resolves short names while constructing tag context. A
        # colliding branch or other ref can change that context even when the range
        # itself uses the verified full tag reference.
        result = processes.run(
            ["git", "rev-parse", "--symbolic-full-name", "--verify", "--end-of-options", tag],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
        )
        if result.returncode or result.stdout.rstrip(b"\r\n") != os.fsencode(reference):
            raise GenerationError("--from-tag must name an unambiguous local tag")
    except (OSError, subprocess.SubprocessError) as error:
        raise GenerationError(f"could not validate --from-tag: {error}") from error


@dataclass(frozen=True)
class HiddenCommit:
    id: str
    subject: str
    breaking: bool


@dataclass(frozen=True)
class HiddenChange:
    """Releasable commits a first-parent entry does not show, grouped by their merge.

    `merge_skipped`: the merge's own subject was skipped by the changelog parsers, so
    nothing in the entry stands for these commits. `breaking_not_marked`: the merge is
    in the entry, but it does not carry the break these commits declare.
    """

    reason: str
    merge_id: str
    merge_subject: str
    commits: tuple[HiddenCommit, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "reason": self.reason,
            "merge": {"id": self.merge_id, "subject": self.merge_subject},
            "commits": [
                {"id": commit.id, "subject": commit.subject, "breaking": commit.breaking}
                for commit in self.commits
            ],
        }


@dataclass(frozen=True)
class Draft:
    text: str
    units: str = "commits"
    hidden: tuple[HiddenChange, ...] = ()


def draft(
    policy: ChangelogConfig,
    version: str,
    *,
    root: Path,
    allow_download: bool = True,
    from_tag: str = "",
    repository: str = "",
) -> str:
    """The draft text alone; `draft_entry` also reports what a first-parent unit hides."""
    return draft_entry(
        policy,
        version,
        root=root,
        allow_download=allow_download,
        from_tag=from_tag,
        repository=repository,
    ).text


def draft_entry(
    policy: ChangelogConfig,
    version: str,
    *,
    root: Path,
    allow_download: bool = True,
    from_tag: str = "",
    repository: str = "",
) -> Draft:
    root = storage.checked(root)
    first_parent = policy.units == "first-parent"
    if first_parent and policy.generator is not None and policy.generator.command:
        raise GenerationError(
            'units = "first-parent" requires the git-cliff engine; '
            "custom commands run exactly as declared"
        )
    default = uses_default_template(policy, root)
    if not default and not first_parent:
        return Draft(_draft(policy, version, root, allow_download, from_tag, None))
    configuration = default_cliff_config(github_repository(root, repository)) if default else None
    with storage.temporary(root, "cliff-") as workspace:
        path = None
        if configuration is not None:
            path = workspace.path / "cliff.toml"
            path.write_text(configuration, encoding="utf-8", newline="\n")
            workspace.remember(path)
        if first_parent:
            return _first_parent_draft(
                policy, version, root, allow_download, from_tag, path, workspace
            )
        return Draft(_draft(policy, version, root, allow_download, from_tag, path))


def _draft(
    policy: ChangelogConfig,
    version: str,
    root: Path,
    allow_download: bool,
    from_tag: str,
    config: Path | None,
) -> str:
    command = command_for(
        policy, version, root=root, allow_download=allow_download, from_tag=from_tag, config=config
    )
    engine = bool(policy.generator and policy.generator.engine)
    return _text(_run(command, root, engine=engine), command, version)


def _run(command: list[str], root: Path, *, engine: bool) -> bytes:
    environment = None
    if engine:
        environment = dict(os.environ)
        # --output overrides the configured output file; --prepend is a separate
        # environment-controlled write that has no place in a draft operation.
        environment.pop("GIT_CLIFF_PREPEND", None)
    try:
        completed = processes.run(
            command,
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GenerationError(f"could not run {command[0]}: {error}") from error
    if completed.returncode:
        detail = _failure_detail(completed.stderr, completed.stdout)
        raise GenerationError(
            f"{Path(command[0]).name} exited {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout


def _text(stdout: bytes, command: list[str], version: str) -> str:
    try:
        text = stdout.decode("utf-8")
    except UnicodeError as error:
        raise GenerationError(f"{Path(command[0]).name} did not produce UTF-8 output") from error
    if not text.strip():
        raise GenerationError(_no_entry(command, version))
    return text


def _no_entry(command: list[str], version: str) -> str:
    return (
        f"{Path(command[0]).name} produced no entry for {version}; "
        "there may be no releasable commits since the last tag"
    )


def _first_parent_draft(
    policy: ChangelogConfig,
    version: str,
    root: Path,
    allow_download: bool,
    from_tag: str,
    config: Path | None,
    workspace: storage.Workspace,
) -> Draft:
    # The context is git-cliff's own reading of the range after the project's parsers,
    # filters and grouping, so a commit in it is one the template would list. Keeping
    # only the first-parent commits and rendering that context with the same template
    # changes the unit and nothing else. Skip lists and the `merge_commit` parser were
    # rejected: the first overflows a command line on Windows, the second cannot keep a
    # direct commit on the main line or drop a merge inside a merged branch.
    command = command_for(
        policy,
        version,
        root=root,
        allow_download=allow_download,
        from_tag=from_tag,
        config=config,
        context=True,
    )
    releases = _context(_run(command, root, engine=True), command)
    listed = {commit["id"]: commit for release in releases for commit in release["commits"]}
    if not listed:
        raise GenerationError(_no_entry(command, version))
    history = _History.read(root, from_tag)
    for release in releases:
        release["commits"] = [c for c in release["commits"] if c["id"] in history.line]
    hidden = history.hidden(listed)
    if not any(release["commits"] for release in releases):
        merges = sum(1 for change in hidden if change.reason == "merge_skipped")
        raise GenerationError(
            f"no releasable commit for {version} is on the first-parent line of HEAD: "
            f"{len(listed)} releasable commit(s) reached it only through {merges} merge(s) "
            "whose subjects the changelog parsers skip. Give such a merge a releasable "
            'subject (for example `feat: ...`) or draft with units = "commits"\n'
            + "\n".join(review_lines(hidden))
        )
    path = workspace.path / "context.json"
    path.write_text(json.dumps(releases), encoding="utf-8", newline="\n")
    workspace.remember(path)
    render = [command[0], "--from-context", str(path), "--output", "-", "--no-exec"]
    render.extend(_config_arguments(config))
    text = _text(_run(render, root, engine=True), render, version)
    return Draft(text, "first-parent", hidden)


def _context(stdout: bytes, command: list[str]) -> list[dict]:
    name = Path(command[0]).name
    try:
        releases = json.loads(stdout.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise GenerationError(f"{name} did not produce a readable --context: {error}") from error
    if not isinstance(releases, list) or not all(
        isinstance(release, dict)
        and isinstance(release.get("commits"), list)
        and all(
            isinstance(commit, dict) and isinstance(commit.get("id"), str)
            for commit in release["commits"]
        )
        for release in releases
    ):
        raise GenerationError(f"{name} produced a --context without releases and commit ids")
    return releases


@dataclass(frozen=True)
class _History:
    """The draft's range in Git: its first-parent line and which commit brought in each."""

    line: frozenset[str]
    introduced_by: dict[str, str]
    subjects: dict[str, str]
    position: dict[str, int]

    @classmethod
    def read(cls, root: Path, from_tag: str) -> _History:
        # Without --from-tag git-cliff picks the unreleased range by its own tag rules.
        # The whole first-parent line of HEAD contains that range's main-line commits
        # and no commit of a merged branch, so intersecting with it needs no boundary.
        revisions = f"refs/tags/{from_tag}..HEAD" if from_tag else "HEAD"
        line = _git(root, "rev-list", "--first-parent", revisions).split()
        records = [
            record.split("\x1f")
            for record in _git(
                root, "log", "--no-color", "--format=%H%x1f%P%x1f%s", revisions
            ).splitlines()
        ]
        parents = {record[0]: record[1].split() for record in records if len(record) == 3}
        subjects = {record[0]: record[2] for record in records if len(record) == 3}
        # Walking the main line oldest first, everything reachable from the previous
        # main-line commit has already been seen, so what a commit's other parents reach
        # for the first time is exactly what that merge brought in.
        introduced_by: dict[str, str] = {}
        seen: set[str] = set()
        for merge in reversed(line):
            seen.add(merge)
            pending = list(parents.get(merge, [])[1:])
            while pending:
                commit = pending.pop()
                if commit in seen or commit not in parents:
                    continue
                seen.add(commit)
                introduced_by[commit] = merge
                pending.extend(parents[commit])
        return cls(
            frozenset(line),
            introduced_by,
            subjects,
            {commit: index for index, commit in enumerate(subjects)},
        )

    def hidden(self, listed: dict[str, dict]) -> tuple[HiddenChange, ...]:
        groups: dict[tuple[str, str], list[str]] = {}
        for commit, fields in listed.items():
            merge = self.introduced_by.get(commit)
            if commit in self.line or merge is None:
                continue
            if merge not in listed:
                groups.setdefault(("merge_skipped", merge), []).append(commit)
            elif fields.get("breaking") and not listed[merge].get("breaking"):
                groups.setdefault(("breaking_not_marked", merge), []).append(commit)
        return tuple(
            HiddenChange(
                reason,
                merge,
                self.subjects.get(merge, ""),
                tuple(
                    HiddenCommit(
                        commit, self.subjects.get(commit, ""), bool(listed[commit].get("breaking"))
                    )
                    for commit in self._ordered(commits)
                ),
            )
            for (reason, merge), commits in sorted(
                groups.items(),
                key=lambda item: (item[0][0] != "merge_skipped", self._rank(item[0][1])),
            )
        )

    def _rank(self, commit: str) -> int:
        return self.position.get(commit, len(self.position))

    def _ordered(self, commits: Iterable[str]) -> list[str]:
        return sorted(commits, key=self._rank)


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = processes.run(
            ["git", "-c", "log.showSignature=false", *arguments],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GenerationError(f"could not read the first-parent history: {error}") from error
    if completed.returncode:
        detail = _failure_detail(completed.stderr, completed.stdout)
        raise GenerationError(f"could not read the first-parent history: {detail}")
    return completed.stdout.decode("utf-8", errors="replace")


# The lead line of the review hint for each reason, in the order it is printed.
_REVIEW_LEADS = {
    "merge_skipped": (
        "{count} releasable commit(s) are not in this entry: they reached the main line "
        "only through a merge whose subject the changelog parsers skip. Give the merge a "
        "releasable subject, or say what readers need in the entry before committing it:"
    ),
    "breaking_not_marked": (
        "{count} breaking commit(s) are behind a merge the entry lists without marking "
        "it breaking. Describe the break in the entry before committing it:"
    ),
}


def review_lines(hidden: Iterable[HiddenChange]) -> list[str]:
    """The review hint for what a first-parent entry does not show, one line each."""
    hidden = tuple(hidden)
    lines: list[str] = []
    for reason, lead in _REVIEW_LEADS.items():
        changes = [change for change in hidden if change.reason == reason]
        if not changes:
            continue
        count = sum(len(change.commits) for change in changes)
        lines.append("review: " + lead.format(count=count))
        for change in changes:
            lines.append(f"  {change.merge_id[:7]} {change.merge_subject}")
            lines.extend(f"    {commit.id[:7]} {commit.subject}" for commit in change.commits)
    return lines


def compose(changelog: str, entry: str) -> str:
    """Insert an entry before the first version entry, in the changelog's newline style.

    The title and any `Unreleased` section stay above it. Without a version entry the
    new one goes at the end. Nothing else in the file is touched.
    """
    newline = "\r\n" if "\r\n" in changelog else "\n"
    block = newline.join(entry.replace("\r\n", "\n").rstrip("\n").split("\n")) + newline
    index = first_entry_line(changelog)
    lines = changelog.splitlines(keepends=True)
    head = changelog if index is None else "".join(lines[:index])
    tail = "" if index is None else "".join(lines[index:])
    if head and not head.endswith(("\n", "\r")):
        head += newline
    if head.strip() and not head.endswith(newline + newline):
        head += newline
    return head + block + (newline + tail if tail else "")
