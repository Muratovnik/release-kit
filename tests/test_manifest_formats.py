"""Native JSON/YAML parsing must account for every declared Dotbot link."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from releasekit.overlay import manifest, verify


class ManifestFormatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="manifest formats ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "install.conf.yaml"

    def read(self, text):
        self.path.write_text(text, encoding="utf-8")
        return manifest.read(self.path)

    def test_comments_flow_and_quoted_keys_do_not_hide_a_second_block(self):
        for second in (
            "- link:\n    ../public/.two: local/two\n",
            "- link: # another application\n    ../public/.two: local/two\n",
            "- link: {../public/.two: local/two}\n",
            '- "link":\n    "../public/.two": "local/two"\n',
        ):
            with self.subTest(second=second):
                found = self.read("- link:\n    ../public/.one: local/one\n" + second)
                self.assertEqual(
                    [("../public/.one", "local/one"), ("../public/.two", "local/two")],
                    [(item.link, item.target) for item in found],
                )

    def test_flow_style_whole_yaml_document_uses_native_parser(self):
        found = self.read("[{link: {../public/.one: local/one}}]")
        self.assertEqual((manifest.Mount("../public/.one", "local/one"),), found)

    def test_json_in_the_existing_filename_needs_no_yaml_install(self):
        with patch.dict(sys.modules, {"yaml": None}):
            found = self.read(json.dumps([{"link": {"../public/.one": "local/one"}}]))
        self.assertEqual((manifest.Mount("../public/.one", "local/one"),), found)

    def test_missing_yaml_is_an_explicit_refusal_not_a_partial_result(self):
        with (
            patch.dict(sys.modules, {"yaml": None}),
            self.assertRaisesRegex(manifest.ManifestError, "PyYAML"),
        ):
            self.read("- link:\n    ../public/.one: local/one\n")

    def test_yaml_aliases_are_resolved_by_the_library(self):
        found = self.read("- link: &mounts {../public/.one: local/one}\n- link: *mounts\n")
        self.assertEqual(2, len(found))
        self.assertEqual(found[0], found[1])

    def test_defaults_options_are_not_interpreted_as_mounts(self):
        found = self.read(
            "- defaults:\n    link: {relative: true, relink: true}\n"
            "- link: {../public/.one: local/one}\n"
        )
        self.assertEqual(1, len(found))

    def test_duplicate_link_keys_fail_instead_of_overwriting(self):
        for text in (
            "- link: {../public/.one: local/one, ../public/.one: local/two}\n",
            "- link: {../public/.one: local/one}\n  link: {../public/.two: local/two}\n",
            '[{"link":{"../public/.one":"local/one","../public/.one":"local/two"}}]',
        ):
            with self.subTest(text=text), self.assertRaises(manifest.ManifestError):
                self.read(text)

    def test_unsupported_later_block_refuses_the_entire_manifest(self):
        first = "- link: {../public/.one: local/one}\n"
        for last in (
            "- link:\n    ../public/.two:\n      path: local/two\n      create: true\n",
            "- link: [local/two]\n",
            "- defaults: {link: {glob: true}}\n",
            "- link: {../public/.two: null}\n",
            '- link: {../public/.two: "$HOME/private"}\n',
        ):
            with self.subTest(last=last), self.assertRaises(manifest.ManifestError):
                self.read(first + last)

    def test_invalid_data_empty_links_and_multiple_documents_refuse(self):
        for text in (
            "null",
            "42",
            "[]",
            "- shell: echo example\n",
            "- link: {}\n",
            "- link: [\n",
            "- link: {../public/.one: local/one}\n---\n[]",
        ):
            with self.subTest(text=text), self.assertRaises(manifest.ManifestError):
                self.read(text)

    def test_explicit_json_file_does_not_fallback_to_yaml(self):
        path = self.root / "manifest.json"
        path.write_text("- link: {../public/.one: local/one}", encoding="utf-8")
        with self.assertRaisesRegex(manifest.ManifestError, "invalid JSON"):
            manifest.read(path)

    def test_safe_loader_rejects_python_object_tags(self):
        marker = self.root / "unexpected"
        text = f'!!python/object/apply:pathlib.Path.touch ["{marker}"]'
        with self.assertRaises(manifest.ManifestError):
            self.read(text)
        self.assertFalse(marker.exists())

    def test_missing_and_non_utf8_files_refuse(self):
        with self.assertRaises(manifest.ManifestError):
            manifest.read(self.path)
        self.path.write_bytes(b"\xff\xfe")
        with self.assertRaises(manifest.ManifestError):
            manifest.read(self.path)

    def test_missing_second_link_is_reported_through_the_real_verifier(self):
        public, private = self.root / "public", self.root / "private"
        public.mkdir()
        (private / "local/one").mkdir(parents=True)
        (private / "local/two").mkdir()
        for name in ("one", "two"):
            (private / "local" / name / "file").write_text("synthetic", encoding="utf-8")
        (public / ".gitignore").write_text("/.one\n/.two\n", encoding="utf-8")
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        for root in (public, private):
            for args in (("init", "--template=", "-q"), ("add", "--all")):
                subprocess.run(
                    ["git", *args],
                    cwd=root,
                    env=environment,
                    check=True,
                    capture_output=True,
                )
        try:
            (public / ".one").symlink_to(private / "local/one", target_is_directory=True)
        except OSError as error:
            self.skipTest(f"host does not permit synthetic directory links: {error}")
        mounts = self.read(
            "- link: {../public/.one: local/one}\n"
            "- link: # must not be lost\n    ../public/.two: local/two\n"
        )
        with patch.dict(os.environ, environment, clear=True):
            problems, skipped = verify.check(mounts, public_root=public, private_root=private)
        self.assertEqual([], skipped)
        self.assertEqual([(".two", "missing")], [(p.mount, p.kind) for p in problems])
        (public / ".two").symlink_to(private / "local/two", target_is_directory=True)
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                ([], []), verify.check(mounts, public_root=public, private_root=private)
            )


if __name__ == "__main__":
    unittest.main()
