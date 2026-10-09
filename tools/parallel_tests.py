"""Run the declared unittest suite across processes instead of one at a time.

The suite is dominated by integration tests that each build a real Git repository
and run real commands: 106 of 529 tests hold 94% of the wall time, and none of them
share a port, a working directory or a temp path. That is a suite waiting to be
spread over the machine, so this runner spreads it and changes nothing else. Every
test still runs, in its own process, and the verdict is the same one unittest would
print. `--jobs 1` keeps the sequential behaviour for comparing the two.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Some suite modules import their siblings by module name, so the suite directory
# belongs on the path next to `src`, exactly as sequential discovery arranges it.
IMPORT_PATHS = (str(ROOT / "src"), str(ROOT / "tests"))
# Past this point the disk, not the CPU, is the limit: these tests spend their time
# in Git and child processes. A cap also keeps memory and handle use predictable.
MAX_WORKERS = 16


def _writer_record(descriptor, expected=None):
    import fcntl

    observed = os.fstat(descriptor)
    identity = (observed.st_dev, observed.st_ino)
    if (
        descriptor < 3
        or not stat.S_ISFIFO(observed.st_mode)
        or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_WRONLY
        or (expected is not None and identity != expected)
    ):
        raise RuntimeError("source-test lifetime writer changed during transfer")
    return [descriptor, *identity]


class _InheritedWriter:
    """Give each spawned worker its own transfer of the same lifetime writer."""

    def __init__(self, descriptor):
        self.record = _writer_record(descriptor)

    def __reduce__(self):
        from multiprocessing.reduction import DupFd

        # DupFd is consumed once. Construct it during each worker's serialization,
        # rather than sharing a precomputed transfer between workers.
        return _restore_writer, (DupFd(self.record[0]), tuple(self.record[1:]))


def _restore_writer(transfer, expected):
    descriptor = transfer.detach()
    try:
        _writer_record(descriptor, expected)
        return _InheritedWriter(descriptor)
    except BaseException:
        os.close(descriptor)
        raise


def _inherit_writers(writers):
    _extend(IMPORT_PATHS)
    from releasekit import processes

    records = []
    for writer in writers:
        record = _writer_record(writer.record[0], tuple(writer.record[1:]))
        os.set_inheritable(record[0], True)
        records.append(record)
    # Spawn and forkserver renumber descriptors. Publish the child's verified
    # numbers so its managed commands preserve every ancestor's lifetime witness.
    os.environ[processes._LIFETIME_ENV] = json.dumps(records, separators=(",", ":"))


def _extend(paths: tuple[str, ...]) -> None:
    for entry in paths:
        if entry not in sys.path:
            sys.path.insert(0, entry)


def identifiers(start: Path, paths: tuple[str, ...]) -> list[str]:
    """Every test id discovery finds, including the failures discovery itself records."""
    _extend(paths)
    found: list[str] = []
    pending = [unittest.defaultTestLoader.discover(str(start), top_level_dir=str(start))]
    while pending:
        item = pending.pop()
        if isinstance(item, unittest.TestSuite):
            pending.extend(item)
        else:
            found.append(item.id())
    return sorted(found)


def _execute(work: tuple[str, tuple[str, ...]]) -> tuple[str, float, list, list, list]:
    identifier, paths = work
    _extend(paths)
    result = unittest.TestResult()
    started = time.monotonic()
    try:
        unittest.defaultTestLoader.loadTestsFromName(identifier).run(result)
    except Exception as error:  # noqa: BLE001 - a load failure is a test failure here
        return identifier, time.monotonic() - started, [], [f"{identifier}: {error!r}"], []
    return (
        identifier,
        time.monotonic() - started,
        [text for _, text in result.failures]
        + [f"unexpected success: {test.id()}" for test in result.unexpectedSuccesses],
        [text for _, text in result.errors],
        [reason for _, reason in result.skipped],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=0, help="0 selects a value for this host")
    parser.add_argument("--start-dir", default=str(ROOT / "tests"))
    arguments = parser.parse_args(argv)

    start = Path(arguments.start_dir).resolve()
    paths = tuple(dict.fromkeys((*IMPORT_PATHS, str(start))))
    # Tests launch child interpreters that import `releasekit`, so the import path has
    # to reach them through the environment as well, not only this process's sys.path.
    # Without it the runner would only work when something else exported PYTHONPATH.
    os.environ["PYTHONPATH"] = str(ROOT / "src")
    names = identifiers(start, paths)
    if not names:
        print(f"tests: no tests discovered in {start}", flush=True)
        return 2
    jobs = arguments.jobs or min(os.cpu_count() or 1, MAX_WORKERS)
    jobs = max(1, min(jobs, len(names) or 1))

    print(f"tests: {len(names)} across {jobs} process(es)", flush=True)
    failures: list[str] = []
    errors: list[str] = []
    skipped = 0
    started = time.monotonic()
    work = [(name, paths) for name in names]
    # A row of dots says the suite is alive but not how much of it is left. On a
    # terminal the count replaces itself in place; piped output keeps the plain dots,
    # so a captured gate log reads exactly as it did before.
    live = sys.stdout.isatty()
    pool = lifetime = None
    try:
        if jobs == 1:
            outcomes = map(_execute, work)
        else:
            options = {}
            if os.name != "nt":
                from releasekit import processes

                lifetime = processes.LifetimePipe(None)
                options = {
                    "initializer": _inherit_writers,
                    "initargs": (tuple(_InheritedWriter(fd) for fd in lifetime.descriptors),),
                }
            pool = ProcessPoolExecutor(max_workers=jobs, **options)
            outcomes = pool.map(_execute, work)
        for done, (_, _, test_failures, test_errors, test_skips) in enumerate(outcomes, start=1):
            failures.extend(test_failures)
            errors.extend(test_errors)
            skipped += len(test_skips)
            mark = "F" if test_failures else ("E" if test_errors else ".")
            if live:
                sys.stdout.write(f"\r[{done}/{len(names)}] {len(failures) + len(errors)} failing ")
            else:
                sys.stdout.write(mark)
            sys.stdout.flush()
    finally:
        try:
            if pool is not None:
                pool.shutdown()
        finally:
            if lifetime is not None:
                try:
                    # Keep the parent writer until shutdown: spawn can be lazy.
                    lifetime.spawned()
                    if not lifetime.finished():
                        raise processes.CleanupError(
                            "source-test child cleanup is unconfirmed; retain check state"
                        )
                finally:
                    lifetime.close()
    total = time.monotonic() - started

    print(f"\n{'-' * 70}\nRan {len(names)} tests in {total:.3f}s\n", flush=True)
    for report in (*failures, *errors):
        print(report, flush=True)
    if failures or errors:
        detail = ", ".join(
            part
            for part in (
                f"failures={len(failures)}" if failures else "",
                f"errors={len(errors)}" if errors else "",
            )
            if part
        )
        print(f"FAILED ({detail})", flush=True)
        return 1
    if skipped == len(names):
        print("tests: all discovered tests were skipped; source was not qualified", flush=True)
        return 2
    print(f"OK{f' (skipped={skipped})' if skipped else ''}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
