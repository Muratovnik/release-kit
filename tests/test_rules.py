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

    def test_a_declared_placeholder_adds_to_the_defaults_rather_than_replacing_them(self) -> None:
        """Declaring one must not cost the built-in ones; the first version did."""
        found = rules.kinds_in_text(r"C:\Users\alice\x", allowed_users={"buildbot"})

        self.assertNotIn(rules.HOME_DIRECTORY, found)

    def test_an_account_under_a_reserved_example_domain_needs_no_declaration(self) -> None:
        """RFC 2606 and 6761 reserve these, so they are placeholders by definition."""
        for path in (
            r"C:\Users\alice@example.com\x",
            "/home/someone@example.org/x",
            "/home/dev@company.invalid/x",
            "/home/qa@host.test/x",
        ):
            with self.subTest(path=path):
                self.assertNotIn(rules.HOME_DIRECTORY, rules.kinds_in_text(path))

    def test_a_real_looking_domain_is_not_reserved(self) -> None:
        found = rules.kinds_in_text("/home/dev@acme.com/x")

        self.assertIn(rules.HOME_DIRECTORY, found)

    def test_a_project_placeholder_is_declared_by_the_project(self) -> None:
        """Only the project knows that its domain calls its example account this."""
        text = "path: 'C:\\\\Users\\\\Player\\\\Documents'"

        self.assertIn(rules.HOME_DIRECTORY, rules.kinds_in_text(text))
        self.assertNotIn(rules.HOME_DIRECTORY, rules.kinds_in_text(text, allowed_users={"player"}))


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

    def test_a_path_that_climbs_and_comes_back_inside_is_not_a_finding(self) -> None:
        """Ordinary. Flagging it is how a gate earns its way to being switched off."""
        found = rules.kinds_in_text(
            'run "../../web/build.sh" from here', relative_path="tools/config/task.json"
        )

        self.assertNotIn(rules.ESCAPES_REPOSITORY, found)

    def test_the_same_path_from_the_root_does_escape(self) -> None:
        found = rules.kinds_in_text('run "../../web/build.sh"', relative_path="task.json")

        self.assertIn(rules.ESCAPES_REPOSITORY, found)

    def test_prose_may_print_a_path_that_does_not_resolve_here(self) -> None:
        """Documenting an arrangement is not making a machine-local assumption.

        A README showing `private_root = "../thing-private"` describes a layout; it
        does not depend on one. Reporting it would fire on every project that
        documents the arrangement this tool configures.
        """
        text = 'private_root = "../thing-private"'

        self.assertIn(rules.ESCAPES_REPOSITORY, rules.kinds_in_text(text, relative_path="a.toml"))
        self.assertNotIn(
            rules.ESCAPES_REPOSITORY, rules.kinds_in_text(text, relative_path="README.md")
        )

    def test_prose_is_still_held_to_the_other_rules(self) -> None:
        found = rules.kinds_in_text(r"install into C:\Users\someone\tools", relative_path="a.md")

        self.assertIn(rules.HOME_DIRECTORY, found)

    def test_release_kit_config_defers_overlay_paths_to_the_exact_verifier(self) -> None:
        text = 'private_root = "../example-private"\n'

        self.assertNotIn(
            rules.ESCAPES_REPOSITORY,
            rules.kinds_in_text(text, relative_path="relkit.toml"),
        )


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

    def test_the_caller_adds_to_the_suffix_list(self) -> None:
        self.assertIn(
            rules.FORBIDDEN_KIND, rules.kinds_in_path("a.zip", forbidden_suffixes=[".zip"])
        )
        self.assertIn(
            rules.FORBIDDEN_KIND, rules.kinds_in_path("a.key", forbidden_suffixes=[".zip"])
        )


if __name__ == "__main__":
    unittest.main()
