from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import owner, protection


class OwnerPolicyTests(unittest.TestCase):
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
            with patch.dict(os.environ, {owner.PRIVATE_ROOT_ENV: str(private)}):
                self.assertEqual(private.resolve(), owner.discover(public).root)

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
