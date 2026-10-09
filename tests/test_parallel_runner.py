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
