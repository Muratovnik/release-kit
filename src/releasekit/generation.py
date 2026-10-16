"""Run a project's declared changelog generator to produce a draft for review.

A draft is not release notes. Publication reads the entry a person reviewed and
committed. The wrapper validates stdout without inserting it into the changelog or
authorizing publication. It turns history into a starting point and holds that
starting point to the same profile the published entry must satisfy. A generator
that produces an invalid layout is a misconfiguration, not a reason to relax the check.

Two adapters, and the difference between them is who vouches for what runs. A named
engine is provisioned and verified by release-kit, exactly like the scanners. Anything
else is an exact argv the project supplies, executed as written: a short name for a
third-party tool would have to guess at the operator's environment, and guessing is
what this tool refuses to do.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import processes, storage, toolchain
from .config import ChangelogConfig
from .release.changelog import normalize

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
) -> list[str]:
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
    if config is not None:
        command.extend(["--config", str(config), "--offline"])
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


def draft(
    policy: ChangelogConfig,
    version: str,
    *,
    root: Path,
    allow_download: bool = True,
    from_tag: str = "",
    repository: str = "",
) -> str:
    root = storage.checked(root)
    if not uses_default_template(policy, root):
        return _draft(policy, version, root, allow_download, from_tag, None)
    configuration = default_cliff_config(github_repository(root, repository))
    with storage.temporary(root, "cliff-") as workspace:
        path = workspace.path / "cliff.toml"
        path.write_text(configuration, encoding="utf-8", newline="\n")
        workspace.remember(path)
        return _draft(policy, version, root, allow_download, from_tag, path)


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
    environment = None
    if policy.generator and policy.generator.engine:
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
    try:
        text = completed.stdout.decode("utf-8")
    except UnicodeError as error:
        raise GenerationError(f"{Path(command[0]).name} did not produce UTF-8 output") from error
    if not text.strip():
        raise GenerationError(
            f"{Path(command[0]).name} produced no entry for {version}; "
            "there may be no releasable commits since the last tag"
        )
    return text
