from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from releasekit import config, owner, publication
from releasekit.exposure.audit import Report


class HistoryScopeTests(unittest.TestCase):
    def test_dirty_worktree_prevents_a_history_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = config.Config(root=root)
            with (
                patch.object(publication.config_module, "load", return_value=settings),
                patch.object(publication.audit, "scan", return_value=Report()),
                patch.object(publication.audit, "worktree_changes", return_value=(" M file",)),
                patch.object(publication.audit, "history_failures", return_value=[]),
                patch.object(publication.engines, "betterleaks", return_value=0),
                patch.object(publication.engines, "lychee", return_value=0),
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


if __name__ == "__main__":
    unittest.main()
