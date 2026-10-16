from __future__ import annotations

import random
import re
import threading
import unicodedata
import unittest
from unittest.mock import patch

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

    def test_the_current_windows_default_account_is_not_a_placeholder(self) -> None:
        found = rules.kinds_in_text(r"C:\Users\User\Desktop\private.txt")

        self.assertIn(rules.HOME_DIRECTORY, found)

    def test_compatibility_unicode_does_not_hide_a_home_path(self) -> None:
        found = rules.kinds_in_text("Ｃ:\\Ｕｓｅｒｓ\\User\\private.txt")

        self.assertIn(rules.HOME_DIRECTORY, found)

    def test_windows_compatibility_and_root_homes_are_findings(self) -> None:
        paths = (
            "/mnt/c/Users/someone/private.txt",
            "/cygdrive/c/Users/someone/private.txt",
            "/root/private.txt",
            "/var/home/someone/private.txt",
            "file:///home/someone/private.txt",
            "file://localhost/home/someone/private.txt",
            "file://workstation/Users/someone/private.txt",
            r"file:\/\/\/home\/someone\/private.txt",
            r"\\server\Users\someone\private.txt",
            r"\\server\C$\Users\someone\private.txt",
        )

        for path in paths:
            with self.subTest(path=path):
                self.assertIn(rules.HOME_DIRECTORY, rules.kinds_in_text(path))

    def test_an_https_path_that_resembles_a_user_route_is_not_a_home_path(self) -> None:
        found = rules.kinds_in_text("https://example.com/Users/someone/profile")

        self.assertNotIn(rules.HOME_DIRECTORY, found)

    def test_placeholder_users_are_not_findings(self) -> None:
        for path in (r"C:\Users\runneradmin\work", "/home/example/project"):
            with self.subTest(path=path):
                self.assertNotIn(rules.HOME_DIRECTORY, rules.kinds_in_text(path))

    def test_the_allowed_list_is_caller_supplied(self) -> None:
        found = rules.kinds_in_text(r"C:\Users\buildbot\x", allowed_users={"buildbot"})

        self.assertNotIn(rules.HOME_DIRECTORY, found)

    def test_a_declared_placeholder_adds_to_the_defaults_rather_than_replacing_them(self) -> None:
        """Declaring one must not cost the built-in ones; the first version did."""
        found = rules.kinds_in_text(r"C:\Users\runneradmin\x", allowed_users={"buildbot"})

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

    def test_a_climb_at_the_start_of_a_file_is_a_finding(self) -> None:
        found = rules.kinds_in_text("../other-checkout/tool.ps1", relative_path="link")

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


class PrivateValueTests(unittest.TestCase):
    def test_a_declared_name_is_a_finding(self) -> None:
        found = rules.kinds_in_text("routed through Someservice", names=("Someservice",))

        self.assertIn(rules.DECLARED_NAME, found)

    def test_nothing_is_declared_by_default(self) -> None:
        """The tool ships no list: one that did would publish what it protects."""
        found = rules.kinds_in_text("routed through Someservice")

        self.assertNotIn(rules.DECLARED_NAME, found)


class SemanticPolicyTests(unittest.TestCase):
    def test_an_owner_workflow_is_distinct_from_a_product_provider(self) -> None:
        found = rules.kinds_in_text(
            "Someservice runs the maintainer workflow",
            owner_workflows=("Someservice",),
        )

        self.assertIn(rules.OWNER_WORKFLOW, found)

    def test_invisible_formatting_cannot_split_an_owner_workflow(self) -> None:
        found = rules.kinds_in_text(
            "Owner\u200bWorkbench internal workflow",
            owner_workflows=("OwnerWorkbench",),
        )

        self.assertIn(rules.OWNER_WORKFLOW, found)

    def test_an_owner_pattern_can_classify_personal_data_without_shipping_it(self) -> None:
        pattern = rules.PrivatePattern(
            name="opaque-record",
            kind=rules.PERSONAL_DATA,
            expression=r"\brec_[0-9]{4}\b",
        )

        found = rules.kinds_in_text("record rec_1234", private_patterns=(pattern,))

        self.assertIn(rules.PERSONAL_DATA, found)

    def test_internal_planning_markers_are_structural(self) -> None:
        examples = ("card: 123\n", "fixed by card #123", "<!-- kb:plan-anchor -->")

        for text in examples:
            with self.subTest(text=text):
                found = rules.kinds_in_text(text, forbid_internal_planning=True)
                self.assertIn(rules.INTERNAL_PLANNING, found)

    def test_ai_attribution_targets_machine_trailers_not_human_coauthors(self) -> None:
        machine = "Co-" + "Authored-By: " + "Clau" + "de <bot@example.invalid>"
        human = "Co-Authored-By: Example Writer <writer@example.invalid>"

        self.assertIn(
            rules.AI_ATTRIBUTION,
            rules.kinds_in_text(machine, forbid_ai_attribution=True),
        )
        self.assertNotIn(
            rules.AI_ATTRIBUTION,
            rules.kinds_in_text(human, forbid_ai_attribution=True),
        )

    def test_ai_attribution_inside_a_source_comment_is_still_a_finding(self) -> None:
        for text in (
            "# Generated by Codex",
            "// Reviewed with coding agent",
            "# Generated with OpenAI Codex",
            "// Created using Codex",
            "This file was generated by Codex.",
        ):
            with self.subTest(text=text):
                self.assertIn(
                    rules.AI_ATTRIBUTION,
                    rules.kinds_in_text(text, forbid_ai_attribution=True),
                )

    def test_an_ordinary_code_generator_is_not_misclassified_as_an_ai_agent(self) -> None:
        self.assertNotIn(
            rules.AI_ATTRIBUTION,
            rules.kinds_in_text("Generated by protoc", forbid_ai_attribution=True),
        )

    def test_an_agent_identity_is_recognized_but_the_hosting_platform_is_not(self) -> None:
        # The committer of a pull request's test merge. The vendor list names a product
        # of the same host, so the host's own name must not match it.
        self.assertFalse(rules.is_ai_identity("GitHub <noreply@github.com>"))
        self.assertFalse(rules.is_ai_identity("Example Writer <writer@example.invalid>"))
        for identity in (
            "Codex <agent@example.invalid>",
            "copilot-swe-agent[bot] <agent@example.invalid>",
        ):
            with self.subTest(identity=identity):
                self.assertTrue(rules.is_ai_identity(identity))

    def test_a_real_installation_observation_is_detected_only_when_enabled(self) -> None:
        text = "The real installation has 42 readable fixture folders."

        self.assertNotIn(rules.MACHINE_OBSERVATION, rules.kinds_in_text(text))
        self.assertIn(
            rules.MACHINE_OBSERVATION,
            rules.kinds_in_text(text, forbid_machine_observations=True),
        )

    def test_a_product_provider_is_limited_to_declared_surfaces(self) -> None:
        providers = {"Someservice": ("src/*", "docs/*")}

        allowed = rules.kinds_in_text(
            "Someservice supplies records", relative_path="docs/provider.md", providers=providers
        )
        denied = rules.kinds_in_text(
            "Someservice supplies records", relative_path="AGENTS.md", providers=providers
        )

        self.assertNotIn(rules.PROVIDER_SURFACE, allowed)
        self.assertIn(rules.PROVIDER_SURFACE, denied)

    def test_provider_declaration_does_not_reject_its_own_config(self) -> None:
        providers = {"Someservice": ("src/*",)}
        config = "[exposure.providers.Someservice]\nallowed_surfaces = ['src/*']\n"

        found = rules.kinds_in_text(config, relative_path="relkit.toml", providers=providers)

        self.assertNotIn(rules.PROVIDER_SURFACE, found)


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


FLAGS = {
    "forbid_ai_attribution": True,
    "forbid_internal_planning": True,
    "forbid_machine_observations": True,
}
GATE_NAMES = ("Some Wrapped Value", "Someservice")
GATE_WORKFLOWS = ("Invented Workflow",)
DOTLESS_I = chr(0x131)
DOTTED_CAPITAL_I = chr(0x130)
FULLWIDTH_USERS = "".join(chr(ord(c) + 0xFEE0) for c in "Users")


def _all_gates() -> list[rules._Gate]:
    return [
        *(gate for _pattern, gate in rules.USER_ROOT_GATES),
        *(gate for _pattern, gate in rules.ROOT_HOME_GATES),
        *rules.AI_ATTRIBUTION_GATES,
        *rules.INTERNAL_PLANNING_GATES,
        *rules.MACHINE_OBSERVATION_GATES,
    ]


def _findings(text: str, relative_path: str = "a.txt") -> dict[str, str]:
    return rules.text_findings(
        text,
        relative_path=relative_path,
        names=GATE_NAMES,
        owner_workflows=GATE_WORKFLOWS,
        **FLAGS,
    )


def _unfiltered(text: str, relative_path: str = "a.txt") -> dict[str, str]:
    """What the expressions find when no prefilter stands in front of them."""
    with (
        patch.object(rules._Gate, "admits", lambda self, haystack: True),
        patch.object(rules, "CLIMBING_PATH_NEEDLE", ""),
    ):
        return _findings(text, relative_path)


class PrefilterTests(unittest.TestCase):
    """A prefilter only rules out; running every expression is the oracle."""

    def test_a_literal_in_the_text_does_not_make_a_finding_of_it(self) -> None:
        for text, relative_path in (
            ("https://example.com/Users/someone/profile", "a.txt"),
            ("list the users/ and home/ folders", "a.txt"),
            ("ordinary prose about /rooted paths and file: names", "a.txt"),
            ("the card game, and a #hashtag", "a.txt"),
            ("kb: notes and <!-- a comment -->", "a.txt"),
            ("Reviewed by the agent framework, generated code from codex samples", "a.txt"),
            ("Signed-off-by: Example Writer <writer@example.invalid>", "a.txt"),
            ("the workstation is fine and the installation went well", "a.txt"),
            ("see ../x here, which stays inside the repository", "dir/sub/c.txt"),
        ):
            with self.subTest(text=text):
                self.assertEqual({}, _unfiltered(text, relative_path))
                self.assertEqual({}, _findings(text, relative_path))

    def test_the_dotted_and_dotless_i_still_match_a_literal_with_an_i(self) -> None:
        # Casefolding turns U+0130 into an i plus a combining dot and leaves U+0131 as it
        # is, so a plain casefolded copy lacks the `devin`, `gemini` and `workstation`
        # that the ignore-case expressions still match in these spellings.
        for text, kind, literal in (
            (
                f"# Co-Authored-By: DEV{DOTTED_CAPITAL_I}N <x@example.invalid>",
                rules.AI_ATTRIBUTION,
                "devin",
            ),
            (f"# Generated by Gemin{DOTLESS_I}", rules.AI_ATTRIBUTION, "gemini"),
            (f"on my WORKSTAT{DOTLESS_I}ON everything", rules.MACHINE_OBSERVATION, "workstation"),
        ):
            with self.subTest(text=text):
                self.assertNotIn(literal, text.casefold())
                self.assertIn(kind, _unfiltered(text))
                self.assertIn(kind, _findings(text))

    def test_the_folded_text_keeps_every_spelling_an_ignore_case_match_accepts(self) -> None:
        """The soundness of every needle that contains a letter, for this Python.

        A needle is looked for in casefolded text, so it is only a necessary condition
        if each character that an ignore-case expression equates with a needle letter
        casefolds, after the two repairs, to exactly that letter. Which characters
        those are depends on the interpreter's tables, so this sweeps all of Unicode.
        """
        characters = sorted(
            {c for gate in _all_gates() for group in gate.groups for n in group for c in n}
        )
        charset = re.compile("(?i)[" + "".join(re.escape(c) for c in characters) + "]")
        singles = {c: re.compile("(?i)" + re.escape(c)) for c in characters}
        checked = 0
        for code in range(0x80, 0x110000):
            if 0xD800 <= code <= 0xDFFF:
                continue
            character = chr(code)
            if not charset.fullmatch(character):
                continue
            if (
                unicodedata.normalize("NFKC", character) != character
                or unicodedata.category(character) == "Cf"
            ):
                continue  # Never present in the text the expressions read.
            checked += 1
            equated = [c for c, pattern in singles.items() if pattern.fullmatch(character)]
            self.assertEqual(1, len(equated), hex(code))
            self.assertEqual(equated[0], rules._gate_text(character.casefold()), hex(code))
        self.assertGreaterEqual(checked, 2)  # at least the two letters repaired above

    def test_the_ai_vendor_needles_cover_every_alternative_of_the_expression(self) -> None:
        expression = rules.AI_VENDOR
        self.assertTrue(expression.startswith("(?:") and expression.endswith(")"))
        alternatives: list[str] = []
        depth = 0
        current = ""
        for character in expression[3:-1]:
            depth += character == "("
            depth -= character == ")"
            if character == "|" and depth == 0:
                alternatives.append(current)
                current = ""
            else:
                current += character
        alternatives.append(current)

        for alternative in alternatives:
            with self.subTest(alternative=alternative):
                self.assertTrue(
                    any(needle in alternative.lower() for needle in rules.AI_VENDOR_NEEDLES)
                )
        for needle in rules.AI_VENDOR_NEEDLES:
            with self.subTest(needle=needle):
                self.assertTrue(any(needle in alternative.lower() for alternative in alternatives))

    def test_filtered_and_unfiltered_rules_agree_on_generated_text(self) -> None:
        # fmt: off
        fragments = (
            "C:\\Users\\", "C:/Users/", "\\\\server\\C$\\Users\\", "Documents and Settings\\",
            "/home/", "/Users/", "/root", "/var/home/", "/mnt/c/Users/", "file:///", "file://host/",
            "Users", "someone", "example", "runner", "\\/", "../", "../../", "${PROJECT_DIR}/",
            "Co-Authored-By:", "co-authored-by:", "Tested-By:", "Generated by", "created using",
            "written with", "AI assisted by", "Claude", "anthropic claude", "OpenAI Codex",
            "coding agent", "AI   agent", "agent", "Cursor", "windsurf", "devin",
            f"DEV{DOTTED_CAPITAL_I}N", f"Gemin{DOTLESS_I}", "card", "card:", "card #", "#", "42",
            "<!--", "kb:", "anchor", "-->", "workstation", "installation", "on my workstation", "the real installation has", "this workstation contains", "on my", "the real",
            "has", "contains", f"WORKSTAT{DOTLESS_I}ON", FULLWIDTH_USERS, chr(0xDF), chr(0x17F), chr(0x212A), "Someservice",
            "Some", "Wrapped", "Value", "Invented Workflow", " ", "  ", "\n", "\t", ":", "/", "\\",
        )
        # fmt: on
        generator = random.Random(20261016)
        paths = ("a.txt", "dir/b.py", "notes.md", "relkit.toml", "dir/c.json")
        kinds: set[str] = set()
        for _ in range(4000):
            text = "".join(generator.choice(fragments) for _ in range(generator.randint(1, 14)))
            relative_path = generator.choice(paths)
            expected = _unfiltered(text, relative_path)
            self.assertEqual(expected, _findings(text, relative_path), repr(text))
            kinds.update(expected)

        # The generator has to reach every rule a prefilter stands in front of.
        for kind in (
            rules.HOME_DIRECTORY,
            rules.ESCAPES_REPOSITORY,
            rules.AI_ATTRIBUTION,
            rules.INTERNAL_PLANNING,
            rules.MACHINE_OBSERVATION,
            rules.DECLARED_NAME,
            rules.OWNER_WORKFLOW,
        ):
            self.assertIn(kind, kinds)

    def test_a_wrapped_value_is_found_only_when_all_its_words_are_present(self) -> None:
        names = ("Some Wrapped Value",)

        self.assertTrue(rules.contains_wrapped_declared_name("a some\n  wrapped\tvalue b", names))
        self.assertTrue(rules.contains_wrapped_declared_name("SOME   WRAPPED\n\nVALUE", names))
        # Plain occurrences are the declared-name rule's business, not the wrapped one's.
        self.assertFalse(rules.contains_wrapped_declared_name("some wrapped value", names))
        # Every word is present, but not next to the others.
        self.assertFalse(rules.contains_wrapped_declared_name("some value wrapped", names))
        self.assertFalse(rules.contains_wrapped_declared_name("some wrapped", names))
        self.assertFalse(rules.contains_wrapped_declared_name("wrapped value some", names))

    def test_a_text_without_a_declared_phrase_is_never_collapsed(self) -> None:
        """Collapsing whitespace is a pass over the whole file, needed only for a candidate."""
        names = ("Some Wrapped Value",)
        text = "plain text\nwith several lines\n" * 3

        rules.contains_wrapped_declared_name(text, names)
        self.assertIsNone(rules._prepare(text)._collapsed)

        candidate = "text mentioning some\nwrapped\nvalue"
        self.assertTrue(rules.contains_wrapped_declared_name(candidate, names))
        self.assertIsNotNone(rules._prepare(candidate)._collapsed)

    def test_each_thread_keeps_its_own_prepared_texts(self) -> None:
        """One file judged on one thread is not evicted by the small texts of another."""
        large = "x" * 1000
        prepared = rules._prepare(large)

        def work() -> None:
            for index in range(50):
                rules._prepare(f"message {index}")

        thread = threading.Thread(target=work)
        thread.start()
        thread.join()

        self.assertIs(prepared, rules._prepare(large))


if __name__ == "__main__":
    unittest.main()
