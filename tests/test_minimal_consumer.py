"""A project adopts release-kit with one file: both engines have a built-in default."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from releasekit import cli, config, engines, generation, protection, toolchain

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
ORIGIN = "https://github.com/example-owner/example-project.git"


def environment() -> dict[str, str]:
    """The caller's environment minus anything that redirects Git, plus a quiet Git."""
    values = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    values.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(ROOT / "src"),
    )
    return values


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Example Maintainer", "-c", "user.email=m@example.invalid"]
        + list(arguments),
        cwd=root,
        env=environment(),
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    ).stdout


def repository(directory: Path, files: dict[str, str], *, origin: str = "") -> Path:
    root = directory / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: the first change")
    if origin:
        git(root, "remote", "add", "origin", origin)
    return root


class TemporaryRepository(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="minimal consumer ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.cases = 0

    def fresh(self) -> Path:
        """An empty directory for one more repository in the same test."""
        self.cases += 1
        path = self.directory / f"case-{self.cases}"
        path.mkdir()
        return path


class BetterleaksDefaultPolicyTests(TemporaryRepository):
    """Without the policy file the engine is given its maintained rules, explicitly."""

    def selected(self, root: Path, *, config_name: str, **scope: bool) -> tuple[str, Path]:
        """What the engine would be handed: the policy text and where it came from."""
        seen: dict[str, object] = {}

        def inspect(command, *, root: Path, **keywords):
            path = Path(command[command.index("--config") + 1])
            seen["path"] = path
            seen["text"] = path.read_text(encoding="utf-8")
            return 0

        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "_run", side_effect=inspect),
        ):
            code = engines.betterleaks(
                root,
                config=config_name,
                history=scope.get("history", False),
                staged=scope.get("staged", False),
                include_candidates=False,
                allow_download=False,
            )
        self.assertEqual(0, code)
        return seen["text"], seen["path"]

    def test_every_scope_uses_the_default_when_the_project_has_no_policy(self) -> None:
        root = repository(self.directory, {"app.py": "print('hello')\n"})
        for scope in ({}, {"history": True}, {"staged": True}):
            with self.subTest(scope=scope):
                text, path = self.selected(root, config_name=".betterleaks.toml", **scope)
                self.assertEqual(engines.BETTERLEAKS_DEFAULT_POLICY, text)
                self.assertEqual(
                    {"extend": {"useDefault": True}}, tomllib.loads(text), "no rules of our own"
                )
                self.assertFalse(path.is_relative_to(root / "app.py"))
                self.assertTrue(path.is_relative_to(root / ".git" / "relkit"))
        self.assertEqual("", git(root, "status", "--porcelain"), "nothing is left in the project")
        self.assertEqual([], list((root / ".git" / "relkit" / "tmp").iterdir()))

    def test_the_default_applies_to_a_configured_path_that_does_not_exist(self) -> None:
        root = repository(self.directory, {"app.py": "print('hello')\n"})
        text, _ = self.selected(root, config_name="security/secrets.toml")
        self.assertEqual(engines.BETTERLEAKS_DEFAULT_POLICY, text)

    def test_a_project_policy_is_used_instead_and_never_merged_with_the_default(self) -> None:
        policy = 'title = "project policy"\n\n[extend]\nuseDefault = true\n'
        root = repository(self.directory, {".betterleaks.toml": policy, "app.py": "x = 1\n"})
        for scope in ({}, {"history": True}, {"staged": True}):
            with self.subTest(scope=scope):
                text, path = self.selected(root, config_name=".betterleaks.toml", **scope)
                self.assertEqual(policy, text)
                self.assertEqual(".betterleaks.toml", path.name)
                self.assertNotEqual(engines.BETTERLEAKS_DEFAULT_POLICY, text)

    def test_the_staged_scope_judges_the_index_not_the_worktree(self) -> None:
        # A policy that exists only in the worktree was never reviewed with the change.
        root = repository(self.directory, {"app.py": "x = 1\n"})
        (root / ".betterleaks.toml").write_text('title = "unstaged"\n', encoding="utf-8")
        text, _ = self.selected(root, config_name=".betterleaks.toml", staged=True)
        self.assertEqual(engines.BETTERLEAKS_DEFAULT_POLICY, text)


class GuardPinsAbsenceTests(TemporaryRepository):
    """The default must not become a way to slip a weaker policy past the guard."""

    def setUp(self) -> None:
        super().setUp()
        self.root = repository(
            self.directory,
            {
                "relkit.toml": (EXAMPLES / "minimal/relkit.toml").read_text(encoding="utf-8"),
                ".github/relkit.pyz": "stand-in\n",
            },
        )

    def test_an_absent_policy_is_pinned_as_absent(self) -> None:
        with patch.object(protection, "PUSH_AUDIT_VERSION", "0.0.0"):
            protection.install(self.root)
        pins = protection.recorded_digests(self.root)
        self.assertEqual(protection.ABSENT, pins[".betterleaks.toml"])
        self.assertIsNone(protection.problem(self.root))
        self.assertEqual([], protection.digest_changes(self.root))

    def test_a_policy_added_later_is_a_change_the_guard_refuses(self) -> None:
        with patch.object(protection, "PUSH_AUDIT_VERSION", "0.0.0"):
            protection.install(self.root)
        (self.root / ".betterleaks.toml").write_text("[extend]\nuseDefault = false\n")
        problem = protection.problem(self.root)
        self.assertIn(".betterleaks.toml: pinned <absent> -> current ", problem)
        # The installed hook, not this module, is what refuses a push.
        hook = protection.hook_path(self.root).read_text(encoding="utf-8")
        self.assertIn("expected = {", hook)
        self.assertIn("'.betterleaks.toml': ''", hook)

    def test_a_policy_that_exists_is_pinned_by_its_digest(self) -> None:
        text = "[extend]\nuseDefault = true\n"
        (self.root / ".betterleaks.toml").write_text(text, encoding="utf-8", newline="\n")
        with patch.object(protection, "PUSH_AUDIT_VERSION", "0.0.0"):
            protection.install(self.root)
        self.assertEqual(
            hashlib.sha256(text.encode()).hexdigest(),
            protection.recorded_digests(self.root)[".betterleaks.toml"],
        )

    def test_a_scan_that_is_switched_off_pins_nothing_about_the_policy(self) -> None:
        policy = self.root / "relkit.toml"
        policy.write_text(
            policy.read_text(encoding="utf-8").replace(
                "[exposure]\n", "[exposure]\ncheck_secrets = false\n"
            ),
            encoding="utf-8",
        )
        with patch.object(protection, "PUSH_AUDIT_VERSION", "0.0.0"):
            protection.install(self.root)
        self.assertNotIn(".betterleaks.toml", protection.recorded_digests(self.root))


class DefaultTemplateTests(TemporaryRepository):
    """Without cliff.toml the conventional-changelog profile brings its own template."""

    def policy(self, profile: str = "conventional-changelog", **generator: object):
        return config.ChangelogConfig(
            profile=profile,
            first_version="0.1.0",
            generator=config.GeneratorConfig(**(generator or {"engine": "git-cliff"})),
        )

    def drafted(self, root: Path, *, repository: str = "", **keywords: object):
        """Run a draft against a fake engine, recording the command and its template."""
        seen: dict[str, object] = {}
        real = generation.processes.run

        def run(command, **options):
            if command[0] == "git":
                return real(command, **options)
            seen["command"] = list(command)
            if "--config" in command:
                path = Path(command[command.index("--config") + 1])
                seen["path"] = path
                seen["text"] = path.read_text(encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, b"## [0.1.0]\n", b"")

        with (
            patch.object(generation.toolchain, "resolve", return_value=Path("git-cliff")),
            patch.object(generation.processes, "run", run),
        ):
            generation.draft(self.policy(**keywords), "0.1.0", root=root, repository=repository)
        return seen

    def test_the_template_is_passed_explicitly_and_names_the_origin_repository(self) -> None:
        root = repository(self.directory, {"app.py": "x = 1\n"}, origin=ORIGIN)
        seen = self.drafted(root)
        command = seen["command"]
        self.assertEqual(str(seen["path"]), command[command.index("--config") + 1])
        # The template reads no hosting metadata; the engine must not query the API for it.
        self.assertIn("--offline", command)
        remote = tomllib.loads(seen["text"])["remote"]["github"]
        self.assertEqual({"owner": "example-owner", "repo": "example-project"}, remote)
        self.assertTrue(seen["path"].is_relative_to(root / ".git" / "relkit"))
        self.assertEqual([], list((root / ".git" / "relkit" / "tmp").iterdir()), "no residue")
        self.assertEqual("", git(root, "status", "--porcelain"))

    def test_every_common_spelling_of_a_github_remote_is_understood(self) -> None:
        for remote in (
            "https://github.com/example-owner/example-project",
            "https://github.com/example-owner/example-project.git",
            "https://user@github.com/example-owner/example-project.git",
            "git@github.com:example-owner/example-project.git",
            "ssh://git@github.com/example-owner/example-project.git",
            "https://github.com/example-owner/example-project/",
        ):
            with self.subTest(remote=remote):
                root = repository(self.fresh(), {"a": "b"}, origin=remote)
                self.assertEqual(
                    "example-owner/example-project", generation.github_repository(root)
                )

    def test_a_declared_release_repository_wins_over_the_remote(self) -> None:
        root = repository(self.directory, {"app.py": "x = 1\n"}, origin=ORIGIN)
        seen = self.drafted(root, repository="declared/repository")
        remote = tomllib.loads(seen["text"])["remote"]["github"]
        self.assertEqual({"owner": "declared", "repo": "repository"}, remote)

    def test_a_remote_that_is_not_github_asks_for_a_template_instead_of_guessing(self) -> None:
        for origin in ("", "https://git.example.invalid/team/project.git", "../local/server.git"):
            with self.subTest(origin=origin):
                root = repository(self.fresh(), {"app.py": "x = 1\n"}, origin=origin)
                with self.assertRaisesRegex(generation.GenerationError, "origin.*cliff.toml"):
                    self.drafted(root)

    def test_a_project_template_is_never_replaced(self) -> None:
        for name in generation.CLIFF_CONFIG_NAMES:
            with self.subTest(name=name):
                root = repository(
                    self.fresh(), {name: "# the project's own template\n"}, origin=ORIGIN
                )
                self.assertNotIn("--config", self.drafted(root)["command"])

    def test_the_environment_choice_of_the_operator_is_respected(self) -> None:
        root = repository(self.directory, {"app.py": "x = 1\n"}, origin=ORIGIN)
        for name in generation.CLIFF_CONFIG_ENVIRONMENT:
            with self.subTest(name=name), patch.dict(os.environ, {name: "elsewhere.toml"}):
                self.assertNotIn("--config", self.drafted(root)["command"])

    def test_only_the_conventional_changelog_profile_with_the_engine_is_defaulted(self) -> None:
        root = repository(self.directory, {"app.py": "x = 1\n"}, origin=ORIGIN)
        for label, kwargs in (
            ("strict profile", {"profile": "strict"}),
            ("legacy profile", {"profile": "legacy"}),
            ("custom command", {"command": ("printer",)}),
        ):
            with self.subTest(label=label):
                self.assertFalse(generation.uses_default_template(self.policy(**kwargs), root))
        self.assertTrue(generation.uses_default_template(self.policy(), root))
        self.assertFalse(
            generation.uses_default_template(config.ChangelogConfig(profile="strict"), root)
        )

    def test_the_built_in_template_is_the_published_starter_and_only_differs_in_the_remote(
        self,
    ) -> None:
        starter = (EXAMPLES / "changelog/cliff.toml").read_text(encoding="utf-8")
        built_in = generation.default_cliff_config("example-owner/example-project")
        self.assertIn(generation.DEFAULT_CLIFF, starter)
        self.assertEqual(
            tomllib.loads(starter), tomllib.loads(built_in), "the starter and the default drifted"
        )
        self.assertEqual(
            {"owner": "example-owner", "repo": "example-project"},
            tomllib.loads(built_in)["remote"]["github"],
        )

    def test_the_cli_hands_the_declared_repository_to_the_draft(self) -> None:
        root = repository(
            self.directory,
            {
                "relkit.toml": (
                    '[changelog]\nprofile = "conventional-changelog"\nfirst_version = "0.1.0"\n\n'
                    '[changelog.generator]\nengine = "git-cliff"\n\n'
                    '[release]\nrepository = "declared/repository"\n'
                    'workflow = ".github/workflows/release.yml"\nrequired_jobs = ["publish"]\n'
                    'version_file = "VERSION"\nversion_pattern = "^(.+)$"\n'
                    'assets = ["application.bin", "SHA256SUMS"]\nchecksum_file = "SHA256SUMS"\n'
                    'checks = [["{python}", "check.py"]]\n'
                    'smoke = [["{python}", "smoke.py", "{assets}"]]\n'
                    'smoke_platforms = ["linux"]\n'
                ),
                "VERSION": "0.1.0\n",
            },
        )
        entry = (
            "## [0.1.0](https://github.com/declared/repository/releases/tag/v0.1.0) (2026-01-01)\n"
            "\n### Features\n\n- the first change "
            "([abcdef0](https://github.com/declared/repository/commit/abcdef0))\n"
        )
        with (
            patch.object(cli.generation, "draft", return_value=entry) as draft,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            code = cli.main(["notes", "0.1.0", "--draft", "--root", str(root)])
        self.assertEqual(0, code)
        self.assertEqual("declared/repository", draft.call_args.kwargs["repository"])


def engine_or_skip(name: str, root: Path) -> None:
    try:
        toolchain.resolve(name, root=root, allow_download=False)
    except (toolchain.ToolchainError, OSError) as error:
        raise unittest.SkipTest(f"{name} is not available offline: {error}") from error


class MinimalExampleTests(TemporaryRepository):
    """The shipped example, with only code beside it, through the real engines."""

    def setUp(self) -> None:
        super().setUp()
        self.example = EXAMPLES / "minimal/relkit.toml"

    def test_the_example_is_one_file_and_a_valid_policy(self) -> None:
        self.assertEqual(
            ["relkit.toml"], sorted(path.name for path in self.example.parent.iterdir())
        )
        policy = config.load(self.example.parent)
        self.assertEqual("conventional-changelog", policy.changelog.profile)
        self.assertEqual("git-cliff", policy.changelog.generator.engine)
        self.assertTrue(policy.exposure.check_secrets)
        self.assertTrue(
            generation.uses_default_template(policy.changelog, self.example.parent),
            "the example must rely on the built-in template",
        )

    def adopter(self) -> Path:
        # `.gitignore` is the project's own file: it keeps scanner downloads out of Git.
        root = repository(
            self.directory,
            {
                "relkit.toml": self.example.read_text(encoding="utf-8"),
                ".gitignore": ".cache/\n",
                "src/app.py": "def main():\n    return 'hello'\n",
            },
            origin=ORIGIN,
        )
        (root / ".cache").mkdir()
        self.assertFalse((root / ".betterleaks.toml").exists())
        self.assertFalse((root / "cliff.toml").exists())
        return root

    def relkit(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", "-m", "releasekit.cli", *arguments, "--root", str(root)],
            cwd=root,
            env=environment(),
            capture_output=True,
            encoding="utf-8",
            timeout=600,
            check=False,
        )

    def test_it_audits_and_drafts_notes_with_no_other_policy_file(self) -> None:
        root = self.adopter()
        for name in ("betterleaks", "lychee", "git-cliff"):
            engine_or_skip(name, root)

        audited = self.relkit(root, "--json", "audit", "--no-download")
        report = json.loads(audited.stdout)
        self.assertEqual(0, audited.returncode, audited.stdout + audited.stderr)
        self.assertEqual(0, report["data"]["engines"]["betterleaks"])

        drafted = self.relkit(root, "notes", "0.1.0", "--draft")
        self.assertEqual(0, drafted.returncode, drafted.stdout + drafted.stderr)
        heading = drafted.stdout.splitlines()[0]
        self.assertRegex(
            heading,
            r"^## \[0\.1\.0\]\(https://github\.com/example-owner/example-project/"
            r"releases/tag/v0\.1\.0\) \(\d{4}-\d{2}-\d{2}\)$",
        )
        self.assertIn("### Features", drafted.stdout)
        self.assertIn("the first change", drafted.stdout)
        self.assertEqual("", git(root, "status", "--porcelain"), "the draft writes nothing")

    def test_the_default_secret_rules_still_reject_a_credential(self) -> None:
        root = self.adopter()
        engine_or_skip("betterleaks", root)
        # Built at run time from a never-issued value, so no credential-shaped literal
        # sits in the source or the history of this repository.
        token = "ghp_" + hashlib.sha256(b"release-kit minimal example credential").hexdigest()[:36]
        (root / "src" / "leak.py").write_text(f"github_token = '{token}'\n", encoding="utf-8")

        audited = self.relkit(root, "--json", "audit", "--no-download")

        self.assertEqual(1, audited.returncode, audited.stdout + audited.stderr)
        self.assertEqual(10, json.loads(audited.stdout)["data"]["engines"]["betterleaks"])


if __name__ == "__main__":
    unittest.main()
