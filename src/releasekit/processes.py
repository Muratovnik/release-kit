"""Bounded synchronous execution of trusted commands with owned descendant cleanup.

POSIX uses a process group; Windows uses a native Job Object and a startup barrier.
Nested release-kit runners relay SIGTERM through their own cleanup before unwinding.
Deliberately detached POSIX children and uncatchable host termination are not a sandbox
contract. An unresolved cleanup must retain the caller's state lock for manual recovery.
"""

from __future__ import annotations

import json
import os
import signal
import stat
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
_LIFETIME_ENV = "_RELKIT_PROCESS_WRITERS"


class CleanupError(RuntimeError):
    """Owned process termination could not be established; do not release its lock."""


class LifetimePipe:
    """Observe cooperative POSIX descendants even after an intermediate runner exits.

    No messages are written. EOF means every inherited writer has closed. Each
    managed child receives its ancestors' writers as well as this invocation's;
    a surviving nested group therefore cannot disappear behind its parent's exit.
    Unmanaged wrappers that close descriptors do not preserve this witness.
    """

    def __init__(self, environment):
        import fcntl

        inherited = []
        try:
            records = json.loads(os.environ.get(_LIFETIME_ENV, "[]"))
        except (TypeError, ValueError):
            records = []
        # A raw subprocess may preserve the environment but close descriptors.
        # Ignore stale entries; never inherit a reused fd merely by its number.
        for record in records if isinstance(records, list) else ():
            if not isinstance(record, list) or len(record) != 3:
                continue
            if not all(type(value) is int for value in record) or record[0] < 3:
                continue
            fd, device, inode = record
            try:
                observed = os.fstat(fd)
                writable = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY
                if (
                    stat.S_ISFIFO(observed.st_mode)
                    and (observed.st_dev, observed.st_ino) == (device, inode)
                    and writable
                    and os.get_inheritable(fd)
                    and record not in inherited
                ):
                    inherited.append(record)
            except OSError:
                continue
        self.reader, self.writer = os.pipe()
        try:
            os.set_blocking(self.reader, False)
            observed = os.fstat(self.writer)
            inherited.append([self.writer, observed.st_dev, observed.st_ino])
            self.descriptors = tuple(record[0] for record in inherited)
            self.environment = dict(os.environ if environment is None else environment)
            self.environment[_LIFETIME_ENV] = json.dumps(inherited, separators=(",", ":"))
        except BaseException:
            self.close()
            raise

    def spawned(self):
        if self.writer is not None:
            os.close(self.writer)
            self.writer = None

    def finished(self):
        try:
            payload = os.read(self.reader, 1)
        except BlockingIOError:
            return False
        if payload:
            raise CleanupError("owned command lifetime pipe was modified; retain its state lock")
        return True

    def close(self):
        self.spawned()
        if self.reader is not None:
            os.close(self.reader)
            self.reader = None


@contextmanager
def termination_handler():
    """Let a coordinator's SIGTERM unwind a nested runner before its lock is released."""
    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)

    def interrupted(signum, frame):
        # CPython preserves SIGINT status for KeyboardInterrupt itself. A subclass
        # exits with generic status 1, indistinguishable from failed nested cleanup.
        raise KeyboardInterrupt("command termination requested")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _group(pid: int, signum: int) -> bool:
    try:
        os.killpg(pid, signum)
        return True
    except ProcessLookupError:
        return False


def cancellation_exit_confirmed(returncode: int | None) -> bool:
    """Recognize cooperative POSIX cancellation, not an unexplained relay failure.

    The CLI reports interruption as 3; developer tools use 130. Uncaught Python
    KeyboardInterrupt and the default SIGTERM handler retain their signal status.
    Other failures can carry an inner CleanupError even after this group disappears.
    Trusted commands must not conceal unresolved child cleanup behind a success or
    interruption status; detached children are outside the process-group contract.
    """
    return returncode in (0, 3, 130, -signal.SIGINT, -signal.SIGTERM)


def _stop(process, job, lifetime=None) -> None:
    """Stop owned descendants before allowing caller cleanup or lock release."""
    try:
        was_running = process.poll() is None
        forced = False
        if job is not None:
            job.stop()
        else:
            # A nested runner gets time to stop its own process groups and persist
            # its report, including when we ourselves received SIGTERM. Killing a
            # relay immediately can orphan its separately owned nested groups.
            _group(process.pid, signal.SIGTERM)
            deadline = time.monotonic() + _GRACE
            while True:
                process.poll()  # Reap the leader without mistaking it for the whole group.
                if not _group(process.pid, 0):
                    break
                if time.monotonic() >= deadline:
                    forced = True
                    _group(process.pid, signal.SIGKILL)
                    break
                time.sleep(0.01)
        # Assignment can fail while the Windows startup wrapper is still blocked.
        # Stop that direct child as well, even if the job never acquired it.
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            else:
                forced = job is None or forced
        process.wait(timeout=_GRACE)
        if job is None:
            deadline = time.monotonic() + _GRACE
            while True:
                if not _group(process.pid, 0):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("owned process group remains active")
                time.sleep(0.01)
            # No relay remains in this group to acknowledge a separately owned
            # descendant. Report that uncertainty now, before outer grace periods
            # can expire and kill the caller that must preserve its recovery state.
            if lifetime is not None and not lifetime.finished():
                raise CleanupError("owned nested command remains active; retain its state lock")
            # SIGKILL cannot acknowledge cleanup of a relay's nested groups. An
            # abnormal cooperative exit likewise cannot turn an inner unknown into
            # a confirmed outer timeout just because the immediate group vanished.
            if forced or (was_running and not cancellation_exit_confirmed(process.returncode)):
                raise CleanupError(
                    "owned command cleanup is unconfirmed after forced or abnormal termination; "
                    "retain its state lock"
                )
    except (OSError, subprocess.TimeoutExpired, TimeoutError, KeyboardInterrupt) as error:
        raise CleanupError("owned command cleanup is unconfirmed; retain its state lock") from error


def run(args, *, timeout: float, cwd=None, env=None, stdout=None, stderr=None, check=False):
    """Run noninteractive argv and return the standard CompletedProcess byte result.

    Timeout and interruption confirm cooperative teardown or raise CleanupError.
    The caller decides how to report remote outcomes and retain diagnostics.
    This function does not install dependencies, choose state paths or own reports.
    """
    if timeout <= 0:
        raise ValueError("command timeout must be positive")
    process = job = lifetime = None
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
            else:
                lifetime = LifetimePipe(env)
                env = lifetime.environment
                options["pass_fds"] = lifetime.descriptors
            process = subprocess.Popen(
                command, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr, **options
            )
            if lifetime is not None:
                lifetime.spawned()
            if job is not None:
                job.assign(process.pid)
                process.stdin.write(b"1")
                process.stdin.close()
                process.stdin = None
            try:
                output, errors = process.communicate(timeout=timeout)
            except BaseException:
                _stop(process, job, lifetime)
                raise
            # A command that exits successfully must not leave background workers
            # using its fixture or SDK while the caller releases that state.
            _stop(process, job, lifetime)
            result = subprocess.CompletedProcess(args, process.returncode, output, errors)
            if check:
                result.check_returncode()
            return result
        finally:
            try:
                if process is not None:
                    try:
                        if process.poll() is None:
                            _stop(process, job, lifetime)
                    finally:
                        for stream in (process.stdin, process.stdout, process.stderr):
                            if stream is not None:
                                stream.close()
            finally:
                if job is not None:
                    job.close()
                if lifetime is not None:
                    lifetime.close()
