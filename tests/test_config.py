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
                '[exposure]\nnames_file = "private-names"\n\n'
                '[exposure.baseline]\n"a/b.json" = ["home-directory"]\n',
                encoding="utf-8",
            )
            settings = config.load(root)

        self.assertEqual("private-names", settings.exposure.names_file)
        self.assertEqual({"a/b.json": ["home-directory"]}, settings.exposure.baseline)

    def test_a_baseline_that_is_not_a_mapping_of_lists_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                '[exposure]\nbaseline = { "a" = "home-directory" }\n', encoding="utf-8"
            )
            with self.assertRaises(config.ConfigError):
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
