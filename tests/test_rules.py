from __future__ import annotations

import unittest

from releasekit.exposure import rules


class HomeDirectoryTests(unittest.TestCase):
    def test_plain_windows_path_is_a_finding(self) -> None:
        found = rules.kinds_in_text(r"C:\Users\someone\AppData\node.exe")

        self.assertIn(rules.HOME_DIRECTORY, found)

    def test_escaped_windows_path_is_a_finding(self) -> None:
        """The regression this whole gate exists for.

        A path inside JSON or a quoted command arrives with doubled backslashes. A
        pattern that matched one separator reported a clean repository while four
        absolute home paths sat in a tracked settings file.
        """
        payload = '{"command": "python \\"C:\\\\Users\\\\someone\\\\tool.py\\""}'

        self.assertIn(rules.HOME_DIRECTORY, rules.kinds_in_text(payload))

    def test_posix_home_is_a_finding(self) -> None:
        self.assertIn(rules.HOME_DIRECTORY, rules.kinds_in_text('cwd="/home/someone/src"'))

    def test_placeholder_users_are_not_findings(self) -> None:
        for path in (r"C:\Users\runneradmin\work", "/home/example/project"):
            with self.subTest(path=path):
                self.assertNotIn(rules.HOME_DIRECTORY, rules.kinds_in_text(path))

    def test_the_allowed_list_is_caller_supplied(self) -> None:
        found = rules.kinds_in_text(r"C:\Users\buildbot\x", allowed_users={"buildbot"})

        self.assertNotIn(rules.HOME_DIRECTORY, found)


class EscapingPathTests(unittest.TestCase):
    def test_a_climb_into_a_sibling_is_a_finding(self) -> None:
        found = rules.kinds_in_text('args = ["-File", "../other-checkout/tool.ps1"]')

        self.assertIn(rules.ESCAPES_REPOSITORY, found)

    def test_a_climb_behind_a_variable_escapes_from_the_root_not_the_file(self) -> None:
        """The variable names the project root, so nesting the file cannot cancel the climb."""
        found = rules.kinds_in_text(
            "python ${PROJECT_DIR}/../neighbour/adapter.py",
            relative_path=".someclient/settings.json",
        )

        self.assertIn(rules.ESCAPES_REPOSITORY, found)

    def test_a_link_that_climbs_and_comes_back_inside_is_not_a_finding(self) -> None:
        """Ordinary documentation. Flagging it is how a gate earns its way to being off."""
        found = rules.kinds_in_text(
            "see [the view](../../web/src/App.vue) for the markup",
            relative_path="docs/audits/review.md",
        )

        self.assertNotIn(rules.ESCAPES_REPOSITORY, found)

    def test_the_same_link_from_the_root_does_escape(self) -> None:
        found = rules.kinds_in_text(
            "see [the view](../../web/src/App.vue)", relative_path="README.md"
        )

        self.assertIn(rules.ESCAPES_REPOSITORY, found)


class DeclaredNameTests(unittest.TestCase):
    def test_a_declared_name_is_a_finding(self) -> None:
        found = rules.kinds_in_text("routed through Someservice", names=("Someservice",))

        self.assertIn(rules.DECLARED_NAME, found)

    def test_nothing_is_declared_by_default(self) -> None:
        """The tool ships no list: one that did would publish what it protects."""
        found = rules.kinds_in_text("routed through Someservice")

        self.assertNotIn(rules.DECLARED_NAME, found)


class PathKindTests(unittest.TestCase):
    def test_a_forbidden_extension_is_a_finding(self) -> None:
        self.assertIn(rules.FORBIDDEN_KIND, rules.kinds_in_path("state/store.sqlite3"))

    def test_ordinary_source_is_not(self) -> None:
        self.assertEqual(set(), rules.kinds_in_path("src/main.go"))

    def test_the_caller_may_replace_the_suffix_list(self) -> None:
        self.assertEqual(set(), rules.kinds_in_path("a.key", forbidden_suffixes=[".zip"]))
        self.assertIn(rules.FORBIDDEN_KIND, rules.kinds_in_path("a.zip", forbidden_suffixes=[".zip"]))


if __name__ == "__main__":
    unittest.main()
