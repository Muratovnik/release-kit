"""Declaring a changelog generator, and running it to produce a draft."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import config, generation


def written(root: Path, payload: str) -> Path:
    (root / config.CONFIG_NAME).write_text(payload, encoding="utf-8")
    return root


class GeneratorConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="generator config ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def policy(self, payload: str):
        return config.load(written(self.root, payload)).changelog

    def test_a_renamed_profile_is_accepted_and_says_what_it_is_now_called(self):
        """Refusing the former name stranded every project still using it.

        The installed CLI could not read the new name and the candidate could not read
        the old one, so neither the configuration nor the tool could move first and
        `update` rolled itself back. The name is mapped to the current profile, and the
        project is told to change it rather than being stopped.
        """
        policy = self.policy('[changelog]\nprofile = "vue-like"\n')
        self.assertEqual("conventional-changelog", policy.profile)
        self.assertEqual("vue-like", policy.deprecated_profile)
        notice = config.renamed_profile_notice("vue-like")
        self.assertIn("conventional-changelog", notice)
        self.assertIn("vue-like", notice)

    def test_a_current_profile_is_not_reported_as_deprecated(self):
        self.assertEqual("", self.policy('[changelog]\nprofile = "strict"\n').deprecated_profile)

    def test_a_profile_that_was_never_a_name_is_still_refused(self):
        # Compatibility with one former name is not permission to invent others.
        with self.assertRaisesRegex(config.ConfigError, "must be one of"):
            self.policy('[changelog]\nprofile = "angular"\n')

    def test_an_engine_is_recorded_and_an_unknown_one_is_refused(self):
        policy = self.policy(
            '[changelog]\nprofile = "strict"\n\n[changelog.generator]\nengine = "git-cliff"\n'
        )
        self.assertEqual("git-cliff", policy.generator.engine)
        self.assertEqual((), policy.generator.command)
        with self.assertRaisesRegex(config.ConfigError, "use command for any other tool"):
            self.policy('[changelog.generator]\nengine = "conventional-changelog"\n')

    def test_a_command_is_recorded_exactly_as_written(self):
        policy = self.policy(
            '[changelog.generator]\ncommand = ["npx", "conventional-changelog", "-p", "angular"]\n'
        )
        self.assertEqual(
            ("npx", "conventional-changelog", "-p", "angular"), policy.generator.command
        )
        self.assertEqual("", policy.generator.engine)

    def test_exactly_one_of_engine_and_command_is_required(self):
        for payload in (
            "[changelog.generator]\n",
            '[changelog.generator]\nengine = "git-cliff"\ncommand = ["x"]\n',
        ):
            with (
                self.subTest(payload=payload),
                self.assertRaisesRegex(config.ConfigError, "exactly one of engine"),
            ):
                self.policy(payload)

    def test_an_unknown_generator_key_is_refused(self):
        with self.assertRaisesRegex(config.ConfigError, "unknown \\[changelog.generator\\] key"):
            self.policy('[changelog.generator]\nengine = "git-cliff"\npreset = "angular"\n')

    def test_no_generator_leaves_the_project_on_curated_entries(self):
        self.assertIsNone(self.policy('[changelog]\nprofile = "strict"\n').generator)


class DraftTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="generator draft ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def policy(self, **keywords):
        return config.ChangelogConfig(generator=config.GeneratorConfig(**keywords))

    def test_a_project_without_a_generator_is_told_so(self):
        with self.assertRaisesRegex(generation.GenerationError, "no \\[changelog.generator\\]"):
            generation.draft(config.ChangelogConfig(), "1.2.3", root=self.root)

    def test_a_declared_command_runs_exactly_as_written(self):
        policy = self.policy(command=("printer", "--flag"))
        self.assertEqual(
            ["printer", "--flag"],
            generation.command_for(policy, "1.2.3", root=self.root, allow_download=False),
        )

    def test_an_engine_is_resolved_and_asked_for_the_pending_tag(self):
        with patch.object(generation.toolchain, "resolve", return_value=Path("/tools/git-cliff")):
            command = generation.command_for(
                self.policy(engine="git-cliff"), "v1.2.3", root=self.root, allow_download=False
            )
        self.assertEqual(
            [str(Path("/tools/git-cliff")), "--unreleased", "--tag", "v1.2.3"], command
        )

    def _running(self, returncode: int, stdout: str, stderr: str = ""):
        def run(command, **keywords):
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)

        return patch.object(generation.subprocess, "run", run)

    def test_a_failing_generator_reports_its_own_last_line(self):
        with (
            self._running(2, "", "cliff: no configuration found"),
            self.assertRaisesRegex(generation.GenerationError, "no configuration found"),
        ):
            generation.draft(self.policy(command=("cliff",)), "1.2.3", root=self.root)

    def test_an_empty_draft_is_refused_rather_than_returned(self):
        # Publishing an empty entry is the failure this exists to prevent, so silence
        # from the generator is an error here and not an entry with nothing in it.
        with (
            self._running(0, "   \n"),
            self.assertRaisesRegex(generation.GenerationError, "produced no entry"),
        ):
            generation.draft(self.policy(command=("cliff",)), "1.2.3", root=self.root)

    def test_a_produced_draft_is_returned_unchanged(self):
        entry = "## [1.2.3](https://example.invalid/o/r/compare/v1.2.2...v1.2.3) (2026-01-01)\n"
        with self._running(0, entry):
            self.assertEqual(
                entry, generation.draft(self.policy(command=("cliff",)), "1.2.3", root=self.root)
            )


if __name__ == "__main__":
    unittest.main()
