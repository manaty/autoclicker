"""Floating Toplevel that displays the recent log entries for one region.

Opened from the eye/clipboard icon on a region header. While the panel
is open the autoclicker pauses monitoring of that region (the App
tracks ``_logs_open_regions``) so the contents are stable while the
user reads. Closing the panel re-enables monitoring.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Callable, Optional

from .capture import Monitor
from .log_ticker import LogTicker
from .regions import Region


PANEL_HEIGHT = 220


class RegionLogsPanel:
    def __init__(
        self,
        root,
        idx: int,
        region: Region,
        monitor: Monitor,
        ticker: LogTicker,
        ticker_key: str,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        self._root = root
        self._idx = idx
        self._region = region
        self._monitor = monitor
        self._ticker = ticker
        self._ticker_key = ticker_key
        self._on_close = on_close

        self._win = None
        self._text = None
        self._after_id = None
        self._last_count = -1

        self._build()

    def _placement(self) -> tuple[int, int, int]:
        gx = self._monitor.left + self._region.x
        gy = self._monitor.top + self._region.y
        # Try placing the panel above the header (which sits ~26px above
        # the region). If there isn't room, drop it inside the region.
        wanted_top = gy - 26 - PANEL_HEIGHT - 2
        if wanted_top >= 0:
            return gx, wanted_top, self._region.w
        return gx, gy + 2, self._region.w

    def _build(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        gx, gy, w = self._placement()

        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{w}x{PANEL_HEIGHT}+{gx}+{gy}")
        win.configure(bg="#161616")
        self._win = win

        title_bar = tk.Frame(win, bg="#222", height=22)
        title_bar.pack(fill="x")
        tk.Label(
            title_bar,
            text=f"  Logs · region #{self._idx} · monitoring paused",
            bg="#222", fg="#9cdcfe",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        close_btn = tk.Label(
            title_bar, text="✕  ", bg="#222", fg="#bbb",
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda _e: self.close())
        win.bind("<Escape>", lambda _e: self.close())

        body = tk.Frame(win, bg="#161616")
        body.pack(fill="both", expand=True)

        scroll = ttk.Scrollbar(body, orient="vertical")
        text = tk.Text(
            body,
            wrap="word",
            bg="#161616", fg="#d4d4d4",
            insertbackground="#d4d4d4",
            font=("Consolas", 9),
            relief="flat",
            yscrollcommand=scroll.set,
        )
        scroll.config(command=text.yview)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.config(state="disabled")
        self._text = text

        self._refresh()
        self._after_id = win.after(500, self._tick)

    def _refresh(self) -> None:
        if self._text is None:
            return
        entries = self._ticker.lines_for(key=self._ticker_key, fallback_global=True)
        if len(entries) == self._last_count:
            return
        self._last_count = len(entries)

        body_lines = []
        for ts, msg in entries:
            t = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            body_lines.append(f"[{t}] {msg}")
        body = "\n".join(body_lines) or "(no entries yet)"

        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("1.0", body)
        self._text.see("end")
        self._text.config(state="disabled")

    def _tick(self) -> None:
        if self._win is None:
            return
        try:
            self._refresh()
            self._after_id = self._win.after(500, self._tick)
        except Exception:
            self._after_id = None

    def close(self) -> None:
        if self._after_id is not None and self._win is not None:
            try:
                self._win.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass
