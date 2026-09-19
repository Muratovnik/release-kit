from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import owner, protection


class OwnerPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        override = patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: ""})
        override.start()
        self.addCleanup(override.stop)

    @staticmethod
    def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )

    @classmethod
    def _repository(cls, root: Path) -> None:
        root.mkdir()
        cls._git(root, "init", "-q")

    def test_the_default_policy_is_the_sibling_private_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            private = parent / "example-private"
            public.mkdir()
            private.mkdir()
            (private / ".publication-private-values").write_text(
                "# private\n\n InternalService \n", encoding="utf-8"
            )

            with patch.object(
                owner.subprocess,
                "run",
                side_effect=AssertionError("non-Git discovery must not invoke Git"),
            ):
                policy = owner.discover(public)
            self.assertEqual(private.resolve(), policy.root)
            self.assertEqual(("InternalService",), policy.values())

    def test_a_missing_or_empty_owner_policy_is_not_a_public_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            private = parent / "example-private"
            public.mkdir()
            private.mkdir()
            with self.assertRaises(owner.OwnerPolicyError):
                owner.discover(public).values()
            (private / ".publication-private-values").write_text("# no values\n", encoding="utf-8")
            with self.assertRaises(owner.OwnerPolicyError):
                owner.discover(public).values()

    def test_an_environment_override_supports_an_unusual_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            private = parent / "owner-policy"
            public.mkdir()
            private.mkdir()
            (private / ".publication-private-values").write_text(
                "InternalService\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: private.name}):
                self.assertEqual(private.resolve(), owner.discover(public).root)

    def test_a_nonempty_environment_override_precedes_the_local_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            configured = parent / "configured-policy"
            overridden = parent / "override-policy"
            self._repository(public)
            configured.mkdir()
            overridden.mkdir()
            self._git(
                public,
                "config",
                "--local",
                owner.PRIVATE_ROOT_CONFIG,
                str(configured),
            )

            with patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: str(overridden)}):
                policy = owner.discover(public)

        self.assertEqual(overridden.resolve(), policy.root)

    def test_repository_local_settings_are_isolated_and_relative_to_each_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            first = parent / "first-public"
            second = parent / "second-public"
            first_private = parent / "first-policy"
            second_private = parent / "second-policy"
            self._repository(first)
            self._repository(second)
            first_private.mkdir()
            second_private.mkdir()
            self._git(
                first,
                "config",
                "--local",
                owner.PRIVATE_ROOT_CONFIG,
                "../first-policy",
            )
            self._git(
                second,
                "config",
                "--local",
                owner.PRIVATE_ROOT_CONFIG,
                "../second-policy",
            )

            with patch.dict(
                os.environ,
                {
                    owner.PRIVATE_ROOT_ENV: "",
                    "GIT_DIR": str(second / ".git"),
                    "GIT_WORK_TREE": str(second),
                },
            ):
                first_policy = owner.discover(first)
            with patch.dict(
                os.environ,
                {
                    owner.PRIVATE_ROOT_ENV: "",
                    "GIT_DIR": str(first / ".git"),
                    "GIT_WORK_TREE": str(first),
                },
            ):
                second_policy = owner.discover(second)

            self.assertFalse((first_private / ".git").exists())
            self.assertFalse((second_private / ".git").exists())
            self.assertEqual("", self._git(first, "status", "--short").stdout)
            self.assertEqual("", self._git(second, "status", "--short").stdout)

        self.assertEqual(first_private.resolve(), first_policy.root)
        self.assertEqual(second_private.resolve(), second_policy.root)
        self.assertNotEqual(first_policy.root, second_policy.root)

    def test_an_absent_local_setting_falls_back_to_the_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            sibling = parent / "example-private"
            self._repository(public)
            sibling.mkdir()

            with patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: ""}):
                policy = owner.discover(public)

        self.assertEqual(sibling.resolve(), policy.root)

    def test_global_and_included_settings_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            sibling = parent / "example-private"
            global_private = parent / "global-policy"
            system_private = parent / "system-policy"
            included_private = parent / "included-policy"
            global_config = parent / "global.gitconfig"
            system_config = parent / "system.gitconfig"
            included_config = parent / "included.gitconfig"
            self._repository(public)
            sibling.mkdir()
            global_private.mkdir()
            system_private.mkdir()
            included_private.mkdir()
            self._git(
                public,
                "config",
                "--file",
                str(global_config),
                owner.PRIVATE_ROOT_CONFIG,
                str(global_private),
            )
            self._git(
                public,
                "config",
                "--file",
                str(system_config),
                owner.PRIVATE_ROOT_CONFIG,
                str(system_private),
            )
            self._git(
                public,
                "config",
                "--file",
                str(included_config),
                owner.PRIVATE_ROOT_CONFIG,
                str(included_private),
            )
            self._git(public, "config", "--local", "include.path", str(included_config))
            environment = {
                owner.PRIVATE_ROOT_ENV: "",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_GLOBAL": str(global_config),
                "GIT_CONFIG_KEY_0": owner.PRIVATE_ROOT_CONFIG,
                "GIT_CONFIG_SYSTEM": str(system_config),
                "GIT_CONFIG_VALUE_0": str(global_private),
            }

            with patch.dict(os.environ, environment):
                policy = owner.discover(public)

        self.assertEqual(sibling.resolve(), policy.root)

    def test_duplicate_and_empty_local_settings_fail_closed(self) -> None:
        for case in ("duplicate", "empty"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary)
                public = parent / "example"
                sibling = parent / "example-private"
                self._repository(public)
                sibling.mkdir()
                values = ("../first-policy", "../second-policy") if case == "duplicate" else ("",)
                for value in values:
                    self._git(
                        public,
                        "config",
                        "--local",
                        "--add",
                        owner.PRIVATE_ROOT_CONFIG,
                        value,
                    )

                with (
                    patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: ""}),
                    self.assertRaisesRegex(owner.OwnerPolicyError, "exactly one non-empty value"),
                ):
                    owner.discover(public)

    def test_a_multiline_local_setting_is_rejected_as_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            sibling = parent / "example-private"
            self._repository(public)
            sibling.mkdir()
            self._git(
                public,
                "config",
                "--local",
                owner.PRIVATE_ROOT_CONFIG,
                "owner\npolicy",
            )

            with (
                patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: ""}),
                self.assertRaisesRegex(owner.OwnerPolicyError, "single-line path"),
            ):
                owner.discover(public)

    def test_a_local_config_read_error_is_generic_and_does_not_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            sibling = parent / "example-private"
            self._repository(public)
            sibling.mkdir()
            failed = subprocess.CompletedProcess(
                args=["git"],
                returncode=128,
                stdout="",
                stderr="fatal: detail from a private configuration path",
            )

            with (
                patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: ""}),
                patch.object(owner.subprocess, "run", return_value=failed),
                self.assertRaises(owner.OwnerPolicyError) as raised,
            ):
                owner.discover(public)

        self.assertEqual(
            "repository-local owner policy configuration could not be read",
            str(raised.exception),
        )

    def test_a_configured_missing_policy_does_not_fall_back_to_the_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            sibling = parent / "example-private"
            self._repository(public)
            sibling.mkdir()
            self._git(
                public,
                "config",
                "--local",
                owner.PRIVATE_ROOT_CONFIG,
                "../missing-policy",
            )

            with (
                patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: ""}),
                self.assertRaisesRegex(owner.OwnerPolicyError, "missing-policy"),
            ):
                owner.discover(public)

    def test_an_absolute_local_setting_is_shared_by_linked_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            linked = parent / "example-linked"
            private = parent / "owner-policy"
            self._repository(public)
            private.mkdir()
            (public / "seed.txt").write_text("example\n", encoding="utf-8")
            self._git(public, "config", "user.name", "Example Maintainer")
            self._git(public, "config", "user.email", "maintainer@example.invalid")
            self._git(public, "add", "seed.txt")
            self._git(public, "commit", "-qm", "chore: seed fixture")
            self._git(
                public,
                "config",
                "--local",
                owner.PRIVATE_ROOT_CONFIG,
                str(private),
            )
            self._git(public, "worktree", "add", "--detach", str(linked))

            with patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: ""}):
                public_policy = owner.discover(public)
                linked_policy = owner.discover(linked)

        self.assertEqual(private.resolve(), public_policy.root)
        self.assertEqual(private.resolve(), linked_policy.root)

    def test_a_fresh_process_resolves_the_project_setting_from_an_unrelated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            private = parent / "owner-policy"
            unrelated = parent / "ordinary-folder"
            self._repository(public)
            private.mkdir()
            unrelated.mkdir()
            self._git(
                public,
                "config",
                "--local",
                owner.PRIVATE_ROOT_CONFIG,
                str(private),
            )
            environment = dict(os.environ)
            environment[owner.PRIVATE_ROOT_ENV] = ""
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            program = (
                "import sys\n"
                "from pathlib import Path\n"
                "from releasekit import owner\n"
                "print(owner.discover(Path(sys.argv[1])).root)\n"
            )

            completed = subprocess.run(
                [sys.executable, "-c", program, str(public)],
                cwd=unrelated,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(str(private.resolve()), completed.stdout.strip())

    def test_a_typed_private_policy_carries_workflows_and_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            private = parent / "example-private"
            public.mkdir()
            private.mkdir()
            (private / owner.PRIVATE_VALUES_FILE).write_text("# intentionally empty\n")
            (private / owner.SEMANTIC_POLICY_FILE).write_text(
                "version = 1\n"
                'owner_workflows = ["Someservice"]\n\n'
                "[[patterns]]\n"
                'name = "opaque-record"\n'
                'kind = "personal-data"\n'
                'expression = "rec_[0-9]+"\n',
                encoding="utf-8",
            )

            loaded = owner.discover(public).load()

        self.assertEqual(("Someservice",), loaded.owner_workflows)
        self.assertEqual("opaque-record", loaded.patterns[0].name)

    def test_an_invalid_private_pattern_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            public = parent / "example"
            private = parent / "example-private"
            public.mkdir()
            private.mkdir()
            (private / owner.SEMANTIC_POLICY_FILE).write_text(
                '[[patterns]]\nname = "broken"\nkind = "personal-data"\nexpression = "["\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(owner.OwnerPolicyError, "broken"):
                owner.discover(public).load()


class ProtectionTests(unittest.TestCase):
    @staticmethod
    def _repository(root: Path, hooks_path: Path | None = None) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        (root / ".github").mkdir()
        (root / ".github" / "relkit.pyz").write_bytes(b"trusted projection")
        (root / ".betterleaks.toml").write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
        (root / "relkit.toml").write_text("[exposure]\n", encoding="utf-8")
        target = hooks_path or root / ".git/hooks"
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", str(target)],
            cwd=root,
            check=True,
        )

    @staticmethod
    def _compatible_dispatcher(path: Path) -> str:
        content = (
            "#!/bin/sh\n"
            f"{protection.COMPATIBLE_DISPATCHER_MARKER}\n"
            "# Owned by a user-scoped hook runtime, not release-kit.\n"
            "exit 0\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        os.chmod(path, 0o755)
        return content

    def test_install_creates_the_exact_guard_and_check_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)

            path = protection.install(root)

            self.assertIsNone(protection.problem(root))
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            self.assertIn("drifted", protection.problem(root) or "")

    def test_guard_detects_projection_and_policy_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            protection.install(root)

            (root / ".github" / "relkit.pyz").write_bytes(b"replacement")
            self.assertIn("drifted", protection.problem(root) or "")
            protection.install(root)
            self.assertIsNone(protection.problem(root))

            (root / "relkit.toml").write_text(
                "[exposure]\ncheck_secrets = false\n", encoding="utf-8"
            )
            self.assertIn("drifted", protection.problem(root) or "")

    def test_install_refuses_to_replace_an_unmanaged_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            path = protection.hook_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

            with self.assertRaises(protection.ProtectionError):
                protection.install(root)

    def test_install_accepts_a_compatible_external_dispatcher_without_mutating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repository"
            shared = base / "shared-hooks"
            root.mkdir()
            self._repository(root, shared)
            dispatcher = shared / "pre-push"
            original = self._compatible_dispatcher(dispatcher)

            local = protection.install(root)

            self.assertEqual(protection.hook_content(root), local.read_text(encoding="utf-8"))
            self.assertEqual(original, dispatcher.read_text(encoding="utf-8"))
            self.assertIsNone(protection.problem(root))

    def test_install_preflights_a_missing_dispatcher_before_writing_the_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repository"
            shared = base / "shared-hooks"
            root.mkdir()
            self._repository(root, shared)
            local = protection.hook_path(root)

            with self.assertRaisesRegex(protection.ProtectionError, "compatible pre-push"):
                protection.install(root)

            self.assertFalse(local.exists())
            self.assertFalse((shared / "pre-push").exists())

    def test_install_rejects_the_legacy_release_kit_dispatcher_without_mutating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repository"
            shared = base / "shared-hooks"
            root.mkdir()
            self._repository(root, shared)
            dispatcher = shared / "pre-push"
            legacy = "#!/bin/sh\n# managed by release-kit: shared pre-push dispatcher v1\nexit 0\n"
            dispatcher.parent.mkdir(parents=True)
            dispatcher.write_text(legacy, encoding="utf-8", newline="\n")
            os.chmod(dispatcher, 0o755)
            local = protection.hook_path(root)

            with self.assertRaisesRegex(protection.ProtectionError, "expected"):
                protection.install(root)

            self.assertFalse(local.exists())
            self.assertEqual(legacy, dispatcher.read_text(encoding="utf-8"))

    def test_install_preflights_a_non_executable_dispatcher_before_writing_the_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repository"
            shared = base / "shared-hooks"
            root.mkdir()
            self._repository(root, shared)
            self._compatible_dispatcher(shared / "pre-push")
            local = protection.hook_path(root)

            with (
                patch.object(protection.os, "access", return_value=False),
                self.assertRaisesRegex(protection.ProtectionError, "not executable"),
            ):
                protection.install(root)

            self.assertFalse(local.exists())

    def test_dispatcher_drift_blocks_guard_refresh_without_mutating_either_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repository"
            shared = base / "shared-hooks"
            root.mkdir()
            self._repository(root, shared)
            dispatcher = shared / "pre-push"
            self._compatible_dispatcher(dispatcher)
            local = protection.install(root)
            original_guard = local.read_bytes()
            incompatible = "#!/bin/sh\nexit 0\n"
            dispatcher.write_text(incompatible, encoding="utf-8", newline="\n")
            self.assertIn("expected", protection.problem(root) or "")
            (root / "relkit.toml").write_text(
                "[exposure]\ncheck_secrets = false\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(protection.ProtectionError, "expected"):
                protection.install(root)

            self.assertEqual(original_guard, local.read_bytes())
            self.assertEqual(incompatible, dispatcher.read_text(encoding="utf-8"))

    def test_guard_pins_the_release_workflow_once_the_coordinator_is_configured(self) -> None:
        # Field case: a workflow commit added four upload steps without touching
        # relkit.toml, and the guard stayed silent until after publication.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            workflow = root / ".github" / "workflows" / "release.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "on: push\njobs:\n  publish:\n    runs-on: ubuntu-latest\n", encoding="utf-8"
            )
            (root / "relkit.toml").write_text(
                "[exposure]\n"
                '[release]\nrepository = "example/project"\n'
                'workflow = ".github/workflows/release.yml"\nrequired_jobs = ["publish"]\n'
                'version_file = "VERSION"\nversion_pattern = "^(.+)$"\n'
                'assets = ["example-{version}.zip"]\nchecks = [["python", "check.py"]]\n'
                'smoke = [["python", "smoke.py"]]\nsmoke_platforms = ["linux", "darwin", "win32"]\n',
                encoding="utf-8",
            )

            protection.install(root)

            self.assertIsNone(protection.problem(root))
            self.assertIn(".github/workflows/release.yml", protection.recorded_digests(root))
            workflow.write_text(
                workflow.read_text(encoding="utf-8") + "  upload:\n    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            problem = protection.problem(root) or ""
            self.assertIn("drifted", problem)
            self.assertIn(".github/workflows/release.yml: pinned", problem)
            protection.install(root)
            self.assertIsNone(protection.problem(root))

    def test_guard_pins_no_workflow_for_a_local_publisher(self) -> None:
        # Field case: a skill library adopted `publisher = "github"`, which has no
        # workflow, and every guard command refused with "guarded publication
        # input is unavailable" naming the repository root, because the empty
        # workflow name had been pinned as `root / ""`. The pre-push guard then
        # blocked the very push that carried the configuration.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            (root / "relkit.toml").write_text(
                "[exposure]\n"
                '[release]\npublisher = "github"\nrepository = "example/project"\n'
                'version_file = "VERSION"\nversion_pattern = "^(.+)$"\n'
                'assets = []\nchecks = [["python", "check.py"]]\n'
                'smoke = [["python", "smoke.py"]]\nsmoke_platforms = ["linux", "darwin", "win32"]\n',
                encoding="utf-8",
            )

            protection.install(root)

            self.assertIsNone(protection.problem(root))
            pinned = protection.recorded_digests(root)
            self.assertIn("relkit.toml", pinned)
            self.assertNotIn("", pinned)
            self.assertTrue(all((root / relative).is_file() for relative in pinned), pinned)

    def test_check_reports_a_hooks_path_git_cannot_run_instead_of_installed(self) -> None:
        # Field hypothesis: core.hooksPath pointing at a missing directory leaves a
        # byte-perfect guard inert; check and install must say so, not "installed".
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            protection.install(root)
            self.assertIsNone(protection.problem(root))
            subprocess.run(
                ["git", "config", "--local", "core.hooksPath", str(root / "missing-hooks")],
                cwd=root,
                check=True,
            )

            problem = protection.problem(root) or ""

            self.assertIn("dispatcher is not installed at", problem)
            self.assertIn("missing-hooks", problem)
            with self.assertRaisesRegex(protection.ProtectionError, "not installed at"):
                protection.install(root)

    def test_the_guard_resolves_an_interpreter_instead_of_assuming_python(self) -> None:
        # Field case: a push failed with a bare shell error on a host that ships
        # python3 but no python. The hook has to name the missing runtime itself.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)

            content = protection.install(root).read_text(encoding="utf-8")

            self.assertIn("for relkit_candidate in python python3 py; do", content)
            self.assertIn("release-kit guard needs a working python", content)
            self.assertNotIn("\nexec python ", content)
            self.assertNotIn('\npython - "$root"', content)

    def test_an_intact_older_template_is_refreshable_rather_than_a_failing_push(self) -> None:
        # A release that changes the template must not turn every adopter's push and
        # update into a failure: the older guard pins the same inputs and still runs.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            path = protection.install(root)
            pins = protection.recorded_digests(root)
            path.write_text(protection._hook_v1(repr(pins)), encoding="utf-8", newline="\n")

            self.assertIsNone(protection.problem(root))
            self.assertTrue(protection.outdated_template(root))
            self.assertEqual(pins, protection.recorded_digests(root))
            self.assertEqual([], protection.digest_changes(root))

            protection.install(root)

            self.assertFalse(protection.outdated_template(root))
            self.assertIsNone(protection.problem(root))

    def test_a_stale_pin_in_an_older_template_is_still_reported_as_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            path = protection.install(root)
            pins = dict(protection.recorded_digests(root), **{"relkit.toml": "0" * 64})
            path.write_text(protection._hook_v1(repr(pins)), encoding="utf-8", newline="\n")

            self.assertIn("drifted", protection.problem(root) or "")


if __name__ == "__main__":
    unittest.main()
