"""Native process ownership for an ET retrieval worker and its descendants."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from ctypes import wintypes

if sys.platform == "win32":
    from ctypes import WinError


def worker_launch_options() -> dict[str, bool | int]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def kill_worker_group(pid: int) -> None:
    """On POSIX the worker is a session leader and owns this process group."""
    if sys.platform != "win32":
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("per_process_time", ctypes.c_int64),
        ("per_job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_processes", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io_counters", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


def enter_windows_job() -> None:
    """The worker owns a non-inherited job handle until the OS reaps it.

    On supervisor loss this lets the existing stdin lease record the result and
    clean up before the worker exits. A direct hard kill also closes the handle,
    so children cannot escape cancellation merely by outliving the worker.
    """
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.GetCurrentProcess.argtypes = []
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL

        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise WinError(ctypes.get_last_error())
        try:
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            extended_limit_information = 9  # JobObjectExtendedLimitInformation
            if not kernel.SetInformationJobObject(
                job, extended_limit_information, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise WinError(ctypes.get_last_error())
            if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
                raise WinError(ctypes.get_last_error())
        except OSError:
            kernel.CloseHandle(job)
            raise
