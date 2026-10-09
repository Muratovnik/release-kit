"""Bounded child execution with owned process-tree cancellation, never shell argv."""

import os
import signal
import subprocess
import sys

import anyio

from releasekit.processes import CleanupError, LifetimePipe, cancellation_exit_confirmed

_GRACE = 5.0

# The child cannot start project code until the parent owns its process tree.
# This avoids the Windows race between spawn and Job Object assignment.
BOOTSTRAP = """
import hashlib, pathlib, runpy, sys
if sys.stdin.buffer.read(1) != b'1':
    raise SystemExit(2)
path, expected = sys.argv[1:3]
if hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() != expected:
    raise SystemExit('pinned projection changed before execution')
sys.argv = [path, *sys.argv[3:]]
runpy.run_path(path, run_name='__main__')
"""


def _group(pid, signum):
    """Signal or probe a group; True means present or unconfirmed, not delivered."""
    try:
        os.killpg(pid, signum)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Darwin can report EPERM while an exiting group contains only zombies.
        # Wait within the existing deadline; only ESRCH establishes disappearance.
        # A persistent refusal remains unconfirmed and retains recovery state.
        return True


async def _stop(process, job, lifetime=None):
    """Allow nested CLI runners to clean their groups, then confirm owned teardown."""
    try:
        was_running = process is not None and process.returncode is None
        forced = False
        if job is not None:
            await anyio.to_thread.run_sync(job.stop)
        elif process is not None:
            # releasekit.processes owns separate nested groups. SIGTERM lets its
            # handler stop those workers and persist a release receipt before exit.
            _group(process.pid, signal.SIGTERM)
            with anyio.move_on_after(_GRACE):
                while _group(process.pid, 0):
                    await anyio.sleep(0.01)
            if _group(process.pid, 0):
                forced = True
                _group(process.pid, signal.SIGKILL)
        if process is not None:
            # POSIX teardown already signals the owned group. Its exit notification
            # can still be queued after the group disappears. Await it without
            # treating a no-op PID kill as forced. Windows still needs the fallback
            # for an unassigned child.
            if job is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            with anyio.fail_after(_GRACE):
                await process.wait()
                if job is None:
                    while _group(process.pid, 0):
                        await anyio.sleep(0.01)
            if lifetime is not None and not lifetime.finished():
                raise CleanupError("nested CLI command remains active; inspect retained state")
            if job is None and (
                forced or (was_running and not cancellation_exit_confirmed(process.returncode))
            ):
                raise CleanupError(
                    "CLI process cleanup is unconfirmed after forced or abnormal termination; "
                    "inspect retained state"
                )
    except (OSError, TimeoutError) as error:
        raise CleanupError("CLI process cleanup is unconfirmed; inspect retained state") from error


async def execute(path, digest, argv, root, environment, timeout, limit=8 * 1024 * 1024):
    """Return exit/stdout/stderr/truncation; reap only this invocation's descendants."""
    process = None
    job = None
    lifetime = None
    stdout, stderr = bytearray(), bytearray()
    overflow = False
    stderr_truncated = False

    async def drain(stream, output, capacity, *, tail=False):
        nonlocal overflow, stderr_truncated
        async for chunk in stream:
            if tail:
                output.extend(chunk)
                if len(output) > capacity:
                    del output[:-capacity]
                    stderr_truncated = True
            else:
                room = capacity - len(output)
                output.extend(chunk[:room])
                overflow |= len(chunk) > room
                if overflow:
                    group.cancel_scope.cancel()
                    return

    try:
        with anyio.CancelScope(shield=True):
            options = (
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {"start_new_session": True}
            )
            if os.name == "nt":
                from releasekit._winjob import Job

                job = Job()
            else:
                lifetime = LifetimePipe(environment)
                environment = lifetime.environment
                options["pass_fds"] = lifetime.descriptors
            process = await anyio.open_process(
                [sys.executable, "-I", "-B", "-c", BOOTSTRAP, str(path), digest, *argv],
                cwd=root,
                env=environment,
                **options,
            )
            if lifetime is not None:
                lifetime.spawned()
            if job is not None:
                job.assign(process.pid)
            await process.stdin.send(b"1")
            await process.stdin.aclose()
        with anyio.fail_after(timeout):
            async with anyio.create_task_group() as group:
                group.start_soon(drain, process.stdout, stdout, limit)

                # Keep a bounded tail for a useful final error without buffering logs.
                async def errors():
                    await drain(process.stderr, stderr, 32768, tail=True)

                group.start_soon(errors)
                code = await process.wait()
        if overflow:
            raise ValueError("CLI output exceeded the structured-result size limit")
        return code, bytes(stdout), stderr.decode("utf-8", errors="replace"), stderr_truncated
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await _stop(process, job, lifetime)
            finally:
                if job is not None:
                    job.close()
                if lifetime is not None:
                    lifetime.close()
                if process is not None:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except OSError:
                            pass
                    with anyio.move_on_after(_GRACE):
                        await process.wait()
                        await process.aclose()
