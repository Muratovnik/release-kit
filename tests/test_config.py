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

    def test_it_reads_public_policy_without_a_private_location(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                '[exposure]\nbetterleaks_config = "security.toml"\n'
                'allowed_identities = ["Example Maintainer <owner@example.invalid>"]\n'
                "forbid_png_metadata = true\n\n"
                '[exposure.baseline]\n"a/b.json" = ["home-directory"]\n',
                encoding="utf-8",
            )
            settings = config.load(root)

        self.assertEqual("security.toml", settings.exposure.betterleaks_config)
        self.assertEqual(
            ["Example Maintainer <owner@example.invalid>"],
            settings.exposure.allowed_identities,
        )
        self.assertTrue(settings.exposure.forbid_png_metadata)
        self.assertEqual({"a/b.json": ["home-directory"]}, settings.exposure.baseline)

    def test_it_reads_semantic_provider_and_provenance_policy(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                "[exposure]\n"
                "forbid_ai_attribution = true\n"
                "forbid_internal_planning = true\n"
                "forbid_machine_observations = true\n"
                'provenance_required = ["tests/generated/*"]\n\n'
                "[exposure.providers.Someservice]\n"
                'role = "product-data-provider"\n'
                'allowed_surfaces = ["src/*", "docs/*"]\n\n'
                "[exposure.provenance]\n"
                '"tests/generated/*" = "synthetic"\n',
                encoding="utf-8",
            )

            settings = config.load(root).exposure

        self.assertTrue(settings.forbid_ai_attribution)
        self.assertTrue(settings.forbid_internal_planning)
        self.assertTrue(settings.forbid_machine_observations)
        self.assertEqual(["src/*", "docs/*"], settings.providers["Someservice"].allowed_surfaces)
        self.assertEqual({"tests/generated/*": "synthetic"}, settings.provenance)

    def test_provider_and_provenance_vocabularies_are_closed(self) -> None:
        cases = (
            (
                (
                    "[exposure.providers.Someservice]\n"
                    'role = "owner-workflow"\n'
                    'allowed_surfaces = ["src/*"]\n'
                ),
                "role",
            ),
            (
                ('[exposure.provenance]\n"tests/*" = "copied-from-owner"\n'),
                "provenance",
            ),
        )
        for payload, message in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                (root / config.CONFIG_NAME).write_text(payload, encoding="utf-8")
                with self.assertRaisesRegex(config.ConfigError, message):
                    config.load(root)

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

    def test_a_public_names_file_location_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                '[exposure]\nnames_file = "../private/.publication-private-values"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(config.ConfigError, "names_file"):
                config.load(root)

    def test_a_public_overlay_location_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                '[overlay]\nprivate_root = "../private"\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(config.ConfigError, "overlay"):
                config.load(root)

    def test_the_secret_engine_config_must_stay_inside_the_repository(self) -> None:
        for value in (
            "../private/.betterleaks.toml",
            "C:/private/betterleaks.toml",
            "config/policy.toml:private",
            "config/NUL",
        ):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                (root / config.CONFIG_NAME).write_text(
                    f'[exposure]\nbetterleaks_config = "{value}"\n', encoding="utf-8"
                )

                with self.assertRaisesRegex(config.ConfigError, "betterleaks_config"):
                    config.load(root)

    def test_glob_policy_paths_cannot_escape_or_use_platform_aliases(self) -> None:
        for key, value in (
            ("exclude", "../private/*"),
            ("provenance_required", "reports/CON/*"),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                (root / config.CONFIG_NAME).write_text(
                    f'[exposure]\n{key} = ["{value}"]\n', encoding="utf-8"
                )

                with self.assertRaisesRegex(config.ConfigError, key):
                    config.load(root)

    def test_empty_or_malformed_private_suffixes_are_refused(self) -> None:
        for value in ("", "local.md", " .local.md"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                (root / config.CONFIG_NAME).write_text(
                    f'[exposure]\nprivate_suffixes = ["{value}"]\n', encoding="utf-8"
                )

                with self.assertRaisesRegex(config.ConfigError, "private_suffixes"):
                    config.load(root)

    def test_exact_private_surfaces_cannot_silently_use_glob_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / config.CONFIG_NAME).write_text(
                '[exposure]\nprivate_paths = [".someclient/*"]\n', encoding="utf-8"
            )

            with self.assertRaisesRegex(config.ConfigError, "exact repository path"):
                config.load(root)


if __name__ == "__main__":
    unittest.main()
