from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from releasekit import config, publication
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


if __name__ == "__main__":
    unittest.main()
