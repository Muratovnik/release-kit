"""Keep the documented action vocabulary aligned with the typed public interface."""

import re
import unittest
from pathlib import Path
from typing import get_args

from releasekit_mcp import models

ROOT = Path(__file__).resolve().parents[2]


class DocumentedActionsTests(unittest.TestCase):
    def test_mcp_action_table_covers_the_current_request_models(self):
        text = (ROOT / "docs/mcp.md").read_text(encoding="utf-8")
        for name, model in (
            ("relkit_protect", models.Protect),
            ("relkit_release", models.Release),
            ("relkit_update", models.Update),
            ("relkit_sync", models.Sync),
            ("relkit_project", models.Project),
        ):
            with self.subTest(tool=name):
                rows = [line for line in text.splitlines() if line.startswith(f"| `{name}` |")]
                self.assertEqual(1, len(rows), "each action tool needs one discoverable row")
                actions = set(re.findall(r"`([^`]+)`", rows[0].split("|")[2]))
                self.assertEqual(set(get_args(model.model_fields["action"].annotation)), actions)
