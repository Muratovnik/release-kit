"""A disposable clone is audited on its content; the guards it cannot carry are not required.

Field case: `audit --history --owner` in a throwaway clone always ended in a refusal,
because a clone has no pre-push guard, and each run had to be sorted by hand to tell
that refusal from the real findings. `--sandbox` drops only the guard requirement and
says so; it is refused where a push publishes, since the guard is what judges a push.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from test_commit_audit import PRIVATE_VALUE, CommitRepository

from releasekit import cli, owner


class SandboxCloneTests(CommitRepository):
    def setUp(self) -> None:
        super().setUp()
        self.clone = self.base / "clone"
        self.git("clone", "-q", str(self.root), str(self.clone), root=self.base)
        # Isolated from the machine's global hook path, as the fixture repository is.
        self.git("config", "core.hooksPath", str(self.clone / ".git" / "hooks"), root=self.clone)
        self.git("config", "user.name", "Example Writer", root=self.clone)
        self.git("config", "user.email", "writer@example.invalid", root=self.clone)
        self.git("config", "--local", owner.PRIVATE_ROOT_CONFIG, str(self.private), root=self.clone)

    def audit(self, *extra: str, stdin: str | None = None) -> tuple[int, dict, str]:
        out, err = io.StringIO(), io.StringIO()
        arguments = ["audit", "--no-download", "--json", *extra, "--root", str(self.clone)]
        with redirect_stdout(out), redirect_stderr(err):
            if stdin is None:
                code = cli.main(arguments)
            else:
                with patch("sys.stdin", io.StringIO(stdin)):
                    code = cli.main(arguments)
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_a_clean_clone_passes_the_owner_history_audit_with_the_sandbox_flag(self) -> None:
        code, envelope, err = self.audit("--history", "--owner")
        self.assertEqual(1, code, "without the flag a clone is refused for its missing guard")
        self.assertIn("pre-push guard", err)

        code, envelope, err = self.audit("--history", "--owner", "--sandbox")
        self.assertEqual(0, code, err)
        self.assertNotIn("pre-push guard is not installed", err)
        self.assertIs(True, envelope["data"]["sandbox"])
        self.assertIn("guards_not_checked", [item["code"] for item in envelope["warnings"]])

    def test_the_sandbox_flag_keeps_the_verdict_on_the_lines(self) -> None:
        self.write("notes.md", f"names {PRIVATE_VALUE}\n", root=self.clone)
        self.git("add", "-A", root=self.clone)
        self.git("commit", "-q", "--no-verify", "-m", "docs: name it", root=self.clone)

        code, envelope, err = self.audit("--history", "--owner", "--sandbox")

        self.assertEqual(1, code, err)
        messages = [item["message"] for item in envelope["errors"]]
        self.assertTrue(any("private-value" in message for message in messages), messages)
        self.assertFalse(any("guard" in message for message in messages), messages)

    def test_the_sandbox_flag_is_refused_where_a_push_is_judged(self) -> None:
        head = self.git("rev-parse", "HEAD", root=self.clone)
        update = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
        for extra in (
            ("--push", "--remote", "origin", "--owner"),
            ("--push", "--remote", "origin"),
        ):
            with self.subTest(extra=extra):
                code, envelope, err = self.audit(*extra, "--sandbox", stdin=update)
                self.assertEqual(2, code)
                self.assertEqual("invalid_arguments", envelope["errors"][0]["code"])
                self.assertIn("--sandbox", err)
                self.assertIn("--push", err)

    def test_the_sandbox_flag_is_refused_with_the_commit_check_which_checks_no_guard(
        self,
    ) -> None:
        code, envelope, _err = self.audit("--commit", "--owner", "--sandbox")
        self.assertEqual(2, code)
        self.assertEqual("invalid_arguments", envelope["errors"][0]["code"])
        self.assertIn("--commit", envelope["errors"][0]["message"])
