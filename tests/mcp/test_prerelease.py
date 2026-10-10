"""The optional adapter preserves SemVer identity and never turns selection into publish."""

import dataclasses
import json
import os
import unittest

from test_stdio import Fixture

from releasekit_mcp import models, process
from releasekit_mcp.bridge import Bridge


class PrereleaseRoutingTests(Fixture):
    def setUp(self):
        super().setUp()
        self.bridge = Bridge(self.root, self.digest)
        self.bridge.executor_artifact = dataclasses.replace(
            self.bridge.executor_artifact, version="0.32.0"
        )

    def test_prerelease_selection_and_explicit_versions_keep_exact_argv(self):
        async def scenario():
            requests = [
                (
                    models.Release(action="next", bump="minor", prerelease="rc"),
                    ["release", "next", "--bump", "minor", "--prerelease=rc"],
                ),
                (
                    models.Release(action="prepare", version="v1.2.0-rc.2+build.5"),
                    ["release", "prepare", "v1.2.0-rc.2+build.5"],
                ),
                (
                    models.Release(action="plan", version="1.2.0-rc"),
                    ["release", "plan", "1.2.0-rc"],
                ),
            ]
            for request, expected in requests:
                prepared = await self.bridge.prepare(request)
                self.assertEqual(expected, prepared.argv)
                self.assertFalse(prepared.write)
                self.assertNotIn("--publish", prepared.argv)

        self.run_async(scenario)

    def test_older_executor_requires_sync_before_any_new_prerelease_operation(self):
        self.bridge.executor_artifact = dataclasses.replace(
            self.bridge.executor_artifact, version="0.31.0"
        )

        async def scenario():
            for request in (
                models.Release(action="next", bump="minor", prerelease="rc"),
                models.Release(action="status", version="1.2.0-rc.1"),
            ):
                with self.assertRaisesRegex(ValueError, "0.32.0.*sync"):
                    await self.bridge.prepare(request)
            stable = await self.bridge.prepare(models.Release(action="plan", version="1.2.0"))
            self.assertEqual(["release", "plan", "1.2.0"], stable.argv)

        self.run_async(scenario)

    def test_selection_preserves_leading_hyphen_labels_through_the_real_cli(self):
        (self.root / "relkit.toml").write_text(
            "[exposure]\ncheck_secrets=false\ncheck_links=false\n"
            '[changelog]\nprofile="conventional-changelog"\nfirst_version="1.0.0"\n'
            '[release]\npublisher="directory"\nversion_file="VERSION"\n'
            'version_pattern="^(.+)$"\nassets=[]\n'
            'checks=[["{python}","must-not-run.py"]]\n'
            'smoke=[["{python}","must-not-run.py","{assets}"]]\n'
            'smoke_platforms=["linux","darwin","win32"]\n',
            encoding="utf-8",
        )
        (self.root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (self.root / "must-not-run.py").write_text(
            "from pathlib import Path\nPath('execution-marker').touch()\n", encoding="utf-8"
        )
        self.git("add", ".")
        self.git("commit", "-qm", "test: local selection fixture")
        before = self.git("show-ref")

        async def scenario():
            for label in ("rc", "-rc", "--", "-"):
                with self.subTest(label=label):
                    prepared = await self.bridge.prepare(
                        models.Release(action="next", bump="minor", prerelease=label)
                    )
                    code, payload, stderr, _ = await process.execute(
                        self.projection,
                        self.digest,
                        ["--json", *prepared.argv, "--root", str(self.root)],
                        self.root,
                        dict(os.environ),
                        30,
                    )
                    self.assertEqual(0, code, stderr or payload)
                    selected = json.loads(payload)["data"]["next"]
                    self.assertEqual(f"1.0.0-{label}.1", selected["version"])
                    self.assertEqual(label, selected["prerelease"])
                    self.assertFalse(prepared.write)

        self.run_async(scenario)
        self.assertEqual(before, self.git("show-ref"))
        self.assertEqual(b"", self.git("status", "--porcelain"))
        self.assertFalse((self.root / "execution-marker").exists())

    def test_malformed_versions_and_wrong_prerelease_action_are_rejected(self):
        for version in ("1.2.3-01", "1.2.3-rc..1", "01.2.3", "١.2.3", "1.2.3+", "--help"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                models.Release(action="plan", version=version)
        for label in ("01", "1", "rc.1", "кандидат", "rc+build"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                models.Release(action="next", bump="patch", prerelease=label)
        with self.assertRaises(ValueError):
            models.Release(action="plan", version="1.2.3-rc", prerelease="rc")

    def test_model_does_not_add_an_adapter_only_semver_length_limit(self):
        version = "1" * 130 + ".2.3"
        self.assertEqual(version, models.Release(action="plan", version=version).version)
        label = "r" * 70
        self.assertEqual(
            label, models.Release(action="next", bump="minor", prerelease=label).prerelease
        )


if __name__ == "__main__":
    unittest.main()
