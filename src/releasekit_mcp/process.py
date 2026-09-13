"""Bounded child execution with owned process-tree cancellation, never shell argv."""

import os
import signal
import subprocess
import sys

import anyio

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


async def execute(path, digest, argv, root, environment, timeout, limit=8 * 1024 * 1024):
    """Return exit/stdout/stderr/truncation; reap only this invocation's descendants."""
    process = None
    job = None
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
            process = await anyio.open_process(
                [sys.executable, "-I", "-B", "-c", BOOTSTRAP, str(path), digest, *argv],
                cwd=root,
                env=environment,
                **options,
            )
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
                if job is not None:
                    await anyio.to_thread.run_sync(job.stop)
                elif process is not None and os.name != "nt":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            finally:
                if job is not None:
                    job.close()
                if process is not None:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                    await process.wait()
                    await process.aclose()
