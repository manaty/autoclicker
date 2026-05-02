"""Always-on-top click-through rectangles outlining monitored regions.

One Toplevel per region, transparent interior, lime outline, tiny "#N"
label. On Windows the WS_EX_TRANSPARENT extended style is applied so
mouse events fall through to the window underneath.

Regions whose ``window_title_match`` corresponds to a *completed*
``WindowSession`` are drawn in orange instead of lime — that's the user's
visual cue that the autoclicker has stopped pinging that AI assistant.
"""
from __future__ import annotations

import sys
from typing import List, Sequence

from .capture import Monitor
from .regions import Region
from .sessions import WindowSession


OUTLINE_COLOR = "#37f03c"        # lime — region active
COMPLETED_COLOR = "#ff9933"      # orange — session marked completed
TRANSPARENT_KEY = "#010203"  # any unlikely RGB; Tk makes it see-through


class OverlayController:
    def __init__(self, root) -> None:
        self._root = root
        self._windows: list = []

    def set(
        self,
        regions: Sequence[Region],
        monitors: Sequence[Monitor],
        sessions: Sequence[WindowSession] = (),
    ) -> None:
        self.clear()
        monitor_by_index = {m.index: m for m in monitors}
        completed_matches = {
            s.title_match.lower() for s in sessions if s.completed
        }
        for i, region in enumerate(regions, start=1):
            mon = monitor_by_index.get(region.monitor_index)
            if mon is None:
                continue
            is_completed = (
                bool(region.window_title_match)
                and region.window_title_match.lower() in completed_matches
            )
            color = COMPLETED_COLOR if is_completed else OUTLINE_COLOR
            self._windows.append(self._build(i, region, mon, color))

    def clear(self) -> None:
        for w in self._windows:
            try:
                w.destroy()
            except Exception:
                pass
        self._windows = []

    def _build(self, idx: int, region: Region, monitor: Monitor, color: str = OUTLINE_COLOR):
        import tkinter as tk

        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-transparentcolor", TRANSPARENT_KEY)
        win.configure(bg=TRANSPARENT_KEY)
        gx = monitor.left + region.x
        gy = monitor.top + region.y
        win.geometry(f"{region.w}x{region.h}+{gx}+{gy}")

        canvas = tk.Canvas(
            win,
            width=region.w,
            height=region.h,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)
        canvas.create_rectangle(
            1, 1, region.w - 1, region.h - 1,
            outline=color, width=2,
        )
        canvas.create_text(
            8, 6, text=f"#{idx}",
            fill=color,
            anchor="nw",
            font=("Segoe UI", 10, "bold"),
        )

        if sys.platform == "win32":
            self._make_click_through(win)

        return win

    @staticmethod
    def _make_click_through(win) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000

            GetWindowLong = ctypes.windll.user32.GetWindowLongW
            SetWindowLong = ctypes.windll.user32.SetWindowLongW
            GetWindowLong.restype = ctypes.c_long
            SetWindowLong.restype = ctypes.c_long
            GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
            SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

            cur = GetWindowLong(hwnd, GWL_EXSTYLE)
            SetWindowLong(
                hwnd,
                GWL_EXSTYLE,
                cur | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            )
        except Exception:
            pass
