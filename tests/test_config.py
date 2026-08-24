from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from releasekit import config


class LoadTests(unittest.TestCase):
    def test_a_missing_file_is_an_error_only_when_the_caller_needs_one(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.assertEqual({}, config.load(root, required=False).exposure.baseline)
            with self.assertRaises(config.ConfigError):
                config.load(root)

    def test_it_reads_the_baseline_and_the_names_file(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                '[exposure]\nnames_file = "private-names"\n'
                'betterleaks_config = "security.toml"\n'
                'allowed_identities = ["Example Maintainer <owner@example.invalid>"]\n'
                "forbid_png_metadata = true\n\n"
                '[exposure.baseline]\n"a/b.json" = ["home-directory"]\n',
                encoding="utf-8",
            )
            settings = config.load(root)

        self.assertEqual("private-names", settings.exposure.names_file)
        self.assertEqual("security.toml", settings.exposure.betterleaks_config)
        self.assertEqual(
            ["Example Maintainer <owner@example.invalid>"],
            settings.exposure.allowed_identities,
        )
        self.assertTrue(settings.exposure.forbid_png_metadata)
        self.assertEqual({"a/b.json": ["home-directory"]}, settings.exposure.baseline)

    def test_a_baseline_that_is_not_a_mapping_of_lists_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                '[exposure]\nbaseline = { "a" = "home-directory" }\n', encoding="utf-8"
            )
            with self.assertRaises(config.ConfigError):
                config.load(root)

    def test_a_string_cannot_masquerade_as_a_list(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                '[exposure]\nprivate_paths = ".private"\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(config.ConfigError, "private_paths"):
                config.load(root)

    def test_unknown_keys_are_refused_instead_of_silently_weakening_policy(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                "[exposure]\nprivate_path = ['.private']\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(config.ConfigError, "private_path"):
                config.load(root)

    def test_a_misspelled_top_level_table_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                "[exposuer]\nprivate_paths = ['.private']\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(config.ConfigError, "exposuer"):
                config.load(root)

    def test_boolean_switches_require_toml_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                '[exposure]\ncheck_secrets = "false"\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(config.ConfigError, "check_secrets"):
                config.load(root)


class NamesTests(unittest.TestCase):
    def test_comments_and_blank_lines_are_not_names(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / ".publication-names").write_text(
                "# a comment\n\n  Someservice  \nOther\n", encoding="utf-8"
            )
            names = config.ExposureConfig().names(root)

        self.assertEqual(("Someservice", "Other"), names)

    def test_an_absent_file_declares_nothing(self) -> None:
        """A clone is expected not to carry it; the structural rules still run."""
        with tempfile.TemporaryDirectory() as name:
            self.assertEqual((), config.ExposureConfig().names(Path(name)))


if __name__ == "__main__":
    unittest.main()
