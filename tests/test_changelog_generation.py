from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit import config, generation


class GeneratorBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="changelog generator ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.environment = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }

    def git(self, *arguments):
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Example Maintainer",
                "-c",
                "user.email=maintainer@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=.git/hooks",
                *arguments,
            ],
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    def repository(self):
        self.git("init", "--template=", "-b", "main")
        self.git("commit", "--allow-empty", "-m", "feat: initial API")
        self.git("tag", "v1.0.0")
        self.git("commit", "--allow-empty", "-m", "fix: repaired behavior")

    def policy(self, body):
        return config.ChangelogConfig(
            generator=config.GeneratorConfig(command=(sys.executable, "-B", "-c", body))
        )

    def test_explicit_range_keeps_only_its_verified_tag_boundary(self):
        self.repository()
        with patch.object(generation.toolchain, "resolve", return_value=Path("git-cliff")):
            command = generation.command_for(
                config.ChangelogConfig(generator=config.GeneratorConfig(engine="git-cliff")),
                "1.1.0",
                root=self.root,
                allow_download=False,
                from_tag="v1.0.0",
            )
        self.assertEqual("^v1\\.0\\.0$", command[command.index("--tag-pattern") + 1])
        self.assertEqual(["--", "refs/tags/v1.0.0..HEAD"], command[-2:])
        self.assertNotIn("--unreleased", command)
        for option in ("--skip-tags", "--ignore-tags"):
            self.assertIsNone(re.search(command[command.index(option) + 1], "v1.0.0"))

    def test_unknown_branch_and_invalid_ref_are_refused_before_resolving_engine(self):
        self.repository()
        policy = config.ChangelogConfig(generator=config.GeneratorConfig(engine="git-cliff"))
        for tag in ("main", "v9.9.9", "v1.0.0..HEAD", "../v1.0.0"):
            with self.subTest(tag=tag), patch.object(generation.toolchain, "resolve") as resolve:
                with self.assertRaisesRegex(generation.GenerationError, "existing local tag"):
                    generation.command_for(
                        policy, "1.1.0", root=self.root, allow_download=False, from_tag=tag
                    )
                resolve.assert_not_called()

    def test_tag_outside_head_ancestry_is_refused(self):
        self.repository()
        self.git("checkout", "--orphan", "other")
        self.git("commit", "--allow-empty", "-m", "feat: unrelated release line")
        self.git("tag", "v2.0.0")
        self.git("checkout", "main")
        with self.assertRaisesRegex(generation.GenerationError, "reachable from HEAD"):
            generation.command_for(
                config.ChangelogConfig(generator=config.GeneratorConfig(engine="git-cliff")),
                "1.1.0",
                root=self.root,
                allow_download=False,
                from_tag="v2.0.0",
            )

    def test_a_tag_sharing_its_short_name_with_a_branch_is_refused(self):
        self.repository()
        self.git("branch", "v1.0.0", "HEAD")
        with patch.object(generation.toolchain, "resolve") as resolve:
            with self.assertRaisesRegex(generation.GenerationError, "unambiguous local tag"):
                generation.command_for(
                    config.ChangelogConfig(generator=config.GeneratorConfig(engine="git-cliff")),
                    "1.1.0",
                    root=self.root,
                    allow_download=False,
                    from_tag="v1.0.0",
                )
            resolve.assert_not_called()

    def test_custom_command_does_not_run_when_from_tag_cannot_be_applied(self):
        marker = self.root / "ran"
        with self.assertRaisesRegex(generation.GenerationError, "requires the git-cliff engine"):
            generation.draft(
                self.policy(f"from pathlib import Path; Path({str(marker)!r}).touch()"),
                "1.1.0",
                root=self.root,
                from_tag="v1.0.0",
            )
        self.assertFalse(marker.exists())

    def test_failed_generator_cli_preserves_multiline_cause_and_hint(self):
        diagnostic = (
            b"\x1b[31mERROR\x1b[0m could not get github metadata\n"
            b"Caused by:\n    invalid peer certificate: UnknownIssuer\n"
            b"note: run with RUST_BACKTRACE=full for a verbose backtrace.\n"
        )
        body = (
            "import sys\n"
            "sys.stdout.write('unrelated stdout detail\\n')\n"
            f"sys.stderr.buffer.write({diagnostic!r})\n"
            "raise SystemExit(101)\n"
        )
        (self.root / "relkit.toml").write_text(
            "[changelog.generator]\ncommand = "
            + json.dumps([sys.executable, "-B", "-c", body])
            + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "releasekit.cli",
                "notes",
                "1.1.0",
                "--draft",
                "--root",
                str(self.root),
                "--json",
            ],
            cwd=self.root,
            env={
                **self.environment,
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            },
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(2, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(2, result["exit_code"])
        self.assertEqual("generation_error", result["errors"][0]["code"])
        message = result["errors"][0]["message"]
        self.assertIn("exited 101: ERROR could not get github metadata", message)
        self.assertIn("\nCaused by:\n    invalid peer certificate: UnknownIssuer\n", message)
        self.assertTrue(message.endswith("run with RUST_BACKTRACE=full for a verbose backtrace."))
        self.assertNotIn("unrelated stdout detail", message)
        self.assertNotIn("\x1b", message)
        self.assertEqual(
            f"relkit notes: {message}".splitlines(),
            completed.stderr.decode("utf-8").splitlines(),
        )

    def test_failed_generator_bounds_diagnostics_and_keeps_both_ends(self):
        head = "FIRST cause: UnknownIssuer\n"
        tail = "\nLAST hint: inspect generator configuration"
        body = (
            "import sys\n"
            f"sys.stderr.buffer.write({head.encode()!r}"
            " + ('trace Ω\\n' * 6000).encode('utf-8')"
            f" + {tail.encode()!r})\n"
            "raise SystemExit(101)\n"
        )
        with self.assertRaises(generation.GenerationError) as raised:
            generation.draft(self.policy(body), "1.1.0", root=self.root)
        prefix = f"{Path(sys.executable).name} exited 101: "
        message = str(raised.exception)
        self.assertTrue(message.startswith(prefix))
        detail = message[len(prefix) :]
        self.assertTrue(detail.startswith(head))
        self.assertTrue(detail.endswith(tail))
        self.assertIn("truncated", detail)
        self.assertLessEqual(len(detail), 4096)
        self.assertNotIn("\ufffd", detail)

    def test_failed_generator_replaces_invalid_utf8_without_losing_the_cause(self):
        diagnostic = b"\xffinvalid peer certificate\nlast hint\xfe\n"
        with self.assertRaises(generation.GenerationError) as raised:
            generation.draft(
                self.policy(
                    f"import sys; sys.stderr.buffer.write({diagnostic!r}); raise SystemExit(7)"
                ),
                "1.1.0",
                root=self.root,
            )
        self.assertEqual(
            f"{Path(sys.executable).name} exited 7: \ufffdinvalid peer certificate\nlast hint\ufffd",
            str(raised.exception),
        )

    def test_failed_generator_uses_stdout_when_stderr_has_no_text(self):
        output = b"\x1b[33mstdout cause\x1b[0m\nstdout next step\n"
        for diagnostic in (b"", b" \n\t", b"\x1b[31m\x1b[0m \n"):
            with self.subTest(stderr=diagnostic):
                body = (
                    "import sys\n"
                    f"sys.stdout.buffer.write({output!r})\n"
                    f"sys.stderr.buffer.write({diagnostic!r})\n"
                    "raise SystemExit(9)\n"
                )
                with self.assertRaises(generation.GenerationError) as raised:
                    generation.draft(self.policy(body), "1.1.0", root=self.root)
                self.assertEqual(
                    f"{Path(sys.executable).name} exited 9: stdout cause\nstdout next step",
                    str(raised.exception),
                )

    def test_silent_failed_generator_still_reports_its_exit_status(self):
        with self.assertRaises(generation.GenerationError) as raised:
            generation.draft(self.policy("raise SystemExit(17)"), "1.1.0", root=self.root)
        self.assertEqual(f"{Path(sys.executable).name} exited 17", str(raised.exception))

    def test_custom_command_receives_exact_declared_arguments(self):
        arguments = ("literal with spaces", "--tag=unchanged", "-rc", "κ")
        policy = config.ChangelogConfig(
            generator=config.GeneratorConfig(
                command=(
                    sys.executable,
                    "-B",
                    "-c",
                    "import json,sys; print(json.dumps(sys.argv[1:]))",
                    *arguments,
                )
            )
        )
        output = generation.draft(policy, "1.1.0", root=self.root)
        self.assertEqual(list(arguments), json.loads(output))

    def test_custom_command_output_is_utf8_and_keeps_internal_line_endings(self):
        expected = "## [1.1.0]\r\n\r\nИсправлено: café.\r\n"
        result = generation.draft(
            self.policy(f"import sys; sys.stdout.buffer.write({expected.encode()!r})"),
            "1.1.0",
            root=self.root,
        )
        self.assertEqual(expected, result)
        with self.assertRaisesRegex(generation.GenerationError, "UTF-8"):
            generation.draft(
                self.policy("import sys; sys.stdout.buffer.write(bytes([255]))"),
                "1.1.0",
                root=self.root,
            )

    def test_timed_out_generator_cannot_leave_a_child_writing_after_return(self):
        ready, trigger, late = (self.root / name for name in ("ready", "trigger", "late"))
        child = (
            "from pathlib import Path\nimport time\n"
            f"Path({str(ready)!r}).touch()\n"
            "deadline = time.monotonic() + 20\n"
            f"while not Path({str(trigger)!r}).exists() and time.monotonic() < deadline:\n"
            " time.sleep(0.01)\n"
            f"if Path({str(trigger)!r}).exists(): Path({str(late)!r}).touch()\n"
        )
        parent = (
            "import subprocess,sys\n"
            f"child = subprocess.Popen([sys.executable, '-B', '-c', {child!r}])\n"
            "child.wait()\n"
        )
        with patch.object(generation, "TIMEOUT", 2), self.assertRaises(generation.GenerationError):
            generation.draft(self.policy(parent), "1.1.0", root=self.root)
        self.assertTrue(ready.exists(), "the descendant must start for this probe to count")
        trigger.touch()
        time.sleep(0.5)
        self.assertFalse(late.exists(), "the generator descendant survived cleanup")


if __name__ == "__main__":
    unittest.main()
