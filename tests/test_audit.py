from __future__ import annotations

import gzip
import io
import os
import stat
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from releasekit import cli
from releasekit.exposure import audit, rules

LEAK = 'command = "python C:\\\\Users\\\\someone\\\\adapter.py"\n'


def _repository(files: dict[str, str]) -> tempfile.TemporaryDirectory[str]:
    """A real Git repository, because the scan reports on what Git would publish."""
    handle = tempfile.TemporaryDirectory()
    root = Path(handle.name)
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for arguments in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)
    return handle


def _commit(root: Path, message: str = "test: fixture") -> None:
    for key, value in (
        ("user.name", "Example Writer"),
        ("user.email", "writer@example.invalid"),
    ):
        subprocess.run(["git", "config", key, value], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)


class ArchivePngTests(unittest.TestCase):
    def test_history_audit_checks_pngs_removed_from_the_current_tree(self):
        dirty = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00tEXt\x00\x00\x00\x00"
        policy = (
            "[exposure]\ncheck_secrets = false\ncheck_links = false\nforbid_png_metadata = true\n"
        )
        for packed in (False, True):
            with self.subTest(packed=packed), _repository({"relkit.toml": policy}) as name:
                root = Path(name)
                artifact = root / ("artifact.zip" if packed else "picture.png")
                if packed:
                    with zipfile.ZipFile(artifact, "w") as archive:
                        archive.writestr("picture.png", dirty)
                else:
                    artifact.write_bytes(dirty)
                subprocess.run(["git", "add", "."], cwd=root, check=True)
                _commit(root)
                subprocess.run(["git", "rm", "-q", artifact.name], cwd=root, check=True)
                _commit(root, "test: remove current image")
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as diagnostics:
                    result = cli.main(["audit", "--history", "--root", str(root), "--no-download"])
                self.assertEqual(1, result)
                self.assertIn("png-metadata", diagnostics.getvalue())
                self.assertEqual([], audit.history_failures(root))
                self.assertEqual(
                    [],
                    audit.history_failures(root, forbid_png_metadata=True, exclude=[artifact.name]),
                )

    def test_png_metadata_is_checked_inside_nested_publication_archives(self):
        clean = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\x00\x00\x00\x00"
        dirty = clean[:8] + b"\x00\x00\x00\x00tEXt\x00\x00\x00\x00" + clean[8:]
        for staged in (False, True):
            for payload, expected in ((clean, True), (dirty, False)):
                with self.subTest(staged=staged, clean=expected), _repository({}) as name:
                    root = Path(name)
                    nested = io.BytesIO()
                    with zipfile.ZipFile(nested, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        archive.writestr("picture.PNG", payload)
                    with zipfile.ZipFile(root / "artifact.zip", "w") as archive:
                        archive.writestr("nested.zip", nested.getvalue())
                    subprocess.run(["git", "add", "artifact.zip"], cwd=root, check=True)
                    report = audit.scan(root, staged=staged, forbid_png_metadata=True)
                    self.assertEqual(expected, report.ok, report.failures)
                    if not expected:
                        self.assertTrue(
                            any(
                                "png-metadata" in f and "picture.PNG" in f for f in report.failures
                            ),
                            report.failures,
                        )
                    self.assertTrue(audit.scan(root, staged=staged).ok)
                    self.assertTrue(
                        audit.scan(
                            root, staged=staged, forbid_png_metadata=True, exclude=["artifact.zip"]
                        ).ok
                    )


class ScopeTests(unittest.TestCase):
    def test_an_unreadable_publication_candidate_fails_closed(self) -> None:
        with (
            _repository({"kept.md": "clean\n"}) as name,
            patch.object(audit, "_payload", side_effect=OSError("fixture")),
        ):
            report = audit.scan(Path(name))

        self.assertFalse(report.ok)
        self.assertEqual(["could not read kept.md"], report.failures)

    def test_an_unstaged_deletion_is_absent_from_the_worktree_boundary(self) -> None:
        with _repository({"gone.md": LEAK}) as name:
            (Path(name) / "gone.md").unlink()
            report = audit.scan(Path(name))

        self.assertTrue(report.ok, report.failures)
        self.assertEqual([], report.unreadable)

    def test_the_same_deletion_does_not_change_the_staged_boundary(self) -> None:
        with _repository({"gone.md": LEAK}) as name:
            (Path(name) / "gone.md").unlink()
            report = audit.scan(Path(name), staged=True, include_candidates=False)

        self.assertEqual(["gone.md: home-directory"], report.failures)

    def test_sparse_checkout_entries_are_read_from_the_index(self) -> None:
        with _repository({"sparse.toml": LEAK}) as name:
            root = Path(name)
            subprocess.run(
                ["git", "update-index", "--skip-worktree", "sparse.toml"],
                cwd=root,
                check=True,
            )
            (root / "sparse.toml").unlink()

            report = audit.scan(root)

        self.assertEqual(["sparse.toml: home-directory"], report.failures)

    def test_an_untracked_unignored_file_is_this_gate_s_business(self) -> None:
        """Uncommitted is not safe. It is one `git add -A` from the history."""
        with _repository({"kept.md": "clean\n"}) as name:
            (Path(name) / "scratch.md").write_text(LEAK, encoding="utf-8")
            report = audit.scan(Path(name))

        self.assertEqual(["scratch.md: home-directory"], report.failures)

    def test_an_ignored_file_is_not(self) -> None:
        with _repository({"kept.md": "clean\n", ".gitignore": "scratch.md\n"}) as name:
            (Path(name) / "scratch.md").write_text(LEAK, encoding="utf-8")
            report = audit.scan(Path(name))

        self.assertTrue(report.ok, report.failures)


class RatchetTests(unittest.TestCase):
    def test_a_new_finding_fails(self) -> None:
        with _repository({"config.toml": LEAK}) as name:
            report = audit.scan(Path(name))

        self.assertFalse(report.ok)
        self.assertEqual(["config.toml: home-directory"], report.failures)

    def test_a_recorded_finding_passes_and_is_reported(self) -> None:
        with _repository({"config.toml": LEAK}) as name:
            report = audit.scan(Path(name), baseline={"config.toml": [rules.HOME_DIRECTORY]})

        self.assertTrue(report.ok, report.failures)
        self.assertEqual([audit.Finding("config.toml", rules.HOME_DIRECTORY)], report.baselined)

    def test_a_second_kind_in_a_recorded_file_still_fails(self) -> None:
        """Recording one finding is not a licence for the next one in the same file."""
        with _repository({"config.toml": LEAK + 'args = ["../neighbour/tool.ps1"]\n'}) as name:
            report = audit.scan(Path(name), baseline={"config.toml": [rules.HOME_DIRECTORY]})

        self.assertEqual(["config.toml: escapes-repository"], report.failures)

    def test_owner_privacy_cannot_be_accepted_as_baseline_debt(self) -> None:
        with _repository({"notes.txt": "OwnerWorkbench instructions\n"}) as name:
            report = audit.scan(
                Path(name),
                owner_workflows=("OwnerWorkbench",),
                baseline={"notes.txt": [rules.OWNER_WORKFLOW]},
            )

        self.assertEqual(
            ["notes.txt: owner-workflow (declared by the private owner policy)"], report.failures
        )

    def test_a_record_that_no_longer_matches_fails(self) -> None:
        with _repository({"config.toml": "clean = true\n"}) as name:
            report = audit.scan(Path(name), baseline={"config.toml": [rules.HOME_DIRECTORY]})

        self.assertFalse(report.ok)
        self.assertIn("no longer carries", report.failures[0])

    def test_the_baseline_can_only_shrink(self) -> None:
        """Fixing a finding forces the record to be updated in the same change."""
        with _repository({"config.toml": LEAK}) as name:
            root = Path(name)
            before = audit.scan(root, baseline={"config.toml": [rules.HOME_DIRECTORY]})
            (root / "config.toml").write_text("clean = true\n", encoding="utf-8")
            after = audit.scan(root, baseline={"config.toml": [rules.HOME_DIRECTORY]})
            settled = audit.scan(root, baseline={})

        self.assertTrue(before.ok)
        self.assertFalse(after.ok)
        self.assertTrue(settled.ok, settled.failures)


class PrivateValueTests(unittest.TestCase):
    def test_names_come_from_the_caller(self) -> None:
        with _repository({"README.md": "built on Someservice\n"}) as name:
            without = audit.scan(Path(name))
            with_names = audit.scan(Path(name), names=("Someservice",))

        self.assertTrue(without.ok)
        self.assertEqual(["README.md: private-value"], with_names.failures)

    def test_names_are_case_insensitive_and_cover_paths(self) -> None:
        with _repository({"internalservice/README.md": "clean\n"}) as name:
            report = audit.scan(Path(name), names=("InternalService",))

        self.assertEqual(["internalservice/README.md: private-value"], report.failures)

    def test_provider_contract_is_public_but_owner_data_is_private(self) -> None:
        with _repository(
            {
                "provider.md": "AgentMemory supplies opaque memory references.\n",
                "example.md": "namespace = owner/private-workflow\n",
            }
        ) as name:
            report = audit.scan(Path(name), names=("owner/private-workflow",))

        self.assertEqual(["example.md: private-value"], report.failures)

    def test_a_wrapped_owner_value_is_still_private(self) -> None:
        value = "already holds records, so renaming it would orphan them"
        with _repository(
            {"README.md": "already holds records, so renaming it would\norphan them\n"}
        ) as name:
            report = audit.scan(Path(name), names=(value,))

        self.assertEqual(["README.md: private-value"], report.failures)

    def test_a_clone_without_the_list_does_not_fail_on_records_it_cannot_check(self) -> None:
        """A checkout that has no name list runs the structural rules and stays green."""
        with _repository({"README.md": "built on Someservice\n"}) as name:
            report = audit.scan(Path(name), baseline={"README.md": [rules.DECLARED_NAME]})

        self.assertTrue(report.ok, report.failures)


class ExclusionTests(unittest.TestCase):
    def test_an_excluded_path_is_not_scanned(self) -> None:
        """A repository must be able to hold a fixture of what its rules detect."""
        with _repository({"tests/fixture.toml": LEAK}) as name:
            report = audit.scan(Path(name), exclude=["tests/*"])

        self.assertTrue(report.ok, report.failures)
        self.assertEqual(["tests/fixture.toml"], report.excluded)

    def test_exclusion_does_not_leave_a_stale_baseline_behind(self) -> None:
        with _repository({"tests/fixture.toml": LEAK}) as name:
            report = audit.scan(
                Path(name),
                exclude=["tests/*"],
                baseline={"tests/fixture.toml": [rules.HOME_DIRECTORY]},
            )

        self.assertTrue(report.ok, report.failures)

    def test_a_path_outside_the_pattern_is_still_scanned(self) -> None:
        with _repository({"tests/fixture.toml": LEAK, "config.toml": LEAK}) as name:
            report = audit.scan(Path(name), exclude=["tests/*"])

        self.assertEqual(["config.toml: home-directory"], report.failures)

    def test_exclusions_never_suppress_private_owner_rules(self) -> None:
        pattern = rules.PrivatePattern(
            name="private-record",
            kind=rules.PERSONAL_DATA,
            expression=r"rec_[0-9]+",
        )
        with _repository({"tests/fixture.txt": "OwnerWorkbench uses rec_1234\n"}) as name:
            report = audit.scan(
                Path(name),
                exclude=["tests/*"],
                owner_workflows=("OwnerWorkbench",),
                private_patterns=(pattern,),
            )

        self.assertTrue(any("owner-workflow" in item for item in report.failures), report.failures)
        self.assertTrue(any("personal-data" in item for item in report.failures), report.failures)

    def test_exclusions_and_baselines_never_suppress_external_lfs_content(self) -> None:
        pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{'a' * 64}\nsize 123\n"
        with _repository({"tests/asset.bin": pointer}) as name:
            report = audit.scan(
                Path(name),
                exclude=["tests/*"],
                baseline={"tests/asset.bin": [audit.EXTERNAL_CONTENT]},
            )

        self.assertTrue(any("external-content" in item for item in report.failures))

    def test_exclusions_cannot_hide_uninspected_or_invalid_archives(self) -> None:
        for relative, kind in (
            ("tests/private.tar.gz", audit.EXTERNAL_CONTENT),
            ("tests/broken.zip", audit.ARCHIVE_LIMIT),
            ("tests/history.bundle", audit.EXTERNAL_REPOSITORY),
        ):
            with self.subTest(relative=relative), _repository({relative: "opaque\n"}) as name:
                report = audit.scan(
                    Path(name),
                    exclude=["tests/*"],
                    baseline={relative: [kind]},
                )

            self.assertTrue(any(kind in item for item in report.failures), report.failures)

    def test_an_unsupported_archive_cannot_hide_behind_an_innocent_extension(self) -> None:
        with _repository({"asset.bin": "placeholder\n"}) as name:
            root = Path(name)
            (root / "asset.bin").write_bytes(gzip.compress(b"private payload"))

            report = audit.scan(root)

        self.assertTrue(any("external-content" in item for item in report.failures))


class PathTests(unittest.TestCase):
    def test_git_lfs_objects_are_not_silently_treated_as_audited_blobs(self) -> None:
        pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{'a' * 64}\nsize 123\n"
        with _repository({"asset.bin": pointer}) as name:
            report = audit.scan(Path(name))

        self.assertTrue(any("external-content" in item for item in report.failures))

    def test_a_staged_gitlink_is_an_unaudited_external_repository(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo", f"160000,{commit},vendor"],
                cwd=root,
                check=True,
            )

            staged = audit.scan(
                root,
                staged=True,
                include_candidates=False,
                exclude=["vendor"],
                baseline={"vendor": [audit.EXTERNAL_REPOSITORY]},
            )
            _commit(root)
            historical = audit.history_failures(root, exclude=["vendor"])

        self.assertTrue(any("external-repository" in item for item in staged.failures))
        self.assertTrue(any("external-repository" in item for item in historical), historical)

    def test_staged_git_symlink_target_cannot_leave_the_repository(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=root,
                input="../outside/private.txt",
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo", f"120000,{blob},link"],
                cwd=root,
                check=True,
            )

            report = audit.scan(root, staged=True, include_candidates=False)

        self.assertTrue(
            any("escapes-repository" in item for item in report.failures), report.failures
        )

    def test_a_worktree_file_replacing_an_index_symlink_is_scanned_as_a_file(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=root,
                input="../outside/private.txt",
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo", f"120000,{blob},link"],
                cwd=root,
                check=True,
            )
            (root / "link").write_text("ordinary file\n", encoding="utf-8")

            report = audit.scan(root)

        self.assertTrue(report.ok, report.failures)

    def test_a_symlink_target_that_stays_inside_the_repository_is_portable(self) -> None:
        self.assertFalse(audit._symlink_target_escapes("docs/link", b"../src/module.py"))

    def test_utf16_owner_values_are_scanned(self) -> None:
        payload = "SECRET OWNER VALUE".encode("utf-16")

        details = audit._payload_details(
            "notes.txt",
            payload,
            names=("SECRET OWNER VALUE",),
            owner_workflows=(),
            private_patterns=(),
            forbidden_suffixes=(),
            private_paths=(),
            private_files=(),
            private_suffixes=(),
            allowed_users=(),
            forbid_ai_attribution=False,
            forbid_internal_planning=False,
            forbid_machine_observations=False,
            providers={},
            inspect_archives=False,
        )

        self.assertIn(rules.DECLARED_NAME, details)

    def test_ascii_private_values_inside_non_utf_binary_are_scanned(self) -> None:
        details = audit._payload_details(
            "artifact.bin",
            b"\xff\x80prefix SECRET OWNER VALUE suffix\x00\xfe",
            names=("SECRET OWNER VALUE",),
            owner_workflows=(),
            private_patterns=(),
            forbidden_suffixes=(),
            private_paths=(),
            private_files=(),
            private_suffixes=(),
            allowed_users=(),
            forbid_ai_attribution=False,
            forbid_internal_planning=False,
            forbid_machine_observations=False,
            providers={},
            inspect_archives=False,
        )

        self.assertIn(rules.DECLARED_NAME, details)

    def test_utf16_without_a_bom_is_scanned_when_the_byte_lanes_identify_it(self) -> None:
        payload = "SECRET OWNER VALUE".encode("utf-16-le")

        details = audit._payload_details(
            "notes.txt",
            payload,
            names=("SECRET OWNER VALUE",),
            owner_workflows=(),
            private_patterns=(),
            forbidden_suffixes=(),
            private_paths=(),
            private_files=(),
            private_suffixes=(),
            allowed_users=(),
            forbid_ai_attribution=False,
            forbid_internal_planning=False,
            forbid_machine_observations=False,
            providers={},
            inspect_archives=False,
        )

        self.assertIn(rules.DECLARED_NAME, details)

    def test_utf32_without_a_bom_is_scanned_when_the_byte_lanes_identify_it(self) -> None:
        for encoding in ("utf-32-le", "utf-32-be"):
            with self.subTest(encoding=encoding):
                payload = "SECRET OWNER VALUE".encode(encoding)
                details = audit._payload_details(
                    "notes.txt",
                    payload,
                    names=("SECRET OWNER VALUE",),
                    owner_workflows=(),
                    private_patterns=(),
                    forbidden_suffixes=(),
                    private_paths=(),
                    private_files=(),
                    private_suffixes=(),
                    allowed_users=(),
                    forbid_ai_attribution=False,
                    forbid_internal_planning=False,
                    forbid_machine_observations=False,
                    providers={},
                    inspect_archives=False,
                )

                self.assertIn(rules.DECLARED_NAME, details)

    def test_a_forbidden_extension_is_reported_even_when_unreadable_as_text(self) -> None:
        with _repository({"placeholder.md": "x\n"}) as name:
            root = Path(name)
            (root / "store.sqlite3").write_bytes(b"\x00\x01binary\xff")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
            report = audit.scan(root)

        self.assertEqual(["store.sqlite3: forbidden-kind"], report.failures)

    def test_staged_scan_reads_the_index_not_a_cleaner_worktree_copy(self) -> None:
        with _repository({"config.toml": LEAK}) as name:
            root = Path(name)
            (root / "config.toml").write_text("clean = true\n", encoding="utf-8")
            report = audit.scan(root, staged=True)

        self.assertEqual(["config.toml: home-directory"], report.failures)

    def test_png_metadata_can_be_forbidden_once_for_all_fixtures(self) -> None:
        with _repository({"placeholder.md": "x\n"}) as name:
            root = Path(name)
            payload = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00tEXt" + b"\x00\x00\x00\x00"
            (root / "fixture.png").write_bytes(payload)
            subprocess.run(["git", "add", "fixture.png"], cwd=root, check=True)
            report = audit.scan(root, forbid_png_metadata=True, staged=True)

        self.assertEqual(["fixture.png: png-metadata (PNG tEXt chunk)"], report.failures)

    def test_a_truncated_png_says_so_instead_of_naming_a_metadata_chunk(self) -> None:
        # The owner action differs: one file has metadata to strip, the other is
        # damaged and nothing can be concluded about it.
        with _repository({"placeholder.md": "x\n"}) as name:
            root = Path(name)
            (root / "fixture.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x10\x00IDAT" + b"\x00" * 8
            )
            subprocess.run(["git", "add", "fixture.png"], cwd=root, check=True)
            report = audit.scan(root, forbid_png_metadata=True, staged=True)

        self.assertEqual(
            [
                (
                    "fixture.png: png-metadata (PNG chunk stream is truncated; "
                    "metadata cannot be ruled out)"
                )
            ],
            report.failures,
        )


class SemanticPublicationTests(unittest.TestCase):
    def test_owner_workflow_internal_planning_and_machine_observation_are_separate(self) -> None:
        with _repository(
            {
                "AGENTS.md": "Someservice coordinates local work.\n",
                "docs/plan.md": "card: 123\n",
                "tests/support.py": "# The real installation has 42 folders.\n",
            }
        ) as name:
            report = audit.scan(
                Path(name),
                owner_workflows=("Someservice",),
                forbid_internal_planning=True,
                forbid_machine_observations=True,
            )

        self.assertTrue(any("owner-workflow" in item for item in report.failures))
        self.assertTrue(any("internal-planning" in item for item in report.failures))
        self.assertTrue(any("machine-observation" in item for item in report.failures))

    def test_provider_contract_does_not_allow_personal_provider_data(self) -> None:
        pattern = rules.PrivatePattern(
            name="private-namespace",
            kind=rules.PERSONAL_DATA,
            expression=r"owner/private-[a-z]+",
        )
        with _repository(
            {
                "docs/provider.md": (
                    "Someservice supplies opaque records.\nnamespace=owner/private-workflow\n"
                )
            }
        ) as name:
            report = audit.scan(
                Path(name),
                private_patterns=(pattern,),
                providers={"Someservice": ("docs/*",)},
            )

        self.assertEqual(1, len(report.failures))
        self.assertIn("personal-data", report.failures[0])

    def test_provider_mentions_outside_the_public_contract_are_findings(self) -> None:
        with _repository({"AGENTS.md": "Someservice runs local tasks.\n"}) as name:
            report = audit.scan(Path(name), providers={"Someservice": ("src/*", "docs/*")})

        self.assertIn("provider-surface", report.failures[0])


class ProvenanceTests(unittest.TestCase):
    def test_required_fixture_provenance_must_be_declared(self) -> None:
        with _repository({"tests/generated/data.json": "{}\n"}) as name:
            missing = audit.scan(Path(name), provenance_required=("tests/generated/*",))
            synthetic = audit.scan(
                Path(name),
                provenance_required=("tests/generated/*",),
                provenance={"tests/generated/*": "synthetic"},
            )

        self.assertIn("provenance-missing", missing.failures[0])
        self.assertTrue(synthetic.ok, synthetic.failures)

    def test_machine_derived_material_is_a_finding_even_when_declared(self) -> None:
        with _repository({"tests/generated/data.json": "{}\n"}) as name:
            report = audit.scan(Path(name), provenance={"tests/generated/*": "machine-derived"})

        self.assertIn("machine-derived", report.failures[0])


class ArchiveTests(unittest.TestCase):
    @staticmethod
    def _archive(entries: dict[str, str]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, text in entries.items():
                archive.writestr(name, text)
        return output.getvalue()

    def test_private_values_inside_a_publication_archive_are_scanned(self) -> None:
        with _repository({"keep.md": "clean\n"}) as name:
            root = Path(name)
            (root / "artifact.pyz").write_bytes(
                self._archive({"package/config.txt": "uses Someservice\n"})
            )
            subprocess.run(["git", "add", "artifact.pyz"], cwd=root, check=True)
            report = audit.scan(root, names=("Someservice",))

        self.assertEqual(1, len(report.failures))
        self.assertIn("private-value", report.failures[0])
        self.assertIn("archive entry", report.failures[0])

    def test_zip_containers_are_recognized_by_content_not_only_extension(self) -> None:
        payload = self._archive({"word/document.xml": "uses Someservice\n"})
        for relative in ("report.docx", "artifact.bin"):
            with self.subTest(relative=relative), _repository({"keep.md": "clean\n"}) as name:
                root = Path(name)
                (root / relative).write_bytes(payload)
                subprocess.run(["git", "add", relative], cwd=root, check=True)

                report = audit.scan(root, names=("Someservice",))

            self.assertTrue(any("private-value" in item for item in report.failures))

    def test_archive_entries_cannot_escape_the_artifact_root(self) -> None:
        with _repository({"keep.md": "clean\n"}) as name:
            root = Path(name)
            (root / "artifact.zip").write_bytes(self._archive({"../outside.txt": "clean\n"}))
            subprocess.run(["git", "add", "artifact.zip"], cwd=root, check=True)
            report = audit.scan(root)

        self.assertIn("archive-path", report.failures[0])

    def test_a_file_with_an_archive_suffix_must_be_a_readable_archive(self) -> None:
        with _repository({"artifact.zip": "not an archive\n"}) as name:
            report = audit.scan(Path(name))

        self.assertTrue(any("archive-limit" in item for item in report.failures), report.failures)

    def test_nested_archives_are_scanned(self) -> None:
        nested = self._archive({"package/private.txt": "uses Someservice\n"})
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("nested.zip", nested)
        with _repository({"keep.md": "clean\n"}) as name:
            root = Path(name)
            (root / "artifact.zip").write_bytes(output.getvalue())
            subprocess.run(["git", "add", "artifact.zip"], cwd=root, check=True)
            report = audit.scan(root, names=("Someservice",))

        self.assertTrue(any("private-value" in item for item in report.failures), report.failures)

    def test_nested_archives_share_one_uncompressed_size_budget(self) -> None:
        nested = self._archive({"package/data.txt": "x" * 20})
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("nested.zip", nested)
        with _repository({"keep.md": "clean\n"}) as name:
            root = Path(name)
            (root / "artifact.zip").write_bytes(output.getvalue())
            subprocess.run(["git", "add", "artifact.zip"], cwd=root, check=True)
            with patch.object(audit, "MAX_ARCHIVE_TOTAL_SIZE", len(nested) + 10):
                report = audit.scan(root)

        self.assertTrue(any("archive-limit" in item for item in report.failures), report.failures)

    def test_an_unsupported_archive_nested_in_zip_fails_closed(self) -> None:
        with _repository({"keep.md": "clean\n"}) as name:
            root = Path(name)
            (root / "artifact.zip").write_bytes(
                self._archive({"package/private.tar.gz": "opaque compressed data"})
            )
            subprocess.run(["git", "add", "artifact.zip"], cwd=root, check=True)

            report = audit.scan(root)

        self.assertTrue(
            any("external-content" in item for item in report.failures), report.failures
        )

    def test_archive_symlinks_cannot_escape_the_artifact_root(self) -> None:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            entry = zipfile.ZipInfo("package/link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../../private")
        with _repository({"keep.md": "clean\n"}) as name:
            root = Path(name)
            (root / "artifact.zip").write_bytes(output.getvalue())
            subprocess.run(["git", "add", "artifact.zip"], cwd=root, check=True)
            report = audit.scan(root)

        self.assertTrue(any("archive-path" in item for item in report.failures), report.failures)

    def test_archive_symlinks_may_target_a_sibling_inside_the_artifact(self) -> None:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("target.txt", "clean\n")
            entry = zipfile.ZipInfo("package/link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../target.txt")
        with _repository({"keep.md": "clean\n"}) as name:
            root = Path(name)
            (root / "artifact.zip").write_bytes(output.getvalue())
            subprocess.run(["git", "add", "artifact.zip"], cwd=root, check=True)

            report = audit.scan(root)

        self.assertTrue(report.ok, report.failures)


class HistoryTests(unittest.TestCase):
    def test_replace_refs_and_grafts_invalidate_a_history_verdict(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "update-ref", f"refs/replace/{commit}", commit],
                cwd=root,
                check=True,
            )
            graft = root / ".git" / "info" / "grafts"
            graft.write_text(f"{commit}\n", encoding="utf-8")

            failures = audit.history_failures(root)

        self.assertTrue(any("replace ref" in item for item in failures), failures)
        self.assertTrue(any("info/grafts" in item for item in failures), failures)

    def test_shallow_history_cannot_produce_a_complete_verdict(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            original_git = audit._git

            def shallow_git(repository, arguments, *, stdin=None):
                if arguments == ["rev-parse", "--is-shallow-repository"]:
                    return subprocess.CompletedProcess(arguments, 0, stdout="true\n", stderr="")
                return original_git(repository, arguments, stdin=stdin)

            with patch.object(audit, "_git", side_effect=shallow_git):
                failures = audit.history_failures(root)

        self.assertTrue(any("non-shallow" in item for item in failures), failures)

    def test_raw_commit_headers_are_scanned_for_owner_metadata(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            payload = (
                b"tree 0000000000000000000000000000000000000000\n"
                b"x-owner-workflow OwnerWorkbench\n\nclean message\n"
            )
            with patch.object(
                audit,
                "_batch_objects",
                return_value=[("a" * 40, payload)],
            ):
                failures = audit._commit_metadata_failures(
                    Path(name),
                    ("a" * 40,),
                    names=(),
                    owner_workflows=("OwnerWorkbench",),
                    private_patterns=(),
                    allowed_users=(),
                    forbid_ai_attribution=False,
                    forbid_internal_planning=False,
                    forbid_machine_observations=False,
                )

        self.assertTrue(any("commit-metadata: owner-workflow" in item for item in failures))

    def test_history_checks_wrapped_owner_values(self) -> None:
        value = "already holds records, so renaming it would orphan them"
        with _repository(
            {"README.md": "already holds records, so renaming it would\norphan them\n"}
        ) as name:
            root = Path(name)
            _commit(root)
            failures = audit.history_failures(root, names=(value,))

        self.assertTrue(any("private-value" in item for item in failures), failures)

    def test_history_checks_commit_messages_for_declared_names(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root, "docs: explain InternalService workflow")
            failures = audit.history_failures(root, names=("InternalService",))

        self.assertTrue(any("commit-message: private-value" in item for item in failures))

    def test_history_commit_messages_may_describe_a_traversal_fixture(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root, "test: reject an ../ traversal attempt")
            failures = audit.history_failures(root)

        self.assertFalse(any("commit-message" in item for item in failures), failures)

    def test_history_scope_ignores_synthetic_client_checkpoint_refs(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(["git", "switch", "-q", "-c", "client-checkpoint"], cwd=root, check=True)
            private = root / ".someclient" / "settings.json"
            private.parent.mkdir()
            private.write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            _commit(root)
            checkpoint = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(["git", "switch", "-q", branch], cwd=root, check=True)
            subprocess.run(
                ["git", "branch", "-D", "client-checkpoint"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "update-ref", "refs/codex/checkpoints/test", checkpoint],
                cwd=root,
                check=True,
            )

            failures = audit.history_failures(root, private_paths=[".someclient"])

        self.assertEqual([], failures)

    def test_history_scope_includes_fetched_pull_request_refs(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(["git", "switch", "-q", "-c", "pull-fixture"], cwd=root, check=True)
            private = root / ".someclient" / "settings.json"
            private.parent.mkdir()
            private.write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            _commit(root)
            pull = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(["git", "switch", "-q", branch], cwd=root, check=True)
            subprocess.run(
                ["git", "branch", "-D", "pull-fixture"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "update-ref", "refs/pull/123/head", pull], cwd=root, check=True)

            failures = audit.history_failures(root, private_paths=[".someclient"])

        self.assertTrue(any("private-path" in item for item in failures), failures)

    def test_history_verdict_can_require_a_clean_worktree(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            self.assertEqual((), audit.worktree_changes(root))
            (root / "kept.md").write_text("changed\n", encoding="utf-8")

            changes = audit.worktree_changes(root)

        self.assertEqual(1, len(changes))
        self.assertIn("kept.md", changes[0])

    def test_history_checks_paths_content_and_identities(self) -> None:
        with _repository(
            {
                ".someclient/settings.json": "{}\n",
                "config.toml": LEAK,
            }
        ) as name:
            root = Path(name)
            _commit(root)
            failures = audit.history_failures(
                root,
                private_paths=[".someclient"],
                allowed_identities=["Somebody Else <else@example.invalid>"],
            )

        self.assertTrue(any("private-path" in failure for failure in failures), failures)
        self.assertTrue(any("home-directory" in failure for failure in failures), failures)
        self.assertTrue(any("identity is not allowed" in failure for failure in failures), failures)

    def test_history_checks_public_ref_names_and_annotated_tag_messages(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            subprocess.run(
                ["git", "branch", "OwnerWorkbench-private"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "tag", "-a", "v1", "-m", "OwnerWorkbench release instructions"],
                cwd=root,
                check=True,
            )

            failures = audit.history_failures(
                root,
                owner_workflows=("OwnerWorkbench",),
            )

        self.assertTrue(any("history ref" in item for item in failures), failures)
        self.assertTrue(any("history tag" in item for item in failures), failures)

    def test_history_identity_policy_includes_annotated_taggers(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            environment = {
                **os.environ,
                "GIT_COMMITTER_NAME": "Unexpected Tagger",
                "GIT_COMMITTER_EMAIL": "tagger@example.invalid",
            }
            subprocess.run(
                ["git", "tag", "-a", "v1", "-m", "release"],
                cwd=root,
                check=True,
                env=environment,
            )

            failures = audit.history_failures(
                root,
                allowed_identities=["Example Writer <writer@example.invalid>"],
            )

        self.assertTrue(any("tag identity is not allowed" in item for item in failures), failures)

    def test_history_exclusions_are_for_deliberate_rule_fixtures(self) -> None:
        with _repository({"tests/fixture.toml": LEAK}) as name:
            root = Path(name)
            _commit(root)
            failures = audit.history_failures(root, exclude=["tests/*"])

        self.assertEqual([], failures)

    def test_history_exclusions_never_suppress_private_owner_rules(self) -> None:
        with _repository({"tests/fixture.txt": "OwnerWorkbench internal workflow\n"}) as name:
            root = Path(name)
            _commit(root)
            failures = audit.history_failures(
                root,
                exclude=["tests/*"],
                owner_workflows=("OwnerWorkbench",),
            )

        self.assertTrue(any("owner-workflow" in item for item in failures), failures)

    def test_history_checks_ai_attribution_trailers(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            _commit(root)
            original_git = audit._git

            def git_with_machine_trailer(
                repository: Path, arguments: list[str] | tuple[str, ...], *, stdin=None
            ):
                if "--format=%H%x1f%B%x1e" in arguments:
                    marker = "Co-" + "Authored-By: " + "Clau" + "de <bot@example.invalid>"
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout=f"{'a' * 40}\x1ffeat: fixture\n\n{marker}\x1e",
                        stderr="",
                    )
                return original_git(repository, arguments, stdin=stdin)

            with patch.object(audit, "_git", side_effect=git_with_machine_trailer):
                failures = audit.history_failures(root, forbid_ai_attribution=True)

        self.assertTrue(any("commit-message: ai-attribution" in item for item in failures))

    def test_history_checks_internal_planning_in_old_blobs(self) -> None:
        with _repository({"docs/plan.md": "card: 123\n"}) as name:
            root = Path(name)
            _commit(root)
            (root / "docs/plan.md").write_text("public plan\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            _commit(root)
            failures = audit.history_failures(root, forbid_internal_planning=True)

        self.assertTrue(any("internal-planning" in item for item in failures), failures)

    def test_history_checks_typed_owner_rules_in_old_blobs(self) -> None:
        pattern = rules.PrivatePattern(
            name="record-reference",
            kind=rules.PERSONAL_DATA,
            expression=r"rec_[0-9]+",
        )
        with _repository({"AGENTS.md": "Someservice coordinates rec_1234.\n"}) as name:
            root = Path(name)
            _commit(root)
            (root / "AGENTS.md").write_text("public instructions\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            _commit(root)
            failures = audit.history_failures(
                root,
                owner_workflows=("Someservice",),
                private_patterns=(pattern,),
            )

        self.assertTrue(any("owner-workflow" in item for item in failures), failures)
        self.assertTrue(any("personal-data" in item for item in failures), failures)

    def test_history_checks_inside_archives(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("package/config.txt", "uses Someservice\n")
            (root / "artifact.pyz").write_bytes(archive.getvalue())
            subprocess.run(["git", "add", "artifact.pyz"], cwd=root, check=True)
            _commit(root)
            failures = audit.history_failures(root, names=("Someservice",))

        self.assertTrue(any("archive entry" in item for item in failures), failures)

    def test_history_checks_utf16_structural_and_owner_content(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            (root / "old.txt").write_bytes(
                r"C:\Users\User\private SECRET OWNER VALUE".encode("utf-16")
            )
            subprocess.run(["git", "add", "old.txt"], cwd=root, check=True)
            _commit(root)
            (root / "old.txt").write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "add", "old.txt"], cwd=root, check=True)
            _commit(root)

            failures = audit.history_failures(root, names=("SECRET OWNER VALUE",))

        self.assertTrue(any("home-directory" in item for item in failures), failures)
        self.assertTrue(any("private-value" in item for item in failures), failures)

    def test_history_checks_git_symlink_targets(self) -> None:
        with _repository({"kept.md": "clean\n"}) as name:
            root = Path(name)
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=root,
                input="/outside/private.txt",
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo", f"120000,{blob},link"],
                cwd=root,
                check=True,
            )
            _commit(root)

            failures = audit.history_failures(root)

        self.assertTrue(any("escapes-repository" in item for item in failures), failures)

    def test_history_checks_every_path_when_one_blob_is_copied_to_multiple_surfaces(self) -> None:
        wrapped = "Some\nService supplies records.\n"
        with _repository(
            {
                "000-allowed/provider.txt": wrapped,
                "zzz-disallowed/provider.txt": wrapped,
            }
        ) as name:
            root = Path(name)
            _commit(root)
            blob_paths = audit._changed_blob_paths(root, ())
            failures = audit.history_failures(
                root,
                providers={"Some Service": ("000-allowed/*",)},
            )

        self.assertTrue(
            any(
                paths == {"000-allowed/provider.txt", "zzz-disallowed/provider.txt"}
                for paths in blob_paths.values()
            ),
            blob_paths,
        )
        self.assertTrue(
            any(
                "zzz-disallowed/provider.txt" in item and "provider-surface" in item
                for item in failures
            ),
            failures,
        )


if __name__ == "__main__":
    unittest.main()
