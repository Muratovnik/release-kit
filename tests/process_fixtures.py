"""Cleanup for positive PIDs recorded by this test invocation's own workers."""

import os
import signal


def _windows_process_finished(pid):
    """Confirm termination on a queried process handle, or retain uncertainty."""
    try:
        import _winapi

        # SYNCHRONIZE is sufficient for a wait; no termination access is needed.
        handle = _winapi.OpenProcess(0x00100000, False, pid)
        try:
            return _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_OBJECT_0
        finally:
            _winapi.CloseHandle(handle)
    except (ImportError, OSError):
        return False


def stop_recorded_worker(ready):
    """Stop a recorded fixture worker without hiding an unknown cleanup failure."""
    if ready.is_file():
        pid = int(ready.read_text())
        if pid <= 0:
            raise ValueError("fixture worker PID must be positive")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as error:
            # Windows OpenProcess reports an absent PID as ERROR_INVALID_PARAMETER.
            # A terminated process can instead deny TerminateProcess while a handle
            # still retains its object. Only a signaled native wait confirms that.
            if os.name == "nt":
                code = getattr(error, "winerror", None)
                if code == 87 or (code == 5 and _windows_process_finished(pid)):
                    return
            raise
