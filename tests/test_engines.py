from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from glob import glob
from pathlib import Path
from unittest.mock import patch

from releasekit import engines


class EngineCommandTests(unittest.TestCase):
    def test_an_engine_timeout_stops_its_child_before_returning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ready, trigger, late = root / "ready", root / "trigger", root / "late"
            child = (
                "import pathlib, time\n"
                f"pathlib.Path({str(ready)!r}).touch()\n"
                "deadline = time.monotonic() + 10\n"
                f"while not pathlib.Path({str(trigger)!r}).exists() and time.monotonic() < deadline:\n"
                " time.sleep(0.01)\n"
                f"if pathlib.Path({str(trigger)!r}).exists(): pathlib.Path({str(late)!r}).touch()\n"
            )
            parent = (
                "import subprocess, sys\n"
                f"subprocess.Popen([sys.executable, '-S', '-c', {child!r}], "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait()\n"
            )
            with (
                patch.object(engines, "ENGINE_TIMEOUT_SECONDS", 1),
                self.assertRaisesRegex(RuntimeError, "timed out"),
            ):
                engines._run([sys.executable, "-S", "-c", parent], root=root)
            self.assertTrue(ready.exists(), "the child must start for this control to count")
            trigger.touch()
            time.sleep(0.5)
            self.assertFalse(late.exists(), "the engine child survived the reported timeout")

    def test_unconfirmed_engine_cleanup_preserves_the_entire_owned_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "example.txt").write_text("public content\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            owned = []

            def incomplete(*args, **kwargs):
                runtime = Path(kwargs["env"]["TMPDIR"])
                owned.append(runtime.parent)
                (runtime / "native.log").write_text("cleanup could not be established")
                raise engines.processes.CleanupError("owned cleanup is unconfirmed")

            with (
                patch.object(
                    engines.toolchain, "resolve", return_value=root / ".git/missing-scanner"
                ),
                patch.object(engines.processes, "run", side_effect=incomplete),
                self.assertRaisesRegex(engines.processes.CleanupError, "unconfirmed"),
            ):
                engines.betterleaks(
                    root,
                    config=".betterleaks.toml",
                    history=False,
                    staged=False,
                    include_candidates=True,
                    allow_download=False,
                )
            self.assertTrue((owned[0] / "source/example.txt").is_file())
            self.assertEqual(
                "cleanup could not be established", (owned[0] / "runtime/native.log").read_text()
            )

    def test_a_sparse_index_and_replacement_parent_link_cannot_silently_change_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "docs").mkdir()
            (root / "docs/example.md").write_text("indexed private content\n", encoding="utf-8")
            (root / "public").mkdir()
            (root / "public/example.md").write_text("public replacement\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "update-index", "--skip-worktree", "docs/example.md"],
                cwd=root,
                check=True,
            )
            (root / "docs/example.md").unlink()
            (root / "docs").rmdir()
            try:
                (root / "docs").symlink_to("public", target_is_directory=True)
            except OSError:
                self.skipTest("symlinks unavailable")
            snapshot = root / ".git/snapshot"
            snapshot.mkdir()

            with self.assertRaisesRegex(RuntimeError, "symlink.*sparse index"):
                engines._materialize_worktree(root, snapshot, include_candidates=True)

    def test_runtime_cache_is_outside_the_scan_and_only_completed_scans_discard_it(self):
        for native in (0, 10, 1):
            with self.subTest(native=native), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                (root / "example.txt").write_text("public content\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=root, check=True)
                owned = []

                def inspect(command, *, environment, native=native, owned=owned, **kwargs):
                    snapshot = Path(command[command.index("dir") + 1])
                    runtime = Path(environment["TMPDIR"])
                    self.assertFalse(runtime.is_relative_to(snapshot))
                    self.assertEqual(snapshot.parent, runtime.parent)
                    owned.append(runtime.parent)
                    (runtime / "scanner-cache.bin").write_bytes(b"synthetic runtime cache")
                    self.assertEqual(["example.txt"], [item.name for item in snapshot.iterdir()])
                    return native

                with (
                    patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
                    patch.object(engines, "_run", side_effect=inspect),
                ):
                    self.assertEqual(
                        native,
                        engines.betterleaks(
                            root,
                            config=".betterleaks.toml",
                            history=False,
                            staged=False,
                            include_candidates=True,
                            allow_download=False,
                        ),
                    )
                self.assertEqual(native == 1, owned[0].exists())
                if native == 1:
                    self.assertEqual(
                        b"synthetic runtime cache",
                        next(owned[0].rglob("scanner-cache.bin")).read_bytes(),
                    )

    def test_worktree_snapshot_does_not_traverse_a_replacement_directory_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "docs").mkdir()
            (root / "docs/example.md").write_text("public content\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            private = root / ".git/private-source"
            private.mkdir()
            (private / "example.md").write_text("private content\n", encoding="utf-8")
            (root / "docs/example.md").unlink()
            (root / "docs").rmdir()
            try:
                (root / "docs").symlink_to(private, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks unavailable")
            snapshot = root / ".git/snapshot"
            snapshot.mkdir()

            engines._materialize_worktree(root, snapshot, include_candidates=True)

            self.assertEqual(str(private), (snapshot / "docs").read_text(encoding="utf-8"))
            self.assertFalse((snapshot / "docs").is_symlink())
            self.assertEqual([snapshot / "docs"], list(snapshot.rglob("*")))

    def test_index_snapshot_preserves_raw_blobs_without_executing_smudge_filters(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            relative = "space λ.txt"
            payload = b"exact indexed data\n"
            (root / relative).write_bytes(payload)
            (root / ".gitattributes").write_text("*.txt filter=fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            script = root / ".git/synthetic-smudge.py"
            marker = root / ".git/smudge-executed"
            script.write_text(
                "import pathlib, sys\n"
                "pathlib.Path(sys.argv[1]).write_text('filter executed')\n"
                "sys.stdin.buffer.read()\n"
                "print('rewritten data')\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "git",
                    "config",
                    "--local",
                    "filter.fixture.smudge",
                    shlex.join([sys.executable, str(script), str(marker)]),
                ],
                cwd=root,
                check=True,
            )
            snapshot = root / ".git/snapshot"
            snapshot.mkdir()

            engines._checkout_index(root, snapshot)

            self.assertFalse(marker.exists(), "a publication snapshot must not run Git filters")
            self.assertEqual(payload, (snapshot / relative).read_bytes())

    def test_staged_secret_policy_and_its_relative_files_come_from_the_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            policy = root / "security.toml"
            policy.write_text('[extend]\npath = "rules.toml"\n')
            (root / "rules.toml").write_text("# reviewed indexed rules\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            policy.write_text("# unrelated worktree policy\n")
            (root / "rules.toml").write_text("# unrelated worktree rules\n")

            def inspect(command, *, root: Path, **kwargs):
                selected = Path(command[command.index("--config") + 1])
                self.assertEqual('[extend]\npath = "rules.toml"\n', selected.read_text())
                self.assertEqual("# reviewed indexed rules\n", (root / "rules.toml").read_text())
                return 0

            with (
                patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
                patch.object(engines, "_run", side_effect=inspect),
            ):
                self.assertEqual(
                    0,
                    engines.betterleaks(
                        root,
                        config="security.toml",
                        history=False,
                        staged=True,
                        include_candidates=False,
                        allow_download=False,
                    ),
                )
            self.assertEqual("# unrelated worktree policy\n", policy.read_text())

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="engine command test ")
        self.addCleanup(temporary.cleanup)
        original = engines.storage.service_root
        storage_patch = patch.object(
            engines.storage,
            "service_root",
            side_effect=lambda root: (
                Path(temporary.name) / "local-storage" if root == Path("repo") else original(root)
            ),
        )
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

    def test_betterleaks_history_uses_the_repository_and_project_config(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "_run", return_value=0) as invoke,
        ):
            result = engines.betterleaks(
                Path("repo"),
                config=".betterleaks.toml",
                history=True,
                staged=False,
                include_candidates=False,
                allow_download=False,
            )

        self.assertEqual(0, result)
        command = list(invoke.call_args.args[0])
        self.assertIn("--redact", command)
        self.assertIn("--verbose", command)
        self.assertEqual("10", command[command.index("--exit-code") + 1])
        self.assertIn("git", command)
        self.assertIn(f"--log-opts={engines.HISTORY_LOG_OPTS}", command)
        self.assertNotIn("--pre-commit", command)
        environment = invoke.call_args.kwargs["environment"]
        self.assertIn("safe.directory", environment.values())
        self.assertEqual("1", environment["GIT_NO_REPLACE_OBJECTS"])

    def test_betterleaks_staged_scope_is_explicit(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "_checkout_index"),
            patch.object(engines, "_run", return_value=0) as invoke,
        ):
            engines.betterleaks(
                Path("repo"),
                config=".betterleaks.toml",
                history=False,
                staged=True,
                include_candidates=False,
                allow_download=False,
            )

        command = list(invoke.call_args.args[0])
        self.assertIn("--pre-commit", command)
        self.assertIn("--staged", command)
        self.assertNotIn(f"--log-opts={engines.HISTORY_LOG_OPTS}", command)

    def test_betterleaks_worktree_scope_uses_the_directory_engine(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "_materialize_worktree"),
            patch.object(engines, "_run", return_value=0) as invoke,
        ):
            engines.betterleaks(
                Path("repo"),
                config=".betterleaks.toml",
                history=False,
                staged=False,
                include_candidates=True,
                allow_download=False,
            )

        command = list(invoke.call_args.args[0])
        self.assertIn("dir", command)
        self.assertNotIn("git", command)
        self.assertNotIn(str(Path("repo")), command[command.index("dir") + 1 :])

    def test_betterleaks_worktree_snapshot_matches_the_publication_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_text("ignored.txt\n.cache/\n", encoding="utf-8")
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=root, check=True)
            (root / "candidate.txt").write_text("candidate\n", encoding="utf-8")
            (root / "ignored.txt").write_text("private\n", encoding="utf-8")
            cache = root / ".cache"
            cache.mkdir()
            (cache / "engine.bin").write_bytes(b"private")
            observed: set[str] = set()

            def inspect(command, **_kwargs):
                snapshot = Path(command[command.index("dir") + 1])
                observed.update(
                    path.relative_to(snapshot).as_posix()
                    for path in snapshot.rglob("*")
                    if path.is_file()
                )
                return 0

            with (
                patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
                patch.object(engines, "_run", side_effect=inspect),
            ):
                result = engines.betterleaks(
                    root,
                    config=".betterleaks.toml",
                    history=False,
                    staged=False,
                    include_candidates=True,
                    allow_download=False,
                )

        self.assertEqual(0, result)
        self.assertEqual({".gitignore", "candidate.txt", "tracked.txt"}, observed)

    def test_worktree_snapshot_keeps_sparse_index_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sparse.toml").write_text("secret from index\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "sparse.toml"], cwd=root, check=True)
            subprocess.run(
                ["git", "update-index", "--skip-worktree", "sparse.toml"],
                cwd=root,
                check=True,
            )
            (root / "sparse.toml").unlink()
            observed = ""

            def inspect(command, **_kwargs):
                nonlocal observed
                snapshot = Path(command[command.index("dir") + 1])
                observed = (snapshot / "sparse.toml").read_text(encoding="utf-8")
                return 0

            with (
                patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
                patch.object(engines, "_run", side_effect=inspect),
            ):
                result = engines.betterleaks(
                    root,
                    config=".betterleaks.toml",
                    history=False,
                    staged=False,
                    include_candidates=True,
                    allow_download=False,
                )

        self.assertEqual(0, result)
        self.assertEqual("secret from index\n", observed)

    def test_betterleaks_respects_a_tracked_only_worktree_boundary(self) -> None:
        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("betterleaks")),
            patch.object(engines, "_materialize_worktree") as materialize,
            patch.object(engines, "_run", return_value=0),
        ):
            engines.betterleaks(
                Path("repo"),
                config=".betterleaks.toml",
                history=False,
                staged=False,
                include_candidates=False,
                allow_download=False,
            )

        self.assertEqual(Path("repo"), materialize.call_args.args[0])
        self.assertFalse(materialize.call_args.kwargs["include_candidates"])

    def test_lychee_is_always_offline_and_reads_a_git_owned_file_list(self) -> None:
        observed = []

        def inspect(command, *, root, **kwargs):
            inputs = Path(command[command.index("--files-from") + 1])
            self.assertFalse(inputs.is_relative_to(root))
            self.assertEqual(root.parent, inputs.parent)
            self.assertEqual("./README.md\n", inputs.read_text(encoding="utf-8"))
            observed.extend(command)
            return 0

        with (
            patch.object(engines.toolchain, "resolve", return_value=Path("lychee")),
            patch.object(engines, "_markdown_paths", return_value=("README.md",)),
            patch.object(engines, "_materialize_worktree"),
            patch.object(engines, "_run", side_effect=inspect),
        ):
            engines.lychee(
                Path("repo"), staged=False, include_candidates=True, allow_download=False
            )

        self.assertIn("--offline", observed)

    def test_lychee_excludes_worktree_deletions_but_keeps_the_index_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("[local](missing.md)\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            (root / "README.md").unlink()

            self.assertEqual(
                (),
                engines._markdown_paths(root, include_candidates=True, staged=False),
            )
            self.assertEqual(
                ("README.md",),
                engines._markdown_paths(root, include_candidates=False, staged=True),
            )

    def _check_literal_lychee_inputs(self, filenames):
        # Exercise the upstream line/comment and glob grammar against real files.
        # This fixture only recognizes its local-link syntax; native pinned-engine
        # probes separately establish the upstream grammar and exit contract.
        for filename in filenames:
            for broken in (False, True):
                with (
                    self.subTest(filename=filename, broken=broken),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    subprocess.run(["git", "init", "-q", str(root)], check=True)
                    subject = root / filename
                    subject.parent.mkdir(parents=True, exist_ok=True)
                    for decoy in ("safe.md", "other.md", "space.md"):
                        (root / decoy).write_text("public example\n", encoding="utf-8")
                    for decoy in ("a.md", "questionZ.md"):
                        (subject.parent / decoy).write_text(
                            "[target](missing.md)\n", encoding="utf-8"
                        )
                    (subject.parent / "target.md").write_text("public target\n", encoding="utf-8")
                    subject.write_text(
                        "[target](" + ("missing.md" if broken else "target.md") + ")\n",
                        encoding="utf-8",
                    )
                    subprocess.run(["git", "add", "."], cwd=root, check=True)
                    for staged in (False, True):
                        observed = []

                        def inspect(command, *, root, observed=observed, **kwargs):
                            specifications = []
                            if "--files-from" in command:
                                inputs = Path(command[command.index("--files-from") + 1])
                                for line in inputs.read_text(encoding="utf-8").split("\n"):
                                    line = line.strip()
                                    if line and not line.startswith("#"):
                                        specifications.append(line)
                            if "--" in command:
                                specifications.extend(command[command.index("--") + 1 :])
                            code = 0
                            for specification in specifications:
                                for relative in glob(specification, root_dir=root):
                                    path = root / relative
                                    observed.append(path.relative_to(root).as_posix())
                                    content = path.read_text(encoding="utf-8")
                                    if content.startswith("[target]("):
                                        target = content.split("(", 1)[1].split(")", 1)[0]
                                        if not (path.parent / target).is_file():
                                            code = engines.LYCHEE_FINDINGS_EXIT
                            return code

                        with (
                            self.subTest(staged=staged),
                            patch.object(engines.toolchain, "resolve", return_value=Path("lychee")),
                            patch.object(engines, "_markdown_paths", return_value=(filename,)),
                            patch.object(engines, "_run", side_effect=inspect),
                        ):
                            code = engines.lychee(
                                root,
                                staged=staged,
                                include_candidates=True,
                                allow_download=False,
                            )
                            self.assertEqual(engines.LYCHEE_FINDINGS_EXIT if broken else 0, code)
                            self.assertEqual([filename], observed)

    def test_lychee_preserves_comment_whitespace_unicode_and_literal_glob_paths(self):
        self._check_literal_lychee_inputs(
            ("subject.md", "#subject.md", " space.md", "資料/space λ.md", "docs/[abc].md")
        )

    @unittest.skipIf(os.name == "nt", "Windows filenames cannot contain line breaks or * ?")
    def test_lychee_preserves_line_breaks_and_literal_wildcards(self):
        self._check_literal_lychee_inputs(
            (
                "safe.md\nother.md",
                "carriage\rreturn.md",
                "docs/*.md",
                "docs/question?.md",
                "folder\nwith[*]?]/subject.md",
            )
        )

    @unittest.skipIf(os.name == "nt", "Windows filenames cannot contain carriage returns")
    def test_lychee_receives_cr_and_lf_git_names_as_distinct_source_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            broken, clean = "carriage\rreturn.md", "carriage\nreturn.md"
            (root / broken).write_text("[target](missing.md)\n", encoding="utf-8")
            (root / clean).write_text("public content\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            for staged in (False, True):
                selected = []

                def inspect(command, *, root, selected=selected, **kwargs):
                    inputs = command[command.index("--") + 1 :] if "--" in command else []
                    selected.extend(str(Path(relative)) for relative in inputs)
                    return (
                        engines.LYCHEE_FINDINGS_EXIT
                        if any("missing.md" in (root / relative).read_text() for relative in inputs)
                        else 0
                    )

                with (
                    self.subTest(staged=staged),
                    patch.object(engines.toolchain, "resolve", return_value=Path("lychee")),
                    patch.object(engines, "_run", side_effect=inspect),
                ):
                    code = engines.lychee(
                        root, staged=staged, include_candidates=True, allow_download=False
                    )
                    self.assertEqual(engines.LYCHEE_FINDINGS_EXIT, code)
                    self.assertCountEqual([broken, clean], selected)

    def test_lychee_batches_only_line_break_paths_and_prioritizes_operational_errors(self):
        normal = tuple(f"normal-{index}.md" for index in range(1000))
        exceptional = tuple(f"split-{index}\nname.md" for index in range(12))
        for outcome in (0, engines.LYCHEE_FINDINGS_EXIT, 1):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                observed, runtimes, sizes = [], [], []

                def inspect(
                    command,
                    *,
                    root,
                    environment,
                    context=(observed, runtimes, sizes, outcome),
                    **kwargs,
                ):
                    observed, runtimes, sizes, outcome = context
                    runtime = Path(environment["TMPDIR"])
                    self.assertFalse(runtime.is_relative_to(root))
                    self.assertNotIn(runtime, runtimes)
                    runtimes.append(runtime)
                    (runtime / "diagnostic.txt").write_text("synthetic scanner diagnostics")
                    if "--files-from" in command:
                        self.assertEqual(1, len(runtimes), "ordinary paths run only once")
                        inputs = Path(command[command.index("--files-from") + 1])
                        observed.extend(inputs.read_text(encoding="utf-8").splitlines())
                    arguments = command[command.index("--") + 1 :] if "--" in command else []
                    sizes.append(sum(len(os.fsencode(argument)) for argument in arguments))
                    observed.extend(arguments)
                    if outcome == 1:
                        return engines.LYCHEE_FINDINGS_EXIT if len(runtimes) == 1 else 1
                    return outcome if len(runtimes) == 1 else 0

                with (
                    patch.object(engines.toolchain, "resolve", return_value=Path("lychee")),
                    patch.object(engines, "_markdown_paths", return_value=normal + exceptional),
                    patch.object(engines, "_materialize_worktree"),
                    patch.object(engines, "LYCHEE_ARGUMENT_BYTES", 256, create=True),
                    patch.object(engines, "_run", side_effect=inspect),
                ):
                    code = engines.lychee(
                        root, staged=False, include_candidates=True, allow_download=False
                    )
                self.assertEqual(outcome, code)
                self.assertGreater(len(runtimes), 1)
                self.assertLessEqual(max(sizes), 256)
                self.assertEqual(["./" + path for path in normal], observed[: len(normal)])
                if outcome == 1:
                    self.assertEqual(2, len(runtimes), "an operational failure stops later batches")
                    self.assertTrue(all((path / "diagnostic.txt").is_file() for path in runtimes))
                else:
                    self.assertEqual(["./" + path for path in exceptional], observed[len(normal) :])
                    self.assertTrue(all(not path.parent.exists() for path in runtimes))

    def test_lychee_batches_share_a_deadline_and_preserve_unconfirmed_cleanup(self):
        for failure in ("deadline", "unconfirmed"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                clock, runtimes, timeouts = [100.0], [], []

                def materialize(root, destination, **kwargs):
                    (destination / "source.txt").write_text("owned source bytes")

                def native(command, context=(clock, runtimes, timeouts, failure), **kwargs):
                    clock, runtimes, timeouts, failure = context
                    runtime = Path(kwargs["env"]["TMPDIR"])
                    runtimes.append(runtime)
                    timeouts.append(kwargs["timeout"])
                    (runtime / "diagnostic.txt").write_text("owned diagnostic")
                    if failure == "unconfirmed" and len(runtimes) == 2:
                        raise engines.processes.CleanupError("child cleanup is unconfirmed")
                    clock[0] += 6 if failure == "deadline" else 1
                    return subprocess.CompletedProcess(command, 0)

                error = RuntimeError if failure == "deadline" else engines.processes.CleanupError
                message = "timed out" if failure == "deadline" else "unconfirmed"
                with (
                    patch.object(engines.toolchain, "resolve", return_value=Path("lychee")),
                    patch.object(
                        engines,
                        "_markdown_paths",
                        return_value=("README.md", "first\nsplit.md", "second\nsplit.md"),
                    ),
                    patch.object(engines, "_materialize_worktree", side_effect=materialize),
                    patch.object(engines, "LYCHEE_ARGUMENT_BYTES", 96),
                    patch.object(engines, "ENGINE_TIMEOUT_SECONDS", 5),
                    patch.object(
                        engines.time, "monotonic", side_effect=lambda clock=clock: clock[0]
                    ),
                    patch.object(engines.processes, "run", side_effect=native),
                    self.assertRaisesRegex(error, message),
                ):
                    engines.lychee(
                        root, staged=False, include_candidates=True, allow_download=False
                    )
                self.assertEqual([5.0] if failure == "deadline" else [5.0, 4.0], timeouts)
                self.assertTrue(all((path / "diagnostic.txt").is_file() for path in runtimes))
                workspace = runtimes[0].parent
                self.assertEqual(
                    failure == "unconfirmed", (workspace / "source/source.txt").exists()
                )
                self.assertEqual(
                    failure == "unconfirmed", (workspace / "markdown-inputs.txt").exists()
                )

    def test_index_snapshot_does_not_follow_a_tracked_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "core.symlinks", "true"], cwd=root, check=True)
            target = root / ".git/private.txt"
            target.write_text("must not be read")
            link = root / "link.md"
            try:
                os.symlink(target, link)
            except OSError:
                self.skipTest("symlinks unavailable")
            subprocess.run(["git", "add", "link.md"], cwd=root, check=True)
            with engines.storage.temporary(root, "test-index-") as workspace:
                engines._checkout_index(root, workspace.path)
                copied = workspace.path / "link.md"
                self.assertFalse(copied.is_symlink())
                expected = subprocess.run(
                    ["git", "cat-file", "blob", ":link.md"],
                    cwd=root,
                    capture_output=True,
                    check=True,
                ).stdout
                self.assertEqual(expected, copied.read_bytes())
                workspace.remember()
            self.assertEqual("must not be read", target.read_text())
