"""Per-region header bar: '#N', scrolling log, edit / resize / delete buttons.

Sits as a separate non-click-through Toplevel just above the
corresponding region rectangle (or at the top of the region if there
isn't enough vertical room above). The rectangle Toplevel itself
remains click-through so the user keeps interacting with the AI's
window underneath; only the header strip captures clicks.
"""
from __future__ import annotations

import sys
from typing import Callable, Optional

from .capture import Monitor
from .log_ticker import LogTicker
from .regions import Region


HEADER_HEIGHT = 24
TICKER_MIN_REGION_WIDTH = 250  # below this, hide the marquee, keep buttons.

# Colors mirror overlay.py — green for active, orange for completed sessions,
# grey when manually paused.
ACTIVE_BG = "#1a2b1a"
ACTIVE_FG = "#37f03c"
COMPLETED_BG = "#2b1f0e"
COMPLETED_FG = "#ff9933"
PAUSED_BG = "#222"
PAUSED_FG = "#888"


class RegionHeader:
    def __init__(
        self,
        root,
        idx: int,
        region: Region,
        monitor: Monitor,
        ticker: LogTicker,
        ticker_key: str,
        on_edit: Optional[Callable[[int], None]] = None,
        on_resize: Optional[Callable[[int], None]] = None,
        on_delete: Optional[Callable[[int], None]] = None,
        on_toggle_pause: Optional[Callable[[int], None]] = None,
        on_show_logs: Optional[Callable[[int], None]] = None,
        completed: bool = False,
        paused: bool = False,
    ) -> None:
        self._root = root
        self._idx = idx
        self._region = region
        self._monitor = monitor
        self._ticker = ticker
        self._ticker_key = ticker_key
        self._on_edit = on_edit
        self._on_resize = on_resize
        self._on_delete = on_delete
        self._on_toggle_pause = on_toggle_pause
        self._on_show_logs = on_show_logs
        self._completed = completed
        self._paused = paused

        self._win = None
        self._marquee_canvas = None
        self._marquee_text_id = None
        self._marquee_x: float = 0.0
        self._marquee_text: str = ""
        self._after_id = None

        self._build()

    # ---- geometry -------------------------------------------------------
    def _placement(self) -> tuple[int, int, int]:
        """Return (gx, gy, w) for the header — above the region if room exists."""
        gx = self._monitor.left + self._region.x
        gy = self._monitor.top + self._region.y
        if gy >= HEADER_HEIGHT + 2:
            return gx, gy - HEADER_HEIGHT - 2, self._region.w
        # Not enough room above → place inside, at the top of the region.
        return gx, gy, self._region.w

    # ---- ui -------------------------------------------------------------
    def _build(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        gx, gy, w = self._placement()
        if self._paused:
            bg, fg = PAUSED_BG, PAUSED_FG
        elif self._completed:
            bg, fg = COMPLETED_BG, COMPLETED_FG
        else:
            bg, fg = ACTIVE_BG, ACTIVE_FG

        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{w}x{HEADER_HEIGHT}+{gx}+{gy}")
        win.configure(bg=bg)
        self._win = win

        if sys.platform == "win32":
            self._make_toolwindow(win)

        # Layout: [#N]  [marquee canvas]  [E][R][X]
        # Use a frame that fills the toplevel.
        frame = tk.Frame(win, bg=bg, height=HEADER_HEIGHT)
        frame.pack(fill="both", expand=True)

        idx_label = tk.Label(
            frame, text=f"#{self._idx}",
            fg=fg, bg=bg,
            font=("Segoe UI", 10, "bold"),
            padx=6,
        )
        idx_label.pack(side="left")

        # Buttons on the right (packed first so they reserve their width).
        btn_style = {
            "bg": bg, "fg": fg, "activebackground": bg,
            "activeforeground": "#ffffff",
            "bd": 0, "padx": 4, "pady": 0,
            "font": ("Segoe UI", 9, "bold"),
            "cursor": "hand2",
        }
        delete_btn = tk.Label(frame, text="✕", **btn_style)
        delete_btn.pack(side="right", padx=(0, 6))
        delete_btn.bind("<Button-1>", lambda _e: self._fire_delete())

        resize_btn = tk.Label(frame, text="⤢", **btn_style)
        resize_btn.pack(side="right")
        resize_btn.bind("<Button-1>", lambda _e: self._fire_resize())

        # Pause / unpause toggle. ▶ when paused (click to resume), ⏸ when active.
        pause_glyph = "▶" if self._paused else "⏸"
        pause_btn = tk.Label(frame, text=pause_glyph, **btn_style)
        pause_btn.pack(side="right")
        pause_btn.bind("<Button-1>", lambda _e: self._fire_pause())

        logs_btn = tk.Label(frame, text="👁", **btn_style)
        logs_btn.pack(side="right")
        logs_btn.bind("<Button-1>", lambda _e: self._fire_show_logs())

        # Edit only meaningful when the region is window-bound (has a session).
        if self._region.window_title_match:
            edit_btn = tk.Label(frame, text="✎", **btn_style)
            edit_btn.pack(side="right")
            edit_btn.bind("<Button-1>", lambda _e: self._fire_edit())

        # Marquee fills the remaining space — only when wide enough.
        if w >= TICKER_MIN_REGION_WIDTH:
            canvas = tk.Canvas(
                frame, bg=bg, height=HEADER_HEIGHT,
                highlightthickness=0, borderwidth=0,
            )
            canvas.pack(side="left", fill="both", expand=True, padx=4)
            self._marquee_canvas = canvas
            self._marquee_text_id = canvas.create_text(
                0, HEADER_HEIGHT // 2,
                anchor="w",
                text="…",
                fill=fg,
                font=("Segoe UI", 9),
            )
            # Start the animation loop.
            self._marquee_x = float(w)
            self._refresh_marquee_text()
            self._tick_marquee()

    def _make_toolwindow(self, win) -> None:
        """Tag the window as a toolwindow so it doesn't show in the taskbar."""
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000

            GetWindowLong = ctypes.windll.user32.GetWindowLongW
            SetWindowLong = ctypes.windll.user32.SetWindowLongW
            GetWindowLong.restype = ctypes.c_long
            SetWindowLong.restype = ctypes.c_long
            GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
            SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

            cur = GetWindowLong(hwnd, GWL_EXSTYLE)
            SetWindowLong(hwnd, GWL_EXSTYLE, cur | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        except Exception:
            pass

    # ---- marquee --------------------------------------------------------
    def _refresh_marquee_text(self) -> None:
        if self._marquee_canvas is None or self._marquee_text_id is None:
            return
        # Show this region's events only — fall back to global only when
        # we have nothing region-specific yet.
        text = self._ticker.text_for(key=self._ticker_key, fallback_global=True) or "—"
        if text != self._marquee_text:
            self._marquee_text = text
            self._marquee_canvas.itemconfig(self._marquee_text_id, text=text)

    def _tick_marquee(self) -> None:
        if self._win is None or self._marquee_canvas is None:
            return
        try:
            self._marquee_x -= 1.5
            self._marquee_canvas.coords(
                self._marquee_text_id,
                self._marquee_x, HEADER_HEIGHT // 2,
            )
            bbox = self._marquee_canvas.bbox(self._marquee_text_id)
            if bbox is not None:
                _x0, _y0, x1, _y1 = bbox
                if x1 < 0:
                    # Whole text scrolled off — refresh and restart from right.
                    self._refresh_marquee_text()
                    canvas_w = self._marquee_canvas.winfo_width() or self._region.w
                    self._marquee_x = float(canvas_w)
            self._after_id = self._win.after(40, self._tick_marquee)
        except Exception:
            self._after_id = None

    # ---- callbacks ------------------------------------------------------
    def _fire_edit(self) -> None:
        if self._on_edit is not None:
            try:
                self._on_edit(self._idx)
            except Exception:
                pass

    def _fire_resize(self) -> None:
        if self._on_resize is not None:
            try:
                self._on_resize(self._idx)
            except Exception:
                pass

    def _fire_delete(self) -> None:
        if self._on_delete is not None:
            try:
                self._on_delete(self._idx)
            except Exception:
                pass

    def _fire_pause(self) -> None:
        if self._on_toggle_pause is not None:
            try:
                self._on_toggle_pause(self._idx)
            except Exception:
                pass

    def _fire_show_logs(self) -> None:
        if self._on_show_logs is not None:
            try:
                self._on_show_logs(self._idx)
            except Exception:
                pass

    # ---- lifecycle ------------------------------------------------------
    def destroy(self) -> None:
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
