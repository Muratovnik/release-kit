"""Bounded synchronous execution of trusted commands with owned descendant cleanup.

POSIX uses a process group; Windows uses a native Job Object and a startup barrier.
Nested release-kit runners relay SIGTERM through their own cleanup before unwinding.
Deliberately detached POSIX children and uncatchable host termination are not a sandbox
contract. An unresolved cleanup must retain the caller's state lock for manual recovery.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

# A Windows command cannot start until its wrapper has joined the job. Native
# arguments remain separate; this is neither shell evaluation nor a JSON protocol.
_WINDOWS_START = """
import subprocess, sys
if sys.stdin.buffer.read(1) != b'1':
    raise SystemExit(2)
raise SystemExit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL))
"""
_GRACE = 5.0


class TerminationRequested(KeyboardInterrupt):
    """A parent requested termination; stop nested workers without another grace wait."""


class CleanupError(RuntimeError):
    """Owned process termination could not be established; do not release its lock."""


@contextmanager
def termination_handler():
    """Let a coordinator's SIGTERM unwind a nested runner before its lock is released."""
    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)

    def interrupted(signum, frame):
        raise TerminationRequested("command termination requested")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def _stop(process, job, *, graceful=True) -> None:
    """Stop owned descendants before allowing caller cleanup or lock release."""
    try:
        if job is not None:
            job.stop()
        else:
            # A nested runner gets time to stop its own process groups and persist
            # its report. The final kill also covers siblings after the leader exits.
            if graceful:
                _group(process.pid, signal.SIGTERM)
                deadline = time.monotonic() + _GRACE
                while time.monotonic() < deadline:
                    process.poll()  # Reap the leader, without mistaking it for the whole group.
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.01)
            _group(process.pid, signal.SIGKILL)
        # Assignment can fail while the Windows startup wrapper is still blocked.
        # Stop that direct child as well, even if the job never acquired it.
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        process.wait(timeout=_GRACE)
        if job is None:
            deadline = time.monotonic() + _GRACE
            while True:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("owned process group did not disappear after termination")
                time.sleep(0.01)
    except (OSError, subprocess.TimeoutExpired, TimeoutError, KeyboardInterrupt) as error:
        raise CleanupError("owned command cleanup is unconfirmed; retain its state lock") from error


def run(args, *, timeout: float, cwd=None, env=None, stdout=None, stderr=None, check=False):
    """Run noninteractive argv and return the standard CompletedProcess byte result.

    Timeout and interruption leave no ordinary child in the owned group/job before
    returning. The caller decides how to report remote outcomes and retain diagnostics.
    This function does not install dependencies, choose state paths or own reports.
    """
    if timeout <= 0:
        raise ValueError("command timeout must be positive")
    process = job = None
    with termination_handler():
        try:
            command = list(args)
            options = {"start_new_session": True}
            stdin = subprocess.DEVNULL
            if os.name == "nt":
                from ._winjob import Job

                job = Job()
                command = [sys.executable, "-I", "-B", "-c", _WINDOWS_START, *command]
                stdin = subprocess.PIPE
                options = {"creationflags": subprocess.CREATE_NO_WINDOW}
            process = subprocess.Popen(
                command, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr, **options
            )
            if job is not None:
                job.assign(process.pid)
                process.stdin.write(b"1")
                process.stdin.close()
                process.stdin = None
            try:
                output, errors = process.communicate(timeout=timeout)
            except BaseException as error:
                _stop(process, job, graceful=not isinstance(error, TerminationRequested))
                raise
            # A command that exits successfully must not leave background workers
            # using its fixture or SDK while the caller releases that state.
            _stop(process, job)
            result = subprocess.CompletedProcess(args, process.returncode, output, errors)
            if check:
                result.check_returncode()
            return result
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        _stop(process, job, graceful=False)
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
            finally:
                if job is not None:
                    job.close()
