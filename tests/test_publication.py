from __future__ import annotations

import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from releasekit import cli, config, owner, publication
from releasekit.exposure.audit import Report
from releasekit.result import Result


class StagedPolicyTests(unittest.TestCase):
    def test_unstaged_policy_cannot_change_the_index_verdict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            policy = root / "relkit.toml"
            prefix = "[exposure]\ncheck_secrets = false\ncheck_links = false\n"
            policy.write_text(prefix + 'forbidden_suffixes = [".internalx"]\n')
            (root / "example.internalx").write_text("synthetic publication fixture\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)

            def check():
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    return cli.main(["audit", "--staged", "--root", str(root), "--no-download"])

            self.assertEqual(1, check())
            policy.write_text(prefix)
            self.assertEqual(1, check(), "unstaged policy must not relax the index")
            subprocess.run(["git", "add", "relkit.toml"], cwd=root, check=True)
            policy.write_text("invalid unstaged TOML [")
            self.assertEqual(0, check(), "unstaged policy must not reject a valid index")
            subprocess.run(
                ["git", "rm", "--cached", "-f", "relkit.toml"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            policy.write_text(prefix)
            self.assertEqual(
                2, check(), "an untracked policy cannot replace a missing index policy"
            )


class HistoryScopeTests(unittest.TestCase):
    def test_tree_inventory_failure_is_an_operational_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = config.Config(root=root)
            with (
                patch.object(publication.config_module, "load", return_value=settings),
                patch.object(
                    publication.audit, "scan", side_effect=RuntimeError("not a repository")
                ),
                redirect_stderr(StringIO()) as stderr,
            ):
                result = publication.run(
                    root,
                    history=False,
                    staged=False,
                    strict=False,
                    owner_mode=False,
                    require_overlay=False,
                    allow_download=False,
                )

        self.assertEqual(2, result)
        self.assertIn("not a repository", stderr.getvalue())

    def test_history_inventory_failure_is_an_operational_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = config.Config(root=root)
            with (
                patch.object(publication.config_module, "load", return_value=settings),
                patch.object(publication.audit, "scan", return_value=Report()),
                patch.object(publication.audit, "worktree_changes", return_value=()),
                patch.object(
                    publication.audit,
                    "history_failures",
                    side_effect=RuntimeError("history timed out"),
                ),
                redirect_stderr(StringIO()) as stderr,
            ):
                result = publication.run(
                    root,
                    history=True,
                    staged=False,
                    strict=False,
                    owner_mode=False,
                    require_overlay=False,
                    allow_download=False,
                )

        self.assertEqual(2, result)
        self.assertIn("history timed out", stderr.getvalue())

    def test_dirty_worktree_prevents_a_history_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = config.Config(root=root)
            with (
                patch.object(publication.config_module, "load", return_value=settings),
                patch.object(publication.audit, "scan", return_value=Report()),
                patch.object(publication.audit, "worktree_changes", return_value=(" M file",)),
                patch.object(publication.audit, "history_failures", return_value=[]) as history,
                patch.object(publication.engines, "betterleaks", return_value=0) as secrets,
                patch.object(publication.engines, "lychee", return_value=0) as links,
                redirect_stderr(StringIO()),
            ):
                result = publication.run(
                    root,
                    history=True,
                    staged=False,
                    strict=False,
                    owner_mode=False,
                    require_overlay=False,
                    allow_download=False,
                )

        self.assertEqual(1, result)
        history.assert_not_called()
        secrets.assert_not_called()
        links.assert_not_called()

    def test_an_untracked_scratch_file_does_not_block_the_history_verdict(self) -> None:
        # Field friction: an unrelated untracked file blocked every push, and the
        # only way through was --no-verify, which switches the whole gate off.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = config.Config(root=root)
            with (
                patch.object(publication.config_module, "load", return_value=settings),
                patch.object(publication.audit, "scan", return_value=Report()),
                patch.object(publication.audit, "worktree_changes", return_value=("?? notes.md",)),
                patch.object(publication.audit, "history_failures", return_value=[]) as history,
                patch.object(publication.toolchain, "prepare"),
                patch.object(publication.engines, "betterleaks", return_value=0),
                patch.object(publication.engines, "lychee", return_value=0),
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                result = publication.run(
                    root,
                    history=True,
                    staged=False,
                    strict=False,
                    owner_mode=False,
                    require_overlay=False,
                    allow_download=False,
                )

        self.assertEqual(0, result)
        history.assert_called_once()


class SemanticWiringTests(unittest.TestCase):
    @staticmethod
    def _owner_policy(root: Path) -> owner.OwnerPolicy:
        private = root.parent / f"{root.name}-private"
        private.mkdir()
        (private / owner.SEMANTIC_POLICY_FILE).write_text(
            "version = 1\n"
            'owner_workflows = ["OwnerWorkbench"]\n\n'
            "[[patterns]]\n"
            'name = "record-reference"\n'
            'kind = "personal-data"\n'
            'expression = "rec_[0-9]+"\n',
            encoding="utf-8",
        )
        return owner.discover(root)

    def test_semantic_settings_reach_tree_and_history_scans(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "example"
            root.mkdir()
            policy = self._owner_policy(root)
            settings = config.Config(
                root=root,
                exposure=config.ExposureConfig(
                    forbid_ai_attribution=True,
                    forbid_internal_planning=True,
                    forbid_machine_observations=True,
                    providers={
                        "Someservice": config.ProviderConfig(
                            role="product-data-provider",
                            allowed_surfaces=["docs/*"],
                        )
                    },
                    provenance_required=["tests/generated/*"],
                    provenance={"tests/generated/*": "synthetic"},
                ),
            )
            with (
                patch.object(publication.config_module, "load", return_value=settings),
                patch.object(publication.owner, "discover", return_value=policy),
                patch.object(publication.audit, "scan", return_value=Report()) as tree_scan,
                patch.object(publication.audit, "worktree_changes", return_value=()),
                patch.object(publication.audit, "history_failures", return_value=[]) as history,
                patch.object(publication.protection, "problem", return_value=None),
                # Provisioning is not what this test is about, and leaving it real made
                # the verdict depend on whether a cache outside the project happened to
                # be populated: the suite passed here and failed inside the release check.
                patch.object(publication.toolchain, "prepare"),
                patch.object(publication.engines, "betterleaks", return_value=0),
                patch.object(publication.engines, "lychee", return_value=0),
            ):
                result = publication.run(
                    root,
                    history=True,
                    staged=False,
                    strict=False,
                    owner_mode=True,
                    require_overlay=False,
                    allow_download=False,
                )

        self.assertEqual(0, result)
        for call in (tree_scan.call_args, history.call_args):
            self.assertTrue(call.kwargs["forbid_ai_attribution"])
            self.assertEqual(("OwnerWorkbench",), call.kwargs["owner_workflows"])
            self.assertEqual({"Someservice": ["docs/*"]}, call.kwargs["providers"])
            self.assertEqual({"tests/generated/*": "synthetic"}, call.kwargs["provenance"])

    def test_a_product_provider_cannot_also_be_an_owner_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "example"
            root.mkdir()
            policy = self._owner_policy(root)
            settings = config.Config(
                root=root,
                exposure=config.ExposureConfig(
                    providers={
                        "OwnerWorkbench": config.ProviderConfig(
                            role="product-data-provider",
                            allowed_surfaces=["docs/*"],
                        )
                    }
                ),
            )
            with (
                patch.object(publication.config_module, "load", return_value=settings),
                patch.object(publication.owner, "discover", return_value=policy),
                redirect_stderr(StringIO()) as stderr,
            ):
                result = publication.run(
                    root,
                    history=False,
                    staged=False,
                    strict=False,
                    owner_mode=True,
                    require_overlay=False,
                    allow_download=False,
                )

        self.assertEqual(2, result)
        self.assertIn("also declared as owner workflow", stderr.getvalue())


class RenamedProfileTests(unittest.TestCase):
    """A rename must not lock an adopter out of the release that performs it."""

    def project(self, root: Path, profile: str) -> None:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "relkit.toml").write_text(
            "[exposure]\ncheck_secrets = false\ncheck_links = false\n\n"
            f'[changelog]\nprofile = "{profile}"\n',
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=root, check=True)

    def test_a_project_on_the_former_profile_name_still_audits(self):
        """`update` runs the candidate's audit and rolls back when it fails.

        While the former name was refused, a project using it could not reach any
        release that knows the new one: the installed tool could not read the new name
        either, so neither side could move first. The audit passes and says what to fix.
        """
        with tempfile.TemporaryDirectory(prefix="renamed profile ") as temporary:
            root = Path(temporary)
            self.project(root, "vue-like")
            printed = StringIO()
            with redirect_stdout(printed), redirect_stderr(StringIO()):
                exit_code = cli.main(["audit", "--root", str(root), "--no-download"])
        self.assertEqual(0, exit_code)
        self.assertIn("conventional-changelog", printed.getvalue())

    def test_the_notice_reaches_the_structured_result(self):
        # Automation reads warnings, not the console, so a rename nobody can parse is a
        # rename nobody acts on until a release fails.
        with tempfile.TemporaryDirectory(prefix="renamed profile ") as temporary:
            root = Path(temporary)
            self.project(root, "vue-like")
            outcome = Result()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                exit_code = publication.run(
                    root,
                    history=False,
                    staged=False,
                    strict=False,
                    owner_mode=False,
                    require_overlay=False,
                    allow_download=False,
                    result=outcome,
                )
        self.assertEqual(0, exit_code)
        self.assertEqual(
            ["deprecated_changelog_profile"], [item["code"] for item in outcome.warnings]
        )

    def test_a_current_profile_warns_about_nothing(self):
        with tempfile.TemporaryDirectory(prefix="current profile ") as temporary:
            root = Path(temporary)
            self.project(root, "conventional-changelog")
            outcome = Result()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                exit_code = publication.run(
                    root,
                    history=False,
                    staged=False,
                    strict=False,
                    owner_mode=False,
                    require_overlay=False,
                    allow_download=False,
                    result=outcome,
                )
        self.assertEqual(0, exit_code)
        self.assertEqual([], outcome.warnings)


if __name__ == "__main__":
    unittest.main()
