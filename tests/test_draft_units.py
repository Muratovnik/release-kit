"""Draft units: one line per commit, or one per first-parent commit of HEAD.

These run the pinned git-cliff against a real history built here, because the
first-parent mode is a round trip through git-cliff's own context and template.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import config, generation, toolchain

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "example-owner/example-project"
STARTER = (ROOT / "examples/changelog/cliff.toml").read_text(encoding="utf-8")


class History:
    """A main line fed by `--no-ff` merges, with every shape the unit must handle."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.sha: dict[str, str] = {}
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        self.environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Example Maintainer",
                "-c",
                "user.email=maintainer@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=.git/hooks",
                *arguments,
            ],
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()

    def commit(self, name: str, message: str) -> None:
        self.git("commit", "-q", "--allow-empty", "-m", message)
        self.sha[name] = self.git("rev-parse", "HEAD")

    def merge(self, name: str, branch: str, message: str) -> None:
        self.git("merge", "-q", "--no-ff", "-m", message, branch)
        self.sha[name] = self.git("rev-parse", "HEAD")

    def build(self) -> History:
        self.git("init", "-q", "--template=", "-b", "main")
        self.git("remote", "add", "origin", f"https://github.com/{REPOSITORY}.git")
        self.commit("initial", "feat: initial API")
        self.git("tag", "v1.0.0")
        # A feature branch merged under a releasable subject: one line, the merge.
        self.git("checkout", "-q", "-b", "widgets")
        self.commit("widget_add", "feat(api): add widgets")
        self.commit("widget_edge", "fix(api): handle an empty widget")
        self.commit("widget_size", "feat(api): accept widget sizes")
        self.git("checkout", "-q", "main")
        self.merge("widgets", "widgets", "feat(api): widgets")
        self.git("tag", "v1.1.0-rc.1")
        # A merge whose own subject the parsers skip hides a feature.
        self.git("checkout", "-q", "-b", "tidy")
        self.commit("dark_mode", "feat(ui): dark mode")
        self.git("checkout", "-q", "main")
        self.merge("tidy", "tidy", "chore: tidy the build")
        # An inner merge into an integration branch never reaches the main line itself.
        self.git("checkout", "-q", "-b", "integration")
        self.git("checkout", "-q", "-b", "inner")
        self.commit("inner_flag", "feat(cli): add an inner flag")
        self.git("checkout", "-q", "integration")
        self.merge("inner", "inner", "feat(cli): inner flags")
        self.commit("integration_fix", "fix(cli): integration repair")
        self.git("checkout", "-q", "main")
        self.merge("integration", "integration", "feat(cli): command line")
        # A direct commit on the main line is its own line.
        self.commit("direct", "fix: direct repair")
        # A breaking commit behind a merge that does not say so.
        self.git("checkout", "-q", "-b", "storage")
        self.commit("drop_format", "feat(io)!: drop the old file format")
        self.git("checkout", "-q", "main")
        self.merge("storage", "storage", "feat(io): new storage")
        return self


ON_LINE = ("widgets", "integration", "direct", "storage")
OFF_LINE = (
    "widget_add",
    "widget_edge",
    "widget_size",
    "dark_mode",
    "inner_flag",
    "inner",
    "integration_fix",
    "drop_format",
)
AFTER_CANDIDATE = ("integration", "direct", "storage")


def engine_or_skip(root: Path) -> Path:
    try:
        return toolchain.resolve("git-cliff", root=root, allow_download=False)
    except (toolchain.ToolchainError, OSError) as error:
        raise unittest.SkipTest(f"git-cliff is not available offline: {error}") from error


def policy(units: str = "first-parent", **generator: object) -> config.ChangelogConfig:
    return config.ChangelogConfig(
        profile="conventional-changelog",
        units=units,
        generator=config.GeneratorConfig(**(generator or {"engine": "git-cliff"})),
    )


class DraftUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="draft units ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "project"
        self.root.mkdir()
        environment = patch.dict(os.environ, {"GIT_CLIFF_OFFLINE": "true"})
        environment.start()
        for name in generation.CLIFF_CONFIG_ENVIRONMENT:
            os.environ.pop(name, None)
        self.addCleanup(environment.stop)
        self.history = History(self.root).build()
        self.executable = engine_or_skip(self.root)

    def draft(self, units: str = "first-parent", from_tag: str = "") -> generation.Draft:
        return generation.draft_entry(
            policy(units),
            "1.1.0",
            root=self.root,
            allow_download=False,
            from_tag=from_tag,
            repository=REPOSITORY,
        )

    def links(self, text: str, names: tuple[str, ...]) -> dict[str, int]:
        return {name: text.count("/commit/" + self.history.sha[name]) for name in names}

    def assert_first_parent(self, text: str, listed: tuple[str, ...]) -> None:
        self.assertEqual(dict.fromkeys(listed, 1), self.links(text, listed), text)
        absent = tuple(name for name in ON_LINE + OFF_LINE if name not in listed)
        self.assertEqual(dict.fromkeys(absent, 0), self.links(text, absent), text)

    def test_commits_units_keep_the_engine_output_byte_for_byte(self):
        (self.root / "cliff.toml").write_text(STARTER, encoding="utf-8")
        drafted = self.draft("commits")
        # Today's documented invocation, written out independently of command_for.
        expected = subprocess.run(
            [str(self.executable), "--tag", "v1.1.0", "--output", "-", "--no-exec"]
            + ["--unreleased"],
            cwd=self.root,
            capture_output=True,
            check=True,
            timeout=120,
        ).stdout.decode("utf-8")
        self.assertEqual(expected, drafted.text)
        self.assertEqual(("commits", ()), (drafted.units, drafted.hidden))
        # Every releasable commit is its own line, inside merged branches too.
        self.assertEqual(1, drafted.text.count("/commit/" + self.history.sha["inner_flag"]))

    def test_first_parent_lists_merges_and_direct_commits_with_the_built_in_template(self):
        drafted = self.draft(from_tag="v1.0.0")
        self.assertTrue(generation.uses_default_template(policy(), self.root))
        self.assertEqual("first-parent", drafted.units)
        self.assert_first_parent(drafted.text, ON_LINE)
        self.assertIn("/compare/v1.0.0...v1.1.0)", drafted.text.splitlines()[0])
        self.assertIn("- **api:** widgets ([", drafted.text)

    def test_first_parent_without_from_tag_drafts_after_the_latest_tag(self):
        drafted = self.draft()
        self.assert_first_parent(drafted.text, AFTER_CANDIDATE)
        self.assertIn("/compare/v1.1.0-rc.1...v1.1.0)", drafted.text.splitlines()[0])

    def test_first_parent_renders_with_a_project_owned_template(self):
        template = STARTER.replace('owner = "example-owner"', 'owner = "project-owner"')
        (self.root / "cliff.toml").write_text(template, encoding="utf-8")
        self.assertFalse(generation.uses_default_template(policy(), self.root))
        for from_tag, listed in (("v1.0.0", ON_LINE), ("", AFTER_CANDIDATE)):
            with self.subTest(from_tag=from_tag):
                text = self.draft(from_tag=from_tag).text
                self.assert_first_parent(text, listed)
                self.assertIn("https://github.com/project-owner/example-project/commit/", text)
                self.assertNotIn("github.com/example-owner/", text)

    def test_the_hint_names_commits_behind_a_skipped_merge_and_an_unmarked_break(self):
        drafted = self.draft(from_tag="v1.0.0")
        sha = self.history.sha
        self.assertEqual(
            [
                {
                    "reason": "merge_skipped",
                    "merge": {"id": sha["tidy"], "subject": "chore: tidy the build"},
                    "commits": [
                        {
                            "id": sha["dark_mode"],
                            "subject": "feat(ui): dark mode",
                            "breaking": False,
                        }
                    ],
                },
                {
                    "reason": "breaking_not_marked",
                    "merge": {"id": sha["storage"], "subject": "feat(io): new storage"},
                    "commits": [
                        {
                            "id": sha["drop_format"],
                            "subject": "feat(io)!: drop the old file format",
                            "breaking": True,
                        }
                    ],
                },
            ],
            [change.as_json() for change in drafted.hidden],
        )
        lines = generation.review_lines(drafted.hidden)
        self.assertEqual(
            [
                f"  {sha['tidy'][:7]} chore: tidy the build",
                f"    {sha['dark_mode'][:7]} feat(ui): dark mode",
            ],
            lines[1:3],
        )
        self.assertTrue(lines[0].startswith("review: 1 releasable commit(s) are not in this entry"))
        self.assertTrue(lines[3].startswith("review: 1 breaking commit(s) are behind a merge"))

    def test_a_breaking_merge_does_not_flag_the_break_it_already_carries(self):
        self.history.git("checkout", "-q", "-b", "marked")
        self.history.commit("marked_drop", "feat(db)!: drop the old schema")
        self.history.git("checkout", "-q", "main")
        self.history.merge("marked", "marked", "feat(db)!: new schema")
        flagged = {
            commit.id
            for change in self.draft(from_tag="v1.0.0").hidden
            for commit in change.commits
        }
        self.assertIn(self.history.sha["drop_format"], flagged)
        self.assertNotIn(self.history.sha["marked_drop"], flagged)

    def test_only_merges_with_skipped_subjects_name_that_cause(self):
        root = self.root.parent / "merged-only"
        root.mkdir()
        history = History(root)
        history.git("init", "-q", "--template=", "-b", "main")
        history.git("remote", "add", "origin", f"https://github.com/{REPOSITORY}.git")
        history.commit("initial", "feat: initial API")
        history.git("tag", "v1.0.0")
        history.git("checkout", "-q", "-b", "topic")
        history.commit("one", "feat: first hidden feature")
        history.commit("two", "fix: hidden repair")
        history.git("checkout", "-q", "main")
        history.merge("topic", "topic", "Merge branch 'topic'")
        arguments = {"root": root, "allow_download": False, "repository": REPOSITORY}
        with self.assertRaises(generation.GenerationError) as raised:
            generation.draft_entry(policy(), "1.1.0", **arguments)
        message = str(raised.exception)
        self.assertIn("no releasable commit for 1.1.0 is on the first-parent line of HEAD", message)
        self.assertIn("2 releasable commit(s) reached it only through 1 merge(s)", message)
        self.assertNotIn("since the last tag", message)
        # The error names which merge hides which commits, as the hint of a draft does.
        self.assertIn(f"  {history.sha['topic'][:7]} Merge branch 'topic'", message)
        for name, subject in (("one", "feat: first hidden feature"), ("two", "fix: hidden repair")):
            self.assertIn(f"    {history.sha[name][:7]} {subject}", message)
        # Control: the same history drafts both commits when each commit is a unit.
        text = generation.draft_entry(policy("commits"), "1.1.0", **arguments).text
        for name in ("one", "two"):
            self.assertIn("/commit/" + history.sha[name], text)

    def test_cli_prints_the_hint_and_reports_it_in_json(self):
        (self.root / "relkit.toml").write_text(
            '[changelog]\nprofile = "conventional-changelog"\nunits = "first-parent"\n'
            '[changelog.generator]\nengine = "git-cliff"\n',
            encoding="utf-8",
        )
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "releasekit.cli", "--json", "notes", "1.1.0"]
            + ["--draft", "--from-tag", "v1.0.0", "--root", str(self.root)],
            cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True,
            timeout=300,
            check=False,
        )
        stderr = completed.stderr.decode("utf-8")
        self.assertEqual(0, completed.returncode, stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(
            ["merge_skipped", "breaking_not_marked"],
            [change["reason"] for change in result["data"]["hidden_changes"]],
        )
        self.assertIn(f"relkit notes:     {self.history.sha['dark_mode'][:7]} feat(ui)", stderr)
        self.assert_first_parent(result["data"]["notes"], ON_LINE)


class UnitRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="draft unit refusal ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_a_custom_command_is_refused_before_it_runs(self):
        marker = self.root / "ran"
        command = (
            sys.executable,
            "-B",
            "-c",
            f"open({str(marker)!r}, 'w').close(); print('## [1.1.0]')",
        )
        with self.assertRaisesRegex(
            generation.GenerationError, 'units = "first-parent" requires the git-cliff engine'
        ):
            generation.draft_entry(policy(command=command), "1.1.0", root=self.root)
        self.assertFalse(marker.exists())
        # Control: the same command runs when each commit is a unit.
        generation.draft_entry(policy("commits", command=command), "1.1.0", root=self.root)
        self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
