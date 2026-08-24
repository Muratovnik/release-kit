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


class ProtectionTests(unittest.TestCase):
    @staticmethod
    def _repository(root: Path, hooks_path: Path | None = None) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        target = hooks_path or root / ".git/hooks"
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", str(target)],
            cwd=root,
            check=True,
        )

    def test_install_creates_the_exact_guard_and_check_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)

            path = protection.install(root)

            self.assertIsNone(protection.problem(root))
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
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

    def test_install_adds_a_shared_dispatcher_when_hooks_path_is_global(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repository"
            shared = base / "shared-hooks"
            root.mkdir()
            self._repository(root, shared)

            local = protection.install(root)

            self.assertEqual(protection.HOOK, local.read_text(encoding="utf-8"))
            self.assertEqual(
                protection.DISPATCHER,
                (shared / "pre-push").read_text(encoding="utf-8"),
            )
            self.assertIsNone(protection.problem(root))


if __name__ == "__main__":
    unittest.main()
