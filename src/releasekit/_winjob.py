"""Minimal native Windows Job Object adapter shared by command and MCP execution.

The OS owns descendant membership and termination. There is no global PID walk and no
breakaway permission. Importing this module on POSIX does not load Windows APIs.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int64)
        for name in (
            "TotalUserTime",
            "TotalKernelTime",
            "ThisPeriodTotalUserTime",
            "ThisPeriodTotalKernelTime",
        )
    ] + [
        (name, wintypes.DWORD)
        for name in (
            "TotalPageFaultCount",
            "TotalProcesses",
            "ActiveProcesses",
            "TotalTerminatedProcesses",
        )
    ]


class Job:
    def __init__(self):
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "QueryInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
                wintypes.BOOL,
            ),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "IsProcessInJob": (
                [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)],
                wintypes.BOOL,
            ),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(api, name)
            function.argtypes, function.restype = arguments, result
        self.api = api
        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, pid: int) -> None:
        process = self.api.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(process)

    def _members(self, deadline):
        capacity = 32
        while True:

            class Members(ctypes.Structure):
                _fields_ = [
                    ("assigned", wintypes.DWORD),
                    ("count", wintypes.DWORD),
                    ("pids", ctypes.c_size_t * capacity),
                ]

            members = Members()
            if self.api.QueryInformationJobObject(
                self.handle, 3, ctypes.byref(members), ctypes.sizeof(members), None
            ):
                return list(members.pids[: members.count])
            error = ctypes.get_last_error()
            if error != 234:  # ERROR_MORE_DATA: membership grew since allocation.
                raise ctypes.WinError(error)
            if time.monotonic() >= deadline:
                raise TimeoutError("Windows job membership did not stabilize")
            capacity = max(capacity * 2, members.assigned)

    def stop(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        handles = []
        try:
            # ActiveProcesses can reach zero before process teardown releases cwd.
            # Keep waitable handles before termination removes the job membership.
            for pid in self._members(deadline):
                process = self.api.OpenProcess(0x00100000 | 0x1000, False, pid)
                if not process:
                    error = ctypes.get_last_error()
                    if error == 87:  # Process exited before its handle was opened.
                        continue
                    raise ctypes.WinError(error)
                handles.append(process)
                member = wintypes.BOOL()
                if not self.api.IsProcessInJob(process, self.handle, ctypes.byref(member)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not member.value:  # PID reuse must never wait on an unrelated process.
                    handles.pop()
                    self.api.CloseHandle(process)
            self._terminate(deadline)
            for process in handles:
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                status = self.api.WaitForSingleObject(process, remaining)
                if status == 258:
                    raise TimeoutError("Windows job process teardown did not finish")
                if status != 0:
                    raise ctypes.WinError(ctypes.get_last_error())
        finally:
            for process in handles:
                self.api.CloseHandle(process)

    def _terminate(self, deadline) -> None:
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        while True:
            state = _Accounting()
            if not self.api.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(state), ctypes.sizeof(state), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not state.ActiveProcesses:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Windows job did not become empty after termination")
            time.sleep(0.01)

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
