"""Cleanup for positive PIDs recorded by this test invocation's own workers."""

import os
import signal


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
            # Access denial and every other unknown error must remain visible.
            if os.name != "nt" or getattr(error, "winerror", None) != 87:
                raise
