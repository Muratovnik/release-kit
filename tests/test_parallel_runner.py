"""The parallel runner must find and judge exactly what sequential discovery does."""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Import it as a module rather than through runpy: the runner ships work to worker
# processes, and a module loaded under a synthetic name cannot be pickled to them.
sys.path.insert(0, str(ROOT / "tools"))
import parallel_tests

SUITE = """
import unittest


class SampleTests(unittest.TestCase):
    def test_passes(self):
        self.assertTrue(True)

    def test_fails(self):
        self.assertEqual(1, 2, "deliberate")

    def test_errors(self):
        raise RuntimeError("deliberate")

    @unittest.skip("deliberate")
    def test_skipped(self):
        pass
"""


def quiet(argv):
    """Run the runner without pasting its report into the gate's own output."""
    with contextlib.redirect_stdout(io.StringIO()):
        return parallel_tests.main(argv)


class ParallelRunnerTests(unittest.TestCase):
    def test_empty_or_all_skipped_collection_does_not_qualify_source(self):
        for skipped in (False, True):
            with tempfile.TemporaryDirectory(prefix="runner no execution ") as temporary:
                folder = Path(temporary)
                if skipped:
                    (folder / "test_all_skipped.py").write_text(
                        "import unittest\n"
                        "@unittest.skip('unsupported fixture')\n"
                        "class SkippedTests(unittest.TestCase):\n"
                        "    def test_skipped(self): pass\n"
                        "    def test_also_skipped(self): pass\n",
                        encoding="utf-8",
                    )
                for jobs in (1, 2):
                    with self.subTest(skipped=skipped, jobs=jobs):
                        self.assertEqual(
                            2, quiet(["--jobs", str(jobs), "--start-dir", str(folder)])
                        )

    def test_expected_failure_and_unexpected_success_preserve_unittest_verdict(self):
        for passes, expected in ((False, 0), (True, 1)):
            with tempfile.TemporaryDirectory(prefix="runner expected failure ") as temporary:
                folder = Path(temporary)
                (folder / f"test_expectation_{passes}.py").write_text(
                    "import unittest\n"
                    "class ExpectationTests(unittest.TestCase):\n"
                    "    @unittest.expectedFailure\n"
                    "    def test_known_failure(self):\n"
                    f"        self.assertTrue({passes!r})\n"
                    "    def test_ordinary_success(self):\n"
                    "        self.assertTrue(True)\n",
                    encoding="utf-8",
                )
                for jobs in (1, 2):
                    with self.subTest(passes=passes, jobs=jobs):
                        self.assertEqual(
                            expected, quiet(["--jobs", str(jobs), "--start-dir", str(folder)])
                        )

    def test_completed_cases_define_qualification_despite_subtest_skips(self):
        skipped = (
            "    def test_skips(self):\n"
            "        for case in ('first', 'second'):\n"
            "            with self.subTest(case=case):\n"
            "                self.skipTest('controlled unsupported case')\n"
        )
        passed = "    def test_passes(self): self.assertEqual(4, 2 + 2)\n"
        cases = (
            ("all_subtests", skipped + "    test_also_skips = test_skips\n", 2),
            (
                "mixed_subtests",
                (
                    "    def test_cases(self):\n"
                    "        with self.subTest(case='unsupported'):\n"
                    "            self.skipTest('controlled unsupported case')\n"
                    "        with self.subTest(case='executed'):\n"
                    "            self.assertEqual(4, 2 + 2)\n"
                )
                + skipped,
                0,
            ),
            ("mixed_methods", skipped + passed, 0),
            (
                "expected_failure_only",
                (
                    "    @unittest.expectedFailure\n"
                    "    def test_known_failure(self): self.assertEqual(5, 2 + 2)\n"
                )
                + skipped,
                0,
            ),
            (
                "failure_and_skips",
                skipped + "    def test_fails(self): self.assertEqual(5, 2 + 2)\n",
                1,
            ),
            (
                "class_setup_skip",
                "    @classmethod\n"
                "    def setUpClass(cls): raise unittest.SkipTest('unsupported class')\n"
                + passed
                + skipped,
                2,
            ),
            (
                "module_setup_skip",
                passed
                + skipped
                + "\ndef setUpModule(): raise unittest.SkipTest('unsupported module')\n",
                2,
            ),
        )
        for name, source, expected in cases:
            with tempfile.TemporaryDirectory(prefix="runner subtests ") as temporary:
                folder = Path(temporary)
                (folder / f"test_completion_{name}.py").write_text(
                    "import unittest\nclass SampleTests(unittest.TestCase):\n" + source,
                    encoding="utf-8",
                )
                for jobs in (1, 2):
                    with self.subTest(case=name, jobs=jobs):
                        self.assertEqual(
                            expected, quiet(["--jobs", str(jobs), "--start-dir", str(folder)])
                        )

    def test_skipped_subtest_ids_and_reasons_reach_the_captured_log(self):
        with tempfile.TemporaryDirectory(prefix="runner skip report ") as temporary:
            folder = Path(temporary)
            (folder / "test_skip_report.py").write_text(
                "import unittest\n"
                "class SampleTests(unittest.TestCase):\n"
                "    def test_cases(self):\n"
                "        with self.subTest(feature='unavailable'):\n"
                "            self.skipTest('no native fixture capability')\n"
                "        with self.subTest(feature='available'):\n"
                "            self.assertEqual(4, 2 + 2)\n"
                "    def test_ordinary(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
            for jobs in (1, 2):
                output = io.StringIO()
                with self.subTest(jobs=jobs), contextlib.redirect_stdout(output):
                    self.assertEqual(
                        0,
                        parallel_tests.main(["--jobs", str(jobs), "--start-dir", str(folder)]),
                    )
                self.assertIn(
                    "SKIP test_skip_report.SampleTests.test_cases (feature='unavailable'): "
                    "no native fixture capability",
                    output.getvalue(),
                )
                self.assertNotIn("(feature='available')", output.getvalue())

    def test_failed_subtest_identity_survives_execution_and_both_runner_modes(self):
        with tempfile.TemporaryDirectory(prefix="runner failure context ") as temporary:
            folder = Path(temporary)
            (folder / "test_failure_context.py").write_text(
                "import unittest\n"
                "class ContextTests(unittest.TestCase):\n"
                "    def test_failures(self):\n"
                "        for wrapper in ('onboarding', 'wheel'):\n"
                "            with self.subTest(wrapper=wrapper):\n"
                "                self.fail('same failure at the same source line')\n"
                "    def test_errors(self):\n"
                "        for wrapper in ('onboarding', 'wheel'):\n"
                "            with self.subTest(wrapper=wrapper):\n"
                "                raise RuntimeError('same error at the same source line')\n"
                "    @unittest.expectedFailure\n"
                "    def test_unexpected(self): pass\n",
                encoding="utf-8",
            )
            paths = (*parallel_tests.IMPORT_PATHS, str(folder))
            prefixes = []
            for method, label, failure_count, error_count in (
                ("test_failures", "FAIL", 2, 0),
                ("test_errors", "ERROR", 0, 2),
            ):
                identifier = f"test_failure_context.ContextTests.{method}"
                outcome = parallel_tests._execute((identifier, paths))
                self.assertEqual(identifier, outcome[0])
                self.assertEqual(failure_count, len(outcome[2]))
                self.assertEqual(error_count, len(outcome[3]))
                self.assertEqual([], outcome[4])
                self.assertFalse(outcome[5])
                for wrapper, report in zip(("onboarding", "wheel"), outcome[2] + outcome[3]):
                    prefix = f"{label}: {identifier} (wrapper={wrapper!r})"
                    prefixes.append(prefix)
                    with self.subTest(method=method, wrapper=wrapper, boundary="execute"):
                        self.assertTrue(report.startswith(prefix + "\nTraceback"), report)
            unexpected = "test_failure_context.ContextTests.test_unexpected"
            outcome = parallel_tests._execute((unexpected, paths))
            self.assertEqual([f"unexpected success: {unexpected}"], outcome[2])
            self.assertEqual([], outcome[3])
            self.assertEqual([], outcome[4])
            self.assertFalse(outcome[5])
            for jobs in (1, 2):
                output = io.StringIO()
                with self.subTest(jobs=jobs), contextlib.redirect_stdout(output):
                    self.assertEqual(
                        1,
                        parallel_tests.main(["--jobs", str(jobs), "--start-dir", str(folder)]),
                    )
                text = output.getvalue()
                self.assertIn("Ran 3 tests in ", text)
                self.assertIn("FAILED (failures=3, errors=2)", text)
                self.assertIn(f"unexpected success: {unexpected}", text)
                for prefix in prefixes:
                    with self.subTest(jobs=jobs, diagnostic=prefix):
                        self.assertIn(prefix + "\nTraceback", text)

    def test_discovery_matches_stdlib_unittest(self):
        start = ROOT / "tests"
        paths = (*parallel_tests.IMPORT_PATHS, str(start))
        found = parallel_tests.identifiers(start, paths)
        expected = []
        pending = [unittest.defaultTestLoader.discover(str(start), top_level_dir=str(start))]
        while pending:
            item = pending.pop()
            if isinstance(item, unittest.TestSuite):
                pending.extend(item)
            else:
                expected.append(item.id())
        # A runner that silently drops a test would still print a green verdict, which
        # is the one failure mode that makes the whole gate worthless.
        self.assertEqual(sorted(expected), found)
        self.assertTrue(found)

    def test_failures_errors_and_skips_survive_the_process_boundary(self):
        with tempfile.TemporaryDirectory(prefix="runner suite ") as temporary:
            folder = Path(temporary)
            (folder / "test_sample.py").write_text(SUITE, encoding="utf-8")
            for jobs in (1, 2):
                with self.subTest(jobs=jobs):
                    code = quiet(["--jobs", str(jobs), "--start-dir", str(folder)])
                    self.assertEqual(1, code)

    def test_a_clean_suite_reports_success_in_both_modes(self):
        with tempfile.TemporaryDirectory(prefix="runner clean ") as temporary:
            folder = Path(temporary)
            (folder / "test_clean.py").write_text(
                "import unittest\n\n\n"
                "class CleanTests(unittest.TestCase):\n"
                "    def test_passes(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            for jobs in (1, 2):
                with self.subTest(jobs=jobs):
                    self.assertEqual(0, quiet(["--jobs", str(jobs), "--start-dir", str(folder)]))

    def test_worker_count_stays_within_the_declared_cap(self):
        self.assertLessEqual(parallel_tests.MAX_WORKERS, 16)
        self.assertGreaterEqual(parallel_tests.MAX_WORKERS, 1)


if __name__ == "__main__":
    unittest.main()
