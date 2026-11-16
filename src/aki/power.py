"""Requests a long run makes of Windows about itself. Neither changes a setting.

Both last only while the asking process is alive, and both are best effort: a run must
never fail because one of them could not be made. On other systems they do nothing.
"""
from __future__ import annotations

import sys


def keep_awake(on: bool) -> bool:
    """Ask Windows not to sleep while a long run is working.

    This is the request a video player makes. The screen may still turn off. Without it, a
    computer set to sleep when idle stops an overnight run (it did, 15 November 2026:
    asleep from 00:13 to 09:48 with the workers frozen). Returns whether it was applied.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        continuous, system_required = 0x80000000, 0x00000001
        flags = continuous | (system_required if on else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:  # noqa: BLE001 - staying awake is a courtesy, never a reason to fail
        return False


def full_speed() -> bool:
    """Ask Windows not to power-throttle this process.

    Windows 11 slows processes it takes for background work (no window, not in front) to
    save power, most of all on laptop processors with separate efficiency cores. Reading
    workers are exactly that, and it cost more than anything else: the same page of the
    1560 Geneva Bible took 167 s throttled and 46 s after this call, for the same 34 s of
    processor work (measured 15 November 2026). The opt-out covers this process only and
    ends with it. Returns whether it was applied.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _Throttling(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                        ("StateMask", wintypes.ULONG)]

        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetProcessInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        # Version 1; control execution speed (0x1); state 0 = never throttle.
        state = _Throttling(1, 0x1, 0x0)
        process_power_throttling = 4
        return bool(kernel.SetProcessInformation(
            kernel.GetCurrentProcess(), process_power_throttling,
            ctypes.byref(state), ctypes.sizeof(state)))
    except Exception:  # noqa: BLE001 - speed is a courtesy, never a reason to fail
        return False
