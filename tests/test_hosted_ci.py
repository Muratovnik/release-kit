from __future__ import annotations

import unittest

from releasekit.exposure import hosted_ci

GUARD = "${{ github.event.repository && !github.event.repository.private }}"


def _workflow(job: str) -> str:
    return f"name: check\non:\n  push:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n{job}"


class GuardTests(unittest.TestCase):
    def test_a_job_with_the_guard_is_skipped_rather_than_refused(self) -> None:
        for condition in (
            GUARD,
            "github.event.repository && !github.event.repository.private",
            f"'{GUARD}'",
            f'"{GUARD}"',
            (
                "${{ github.event_name == 'push' && (github.event.repository && "
                "!github.event.repository.private) }}"
            ),
            "${{ !github.event.repository.private && github.event.repository }}  # keep",
        ):
            with self.subTest(condition=condition):
                text = _workflow(f"  check:\n    if: {condition}\n    runs-on: ubuntu-latest\n")
                self.assertEqual([], hosted_ci.unguarded_jobs(text))

    def test_a_folded_condition_is_read_across_its_lines(self) -> None:
        text = _workflow(
            "  check:\n    if: >-\n      ${{ github.event.repository &&\n"
            "      !github.event.repository.private }}\n    runs-on: ubuntu-latest\n"
        )

        self.assertEqual([], hosted_ci.unguarded_jobs(text))

    def test_the_bare_private_check_still_starts_a_scheduled_run(self) -> None:
        # A scheduled event carries no repository, and !null is true, so this job is
        # requested on every schedule and refused by a host that runs no private jobs.
        for condition in (
            "${{ !github.event.repository.private }}",
            "${{ github.event.repository.private == false }}",
        ):
            with self.subTest(condition=condition):
                text = _workflow(f"  check:\n    if: {condition}\n    runs-on: ubuntu-latest\n")
                self.assertEqual(
                    ["job 'check' can start in a private repository"],
                    hosted_ci.unguarded_jobs(text),
                )

    def test_an_alternative_that_can_bypass_the_guard_is_not_a_guard(self) -> None:
        text = _workflow(f"  check:\n    if: {GUARD[:-3]} || always() }}}}\n    runs-on: x\n")

        self.assertEqual(
            ["job 'check' can start in a private repository"], hosted_ci.unguarded_jobs(text)
        )

    def test_a_guard_on_a_step_does_not_keep_its_job_from_being_requested(self) -> None:
        text = _workflow(
            "  check:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"    - if: {GUARD}\n      run: echo hi\n"
        )

        self.assertEqual(
            ["job 'check' can start in a private repository"], hosted_ci.unguarded_jobs(text)
        )

    def test_every_job_is_judged_on_its_own(self) -> None:
        text = _workflow(
            f"  first:\n    if: {GUARD}\n    runs-on: x\n"
            "  # a comment between jobs\n\n"
            "  second:\n    needs: first\n    runs-on: x\n"
            f'  "third":\n    uses: ./.github/workflows/reusable.yml\n    if: {GUARD}\n'
        )

        self.assertEqual(
            ["job 'second' can start in a private repository"], hosted_ci.unguarded_jobs(text)
        )

    def test_a_layout_the_reader_does_not_understand_fails_instead_of_passing(self) -> None:
        cases = {
            "jobs: {check: {runs-on: x}}\n": ["jobs cannot be read as a block mapping"],
            _workflow("  check: {runs-on: x}\n"): ["job 'check' cannot be read as a block mapping"],
            _workflow("  check: *shared\n"): ["job 'check' cannot be read as a block mapping"],
            _workflow("  check:\n    <<: *shared\n    runs-on: x\n"): [
                "job 'check' takes its keys from a merge and cannot be verified"
            ],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, hosted_ci.unguarded_jobs(text))

    def test_a_file_without_jobs_has_nothing_to_guard(self) -> None:
        self.assertEqual([], hosted_ci.unguarded_jobs("name: empty\non: push\n"))

    def test_only_files_the_host_loads_are_workflows(self) -> None:
        self.assertTrue(hosted_ci.is_workflow(".github/workflows/ci.yml"))
        self.assertTrue(hosted_ci.is_workflow(".github/workflows/Release.YAML"))
        self.assertFalse(hosted_ci.is_workflow(".github/workflows/nested/ci.yml"))
        self.assertFalse(hosted_ci.is_workflow(".github/workflows/README.md"))
        self.assertFalse(hosted_ci.is_workflow("ci.yml"))


if __name__ == "__main__":
    unittest.main()
