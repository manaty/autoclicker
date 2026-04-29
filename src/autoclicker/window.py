"""Win32 helpers: enumerate top-level windows and bring one to the foreground.

Used by the autoclicker when a region is bound to a specific window
(see :class:`autoclicker.regions.Region`). On non-Windows platforms every
function returns an empty / no-op result so the rest of the app keeps
working in dev/test.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import List, Optional


_VSCODE_SUFFIX = " - Visual Studio Code"


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    left: int
    top: int
    right: int
    bottom: int
    minimized: bool = False

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def stable_title_match(title: str) -> str:
    """Best-effort stable substring of a window title.

    For VSCode windows of the form ``"file.ts - workspace - Visual Studio Code"``
    returns ``"workspace - Visual Studio Code"`` so the match survives file
    switches. Falls back to the full title for everything else.
    """
    if title.endswith(_VSCODE_SUFFIX):
        body = title[: -len(_VSCODE_SUFFIX)]
        # Drop the leading "● " modified-marker if present.
        if body.startswith("● "):
            body = body[2:]
        if " - " in body:
            workspace = body.rsplit(" - ", 1)[-1]
            if workspace.strip():
                return f"{workspace}{_VSCODE_SUFFIX}"
    return title


def list_windows() -> List[WindowInfo]:
    """All visible top-level windows with non-empty titles."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    out: List[WindowInfo] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if not title.strip():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        out.append(
            WindowInfo(
                hwnd=int(hwnd),
                title=title,
                left=int(rect.left),
                top=int(rect.top),
                right=int(rect.right),
                bottom=int(rect.bottom),
                minimized=bool(user32.IsIconic(hwnd)),
            )
        )
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def find_window(title_substring: str) -> Optional[WindowInfo]:
    """First visible window whose title contains the substring (case-insensitive)."""
    if not title_substring:
        return None
    needle = title_substring.lower()
    for w in list_windows():
        if needle in w.title.lower():
            return w
    return None


def get_foreground() -> Optional[WindowInfo]:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return WindowInfo(
        hwnd=int(hwnd),
        title=buf.value,
        left=int(rect.left),
        top=int(rect.top),
        right=int(rect.right),
        bottom=int(rect.bottom),
        minimized=bool(user32.IsIconic(hwnd)),
    )


def bring_to_front(hwnd: int, settle_s: float = 0.15) -> bool:
    """Bring a window to the foreground.

    On Windows ``SetForegroundWindow`` only works if the calling process
    owns the current foreground window — otherwise it silently fails.
    The standard workaround is to attach our input thread to the
    foreground window's input thread for the duration of the call.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    if user32.SetForegroundWindow(hwnd):
        if settle_s > 0:
            time.sleep(settle_s)
        return True

    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    cur_thread = kernel32.GetCurrentThreadId()
    attached = False
    try:
        if fg_thread and fg_thread != cur_thread:
            attached = bool(user32.AttachThreadInput(fg_thread, cur_thread, True))
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(fg_thread, cur_thread, False)
    if settle_s > 0:
        time.sleep(settle_s)
    return ok
