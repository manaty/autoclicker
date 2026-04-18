"""Windows single-instance guard using a named mutex.

Call :func:`acquire` at startup. If another instance is already running it
returns ``False`` so the caller can bail out. The mutex handle is
intentionally never released — it lives for the lifetime of the process.
"""
from __future__ import annotations

import sys
from typing import Optional


_MUTEX_NAME = "Global\\autoclicker-singleton-9f1b2c3d"
_ERROR_ALREADY_EXISTS = 183


def acquire(mutex_name: str = _MUTEX_NAME) -> bool:
    """Return True if this is the first instance, False if another is running."""
    if sys.platform != "win32":
        return True

    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]

    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        return True  # fail open: can't create mutex → let it run
    last_err = kernel32.GetLastError()
    return last_err != _ERROR_ALREADY_EXISTS


def show_already_running_dialog() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            "autoclicker is already running. Check the system tray for the icon.",
            "autoclicker",
            0x40,  # MB_ICONINFORMATION
        )
    except Exception:
        pass
