"""Always-on-top click-through rectangles outlining monitored regions.

Two Toplevels per region:
  - the rectangle: click-through, just an outline + "#N" label.
  - the header strip: not click-through, hosts the marquee log + the
    edit / resize / delete buttons (see :mod:`region_header`).

Regions whose ``window_title_match`` corresponds to a *completed*
``WindowSession`` are drawn in orange instead of lime — that's the
user's cue that the autoclicker has stopped pinging that AI.
"""
from __future__ import annotations

import sys
from typing import Callable, List, Optional, Sequence

from .capture import Monitor
from .log_ticker import LogTicker
from .region_header import RegionHeader
from .regions import Region
from .sessions import WindowSession


OUTLINE_COLOR = "#37f03c"        # lime — region active
COMPLETED_COLOR = "#ff9933"      # orange — session marked completed
PAUSED_COLOR = "#888888"         # grey — region monitoring paused
TRANSPARENT_KEY = "#010203"  # any unlikely RGB; Tk makes it see-through


class OverlayController:
    def __init__(
        self,
        root,
        ticker: Optional[LogTicker] = None,
        on_edit: Optional[Callable[[int], None]] = None,
        on_resize: Optional[Callable[[int], None]] = None,
        on_delete: Optional[Callable[[int], None]] = None,
        on_toggle_pause: Optional[Callable[[int], None]] = None,
        on_show_logs: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._root = root
        self._ticker = ticker
        self._on_edit = on_edit
        self._on_resize = on_resize
        self._on_delete = on_delete
        self._on_toggle_pause = on_toggle_pause
        self._on_show_logs = on_show_logs
        self._windows: list = []
        self._headers: list[RegionHeader] = []

    def set(
        self,
        regions: Sequence[Region],
        monitors: Sequence[Monitor],
        sessions: Sequence[WindowSession] = (),
        paused_indices: Optional[set[int]] = None,
    ) -> None:
        self.clear()
        monitor_by_index = {m.index: m for m in monitors}
        completed_matches = {
            s.title_match.lower() for s in sessions if s.completed
        }
        paused_indices = paused_indices or set()
        for i, region in enumerate(regions, start=1):
            mon = monitor_by_index.get(region.monitor_index)
            if mon is None:
                continue
            cfg_idx = i - 1
            is_paused = cfg_idx in paused_indices
            is_completed = (
                not is_paused
                and bool(region.window_title_match)
                and region.window_title_match.lower() in completed_matches
            )
            if is_paused:
                color = PAUSED_COLOR
            elif is_completed:
                color = COMPLETED_COLOR
            else:
                color = OUTLINE_COLOR
            self._windows.append(self._build(i, region, mon, color))
            if self._ticker is not None:
                ticker_key = region.window_title_match or f"region_{cfg_idx}"
                header = RegionHeader(
                    self._root,
                    idx=i,
                    region=region,
                    monitor=mon,
                    ticker=self._ticker,
                    ticker_key=ticker_key,
                    on_edit=(lambda _i, c=cfg_idx: self._on_edit(c)) if self._on_edit else None,
                    on_resize=(lambda _i, c=cfg_idx: self._on_resize(c)) if self._on_resize else None,
                    on_delete=(lambda _i, c=cfg_idx: self._on_delete(c)) if self._on_delete else None,
                    on_toggle_pause=(lambda _i, c=cfg_idx: self._on_toggle_pause(c)) if self._on_toggle_pause else None,
                    on_show_logs=(lambda _i, c=cfg_idx: self._on_show_logs(c)) if self._on_show_logs else None,
                    completed=is_completed,
                    paused=is_paused,
                )
                self._headers.append(header)

    def clear(self) -> None:
        for w in self._windows:
            try:
                w.destroy()
            except Exception:
                pass
        self._windows = []
        for h in self._headers:
            try:
                h.destroy()
            except Exception:
                pass
        self._headers = []

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
