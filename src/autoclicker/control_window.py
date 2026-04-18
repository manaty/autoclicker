"""Dedicated control window — replaces the system-tray icon.

Runs on the main thread (tkinter requires it). The worker thread writes
status snapshots via :meth:`set_status`; the window polls for changes
with ``root.after`` so nothing blocks either side.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .paths import config_path, log_dir


@dataclass
class Status:
    armed: bool = False
    paused: bool = False
    ai_check: bool = False
    region_count: int = 0
    last_detection: str = ""
    last_verdict: str = ""


class ControlWindow:
    def __init__(
        self,
        armed: threading.Event,
        paused: threading.Event,
        on_quit: Callable[[], None],
        on_pick_regions: Callable[[], None],
        on_clear_regions: Callable[[], None],
    ) -> None:
        self._armed = armed
        self._paused = paused
        self._on_quit = on_quit
        self._on_pick_regions = on_pick_regions
        self._on_clear_regions = on_clear_regions
        self._status = Status()
        self._status_lock = threading.Lock()
        self._status_dirty = threading.Event()
        self.root = None  # set in build()

    def build(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("autoclicker")
        root.geometry("260x280")
        root.resizable(False, False)
        root.attributes("-topmost", True)
        self.root = root

        style = ttk.Style()
        try:
            style.theme_use("vista" if sys.platform == "win32" else "clam")
        except Exception:
            pass

        pad = {"padx": 8, "pady": 4}

        self._status_label = tk.Label(
            root, text="", font=("Segoe UI", 11, "bold"),
            anchor="center", bg="#cccccc", fg="black", height=2,
        )
        self._status_label.pack(fill="x")

        self._info_label = tk.Label(
            root, text="", font=("Segoe UI", 9),
            anchor="w", justify="left", wraplength=240,
        )
        self._info_label.pack(fill="x", **pad)

        btn_frame = tk.Frame(root)
        btn_frame.pack(fill="x", **pad)

        self._arm_btn = ttk.Button(btn_frame, text="Arm auto-click", command=self._toggle_arm)
        self._arm_btn.pack(fill="x", pady=2)

        self._pause_btn = ttk.Button(btn_frame, text="Pause monitoring", command=self._toggle_pause)
        self._pause_btn.pack(fill="x", pady=2)

        ttk.Button(btn_frame, text="Set monitored regions", command=self._pick).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="Clear regions", command=self._clear).pack(fill="x", pady=2)

        bottom = tk.Frame(root)
        bottom.pack(fill="x", **pad, side="bottom")
        ttk.Button(bottom, text="Logs", command=self._open_logs, width=8).pack(side="left", padx=2)
        ttk.Button(bottom, text="Config", command=self._open_config, width=8).pack(side="left", padx=2)
        ttk.Button(bottom, text="Quit", command=self._quit, width=8).pack(side="right", padx=2)

        root.protocol("WM_DELETE_WINDOW", self._quit)
        self._refresh()
        root.after(200, self._poll_status)

    def run(self) -> None:
        assert self.root is not None
        self.root.mainloop()

    def set_status(self, **kwargs) -> None:
        with self._status_lock:
            for k, v in kwargs.items():
                setattr(self._status, k, v)
        self._status_dirty.set()

    # ---- button handlers -------------------------------------------------
    def _toggle_arm(self) -> None:
        if self._armed.is_set():
            self._armed.clear()
        else:
            self._armed.set()
        self._refresh()

    def _toggle_pause(self) -> None:
        if self._paused.is_set():
            self._paused.clear()
        else:
            self._paused.set()
        self._refresh()

    def _pick(self) -> None:
        self._on_pick_regions()

    def _clear(self) -> None:
        self._on_clear_regions()
        self._refresh()

    def _quit(self) -> None:
        self._on_quit()
        try:
            self.root.destroy()
        except Exception:
            pass

    def _open_logs(self) -> None:
        self._open_path(log_dir())

    def _open_config(self) -> None:
        p = config_path()
        if not p.exists():
            p.write_text("{}\n", encoding="utf-8")
        self._open_path(p)

    @staticmethod
    def _open_path(p: Path) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception:
            pass

    # ---- rendering -------------------------------------------------------
    def _poll_status(self) -> None:
        if self._status_dirty.is_set():
            self._status_dirty.clear()
            self._refresh()
        if self.root is not None:
            self.root.after(200, self._poll_status)

    def _refresh(self) -> None:
        with self._status_lock:
            st = Status(**self._status.__dict__)
        armed = self._armed.is_set()
        paused = self._paused.is_set()

        if paused:
            txt, bg = "PAUSED", "#b0b0b0"
        elif armed:
            txt, bg = "ARMED", "#37b24d"
        else:
            txt, bg = "DRY-RUN", "#c7c7c7"

        self._status_label.config(text=txt, bg=bg, fg="white" if armed else "black")

        info = [
            f"regions: {st.region_count or 0}",
            f"AI check: {'on' if st.ai_check else 'OFF (no OPENAI_API_KEY)'}",
        ]
        if st.last_detection:
            info.append(f"last: {st.last_detection}")
        if st.last_verdict:
            info.append(f"verdict: {st.last_verdict}")
        self._info_label.config(text="\n".join(info))

        self._arm_btn.config(text="Disarm (dry-run)" if armed else "Arm auto-click")
        self._pause_btn.config(text="Resume monitoring" if paused else "Pause monitoring")
