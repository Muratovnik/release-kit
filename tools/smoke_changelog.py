"""Exercise changelog drafting and extraction with the delivered CLI and git-cliff."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from smoke_onboarding import fixture_environment, git, invoke

ROOT = Path(__file__).resolve().parents[1]


def create_project(root: Path, artifact: Path) -> dict[str, str]:
    """Refuse existing roots; the caller owns this disposable fixture and its cache."""
    root.mkdir()
    environment = fixture_environment()
    environment["GIT_CLIFF_OFFLINE"] = "true"
    environment["GIT_CLIFF_PREPEND"] = "CHANGELOG.md"
    git(root, environment, "init", "--template=", "-q")
    git(root, environment, "config", "user.name", "Example Maintainer")
    git(root, environment, "config", "user.email", "maintainer@example.invalid")
    git(root, environment, "config", "commit.gpgsign", "false")
    git(root, environment, "config", "core.hooksPath", ".git/hooks")
    (root / ".cache").mkdir()
    (root / ".github").mkdir()
    shutil.copyfile(artifact, root / ".github/relkit.pyz")
    (root / ".gitignore").write_text("/.cache/\n", encoding="utf-8")
    (root / "relkit.toml").write_text(
        '[changelog]\nprofile = "conventional-changelog"\nfirst_version = "0.1.0"\n'
        '[changelog.generator]\nengine = "git-cliff"\n'
        '[changelog.section_aliases]\n"Новое" = "features"\n"Исправлено" = "fixes"\n'
        '"Несовместимые изменения" = "breaking"\n"Главное" = "highlights"\n',
        encoding="utf-8",
    )
    (root / "processor.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path('unexpected-processor-write').touch()\nsys.stdout.write(sys.stdin.read())\n",
        encoding="utf-8",
    )
    processor = [sys.executable, str(root / "processor.py")]
    command = subprocess.list2cmdline(processor) if os.name == "nt" else shlex.join(processor)
    # TOML literal strings leave the platform's native command quoting intact.
    if "'''" in command:
        raise ValueError("fixture path cannot contain three consecutive apostrophes")
    template = (ROOT / "examples/changelog/cliff.toml").read_text(encoding="utf-8")
    template = template.replace(
        "[changelog]",
        '[changelog]\noutput = "CHANGELOG.md"\n'
        f"postprocessors = [{{ pattern = \".*\", replace_command = '''{command}''' }}]",
    )
    (root / "cliff.toml").write_text(template, encoding="utf-8")
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\nHuman notes awaiting review.\n", encoding="utf-8"
    )
    git(root, environment, "add", "--", ".")
    return environment


def commit(root: Path, environment: dict[str, str], message: str) -> str:
    git(root, environment, "commit", "--allow-empty", "-qm", message)
    return git(root, environment, "rev-parse", "HEAD").strip()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def smoke(root: Path, artifact: Path) -> None:
    environment = create_project(root, artifact)
    initial = commit(root, environment, "feat(core): initialize public API")
    source = root / "CHANGELOG.md"
    original = source.read_bytes()
    first_candidate = invoke(root, environment, 0, "notes", "0.1.0-rc.1", "--draft")["data"][
        "notes"
    ]
    require(
        "/releases/tag/v0.1.0-rc.1)" in first_candidate,
        "initial candidate was not accepted by its declared first core",
    )
    first = invoke(root, environment, 0, "notes", "0.1.0", "--draft")["data"]["notes"]
    require("/releases/tag/v0.1.0)" in first, "first release did not use its tag URL")
    require("/compare/" not in first, "first release invented a predecessor")
    require("/commit/" + initial in first, "initial feature was omitted from its draft")
    require(source.read_bytes() == original, "draft changed its source changelog")
    require(not (root / "unexpected-processor-write").exists(), "draft executed a template command")
    source.write_text("# Changelog\n\n" + first, encoding="utf-8")
    git(root, environment, "add", "--", "CHANGELOG.md")
    commit(root, environment, "docs: record reviewed initial notes")
    git(root, environment, "tag", "v0.1.0")

    breaking = commit(root, environment, "refactor(api)!: remove legacy option")
    migration = commit(
        root,
        environment,
        "chore(api): change the environment setting\n\nBREAKING CHANGE: Use NEW_MODE instead of OLD_MODE.",
    )
    footer_alias = commit(
        root,
        environment,
        "build(api): replace the package entry\n\nBREAKING-CHANGE: Import the new entry module.",
    )
    early_fix = commit(root, environment, "FIX(core): repair retries")
    mixed_feature = commit(root, environment, "Feat(api): expose request metadata")
    performance = commit(root, environment, "PERF(cache): reduce repeated parsing")
    revert = commit(root, environment, "ReVeRt: restore the fallback behavior")
    internal = commit(root, environment, "refactor: reorganize helpers")
    prefix = commit(root, environment, "feature: unintended type with feat prefix")
    git(root, environment, "tag", "v0.2.0-rc.1")
    later_fix = commit(root, environment, "fix(core): preserve recovery after a retry")
    original = source.read_bytes()
    refs = git(root, environment, "show-ref", "--tags")
    head = git(root, environment, "rev-parse", "HEAD")
    arguments = ["notes", "0.2.0", "--draft", "--from-tag", "v0.1.0"]
    final = invoke(root, environment, 0, *arguments)["data"]["notes"]
    require("/compare/v0.1.0...v0.2.0)" in final, "final release used a candidate as its boundary")
    for sha in (
        breaking,
        migration,
        footer_alias,
        early_fix,
        mixed_feature,
        performance,
        revert,
        later_fix,
    ):
        require(final.count("/commit/" + sha) == 1, "a releasable commit was omitted or duplicated")
    for sha in (initial, internal, prefix):
        require("/commit/" + sha not in final, "a previous or excluded change entered the draft")
    require("### BREAKING CHANGES" in final, "breaking commits lost their visible classification")
    require(
        "Use NEW_MODE instead of OLD_MODE." in final, "breaking migration instructions were lost"
    )
    require("Import the new entry module." in final, "BREAKING-CHANGE footer was lost")

    candidate = invoke(
        root, environment, 0, "notes", "0.2.0-rc.2", "--draft", "--from-tag", "v0.2.0-rc.1"
    )["data"]["notes"]
    require(
        "/compare/v0.2.0-rc.1...v0.2.0-rc.2)" in candidate, "candidate boundary was not preserved"
    )
    require("/commit/" + later_fix in candidate, "candidate omitted its new fix")
    require("/commit/" + early_fix not in candidate, "candidate included an earlier release's fix")

    template_path = root / "cliff.toml"
    template = template_path.read_text(encoding="utf-8")
    for english, localized in (
        ("Features", "Новое"),
        ("Bug Fixes", "Исправлено"),
        ("BREAKING CHANGES", "Несовместимые изменения"),
    ):
        template = template.replace(english, localized)
    template_path.write_text(template, encoding="utf-8")
    localized = invoke(root, environment, 0, *arguments)["data"]["notes"]
    require("### Исправлено" in localized, "localized ordinary heading was not accepted")
    require("### Новое" in localized, "localized feature heading was not accepted")
    require(
        "### Несовместимые изменения" in localized, "localized breaking heading was not accepted"
    )
    old = invoke(root, environment, 0, "notes", "0.1.0")["data"]["notes"]
    require(old == first, "adding aliases changed extraction of an English historical entry")

    invoke(root, environment, 2, *arguments, "--output", "CHANGELOG.md")
    require(source.read_bytes() == original, "draft export replaced historical changelog bytes")
    output = root / "release-notes.md"
    exported = invoke(root, environment, 0, *arguments, "--output", output.name)["data"]["notes"]
    require(
        output.read_bytes() == exported.encode("utf-8"),
        "exported bytes differ from validated notes",
    )
    require(source.read_bytes() == original, "separate draft export changed changelog history")
    require(
        not (root / "unexpected-processor-write").exists(), "a later draft ran a template command"
    )

    invalid = root / "invalid-notes.md"
    invalid.write_text(
        localized.splitlines()[0] + "\n\n### Исправлено\n\n- Изменение без ссылки.\n",
        encoding="utf-8",
    )
    invoke(
        root, environment, 1, "notes", "0.2.0", "--changelog", invalid.name, "--output", output.name
    )
    require(
        output.read_bytes() == exported.encode("utf-8"),
        "invalid localized notes replaced a valid export",
    )
    require(git(root, environment, "show-ref", "--tags") == refs, "drafting changed a tag")
    require(
        git(root, environment, "rev-parse", "HEAD") == head, "drafting changed the fixture commit"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument(
        "--work-dir", type=Path, required=True, help="New disposable project directory"
    )
    arguments = parser.parse_args()
    try:
        smoke(arguments.work_dir.absolute(), arguments.artifact.absolute())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"changelog-smoke: {error}", file=sys.stderr)
        return 1
    print(
        "changelog-smoke: real generation, release boundaries, localized notes and safe export passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
