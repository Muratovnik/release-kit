"""Version vectors follow https://semver.org/spec/v2.0.0.html, sections 9–11."""

import unittest
from itertools import pairwise

from releasekit import semver


class SemVerTests(unittest.TestCase):
    def test_specification_precedence_vectors(self):
        ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
            "2.0.0",
            "2.1.0",
            "2.1.1",
        ]
        versions = [semver.parse(value) for value in ordered]
        self.assertEqual(ordered, [str(value) for value in versions])
        for left, right in pairwise(versions):
            with self.subTest(left=str(left), right=str(right)):
                self.assertLess(left.precedence_key, right.precedence_key)

    def test_build_metadata_changes_identity_without_advancing_precedence(self):
        plain = semver.parse("1.2.3-rc.2")
        first = semver.parse("1.2.3-rc.2+build.001")
        second = semver.parse("1.2.3-rc.2+build.002")
        self.assertNotEqual(first, second)
        self.assertNotEqual(plain, first)
        self.assertEqual(plain.precedence_key, first.precedence_key)
        self.assertEqual(first.precedence_key, second.precedence_key)
        self.assertEqual((1, 2, 3), first.core)
        self.assertTrue(first.is_prerelease)
        self.assertFalse(semver.parse("1.2.3+build.001").is_prerelease)

    def test_valid_identifiers_and_ascii_case_order(self):
        for text in ["0.0.0", "1.0.0-rc", "1.0.0-0.3.7", "1.0.0-x-y-z.--", "1.0.0+001"]:
            with self.subTest(text=text):
                self.assertEqual(text, str(semver.parse(text)))
        self.assertLess(
            semver.parse("1.0.0-RC").precedence_key,
            semver.parse("1.0.0-rc").precedence_key,
        )

    def test_rejects_invalid_numbers_identifiers_and_boundary_syntax(self):
        for text in [
            "1.2",
            "01.2.3",
            "1.02.3",
            "1.2.03",
            "1.2.3-01",
            "1.2.3-rc.01",
            "1.2.3-",
            "1.2.3-rc..1",
            "1.2.3+",
            "1.2.3+a..b",
            "1.2.3-rc_1",
            "1.2.3-候选",
            "1.2.3١",
            "١.2.3",
            "1.2.3-١",
            "v1.2.3",
            "V1.2.3",
            " 1.2.3",
            "1.2.3\n",
            "1.2.3rc1",
            "1.2.3+meta+more",
            "",
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                semver.parse(text)
