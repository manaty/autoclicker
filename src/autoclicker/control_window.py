"""Minimal control window — just status + 3 buttons.

The interactive controls live on each region's header bar (pause,
show-logs, edit goals, resize, delete). The control window only
keeps:

  - Status: number of regions, AI-check on/off, last detection / verdict.
  - "Add window region" — the only entry point to add a new region.
  - "Config" — opens config.json in the OS editor.
  - "Quit".

There's no global arm/disarm or pause anymore: the autoclicker is
always active, and the user pauses individual regions from their
overlay headers.
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
    armed: bool = True
    paused: bool = False
    ai_check: bool = False
    region_count: int = 0
    last_detection: str = ""
    last_verdict: str = ""
    version: str = ""
    update_tag: str = ""        # set when a newer release is found
    update_message: str = ""    # short status line shown next to the button


class ControlWindow:
    def __init__(
        self,
        on_quit: Callable[[], None],
        on_pick_window_region: Optional[Callable[[], None]] = None,
        on_install_update: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_quit = on_quit
        self._on_pick_window_region = on_pick_window_region
        self._on_install_update = on_install_update
        self._status = Status()
        self._status_lock = threading.Lock()
        self._status_dirty = threading.Event()
        self.root = None  # set in build()

    def build(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("autoclicker")
        root.geometry("260x220")
        root.minsize(260, 200)
        root.resizable(False, True)
        root.attributes("-topmost", True)
        self.root = root

        style = ttk.Style()
        try:
            style.theme_use("vista" if sys.platform == "win32" else "clam")
        except Exception:
            pass

        pad = {"padx": 8, "pady": 4}

        self._status_label = tk.Label(
            root, text="ACTIVE", font=("Segoe UI", 11, "bold"),
            anchor="center", bg="#37b24d", fg="white", height=2,
        )
        self._status_label.pack(fill="x")

        self._info_label = tk.Label(
            root, text="", font=("Segoe UI", 9),
            anchor="w", justify="left", wraplength=240,
        )
        self._info_label.pack(fill="x", **pad)

        btn_frame = tk.Frame(root)
        btn_frame.pack(fill="x", **pad)
        ttk.Button(btn_frame, text="Add window region", command=self._pick_window).pack(fill="x", pady=2)

        # Update banner — hidden until an update is found.
        self._update_frame = tk.Frame(root, bg="#37b24d")
        self._update_label = tk.Label(
            self._update_frame, text="", bg="#37b24d", fg="white",
            font=("Segoe UI", 9, "bold"), anchor="w", padx=8,
        )
        self._update_label.pack(side="left", fill="x", expand=True)
        self._update_btn = ttk.Button(
            self._update_frame, text="Install", command=self._install_update, width=10,
        )
        self._update_btn.pack(side="right", padx=4, pady=2)
        # Don't pack the banner yet — _refresh() decides.

        bottom = tk.Frame(root)
        bottom.pack(fill="x", **pad, side="bottom")
        ttk.Button(bottom, text="Config", command=self._open_config, width=10).pack(side="left", padx=2)
        ttk.Button(bottom, text="Quit", command=self._quit, width=10).pack(side="right", padx=2)

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
    def _pick_window(self) -> None:
        if self._on_pick_window_region is not None:
            self._on_pick_window_region()

    def _install_update(self) -> None:
        if self._on_install_update is not None:
            self._on_install_update()

    def _quit(self) -> None:
        self._on_quit()
        try:
            self.root.destroy()
        except Exception:
            pass

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

        info = []
        if st.version:
            info.append(f"version: {st.version}")
        info.append(f"regions: {st.region_count or 0}")
        info.append(f"AI check: {'on' if st.ai_check else 'OFF (no OPENAI_API_KEY)'}")
        if st.last_detection:
            info.append(f"last: {st.last_detection}")
        if st.last_verdict:
            info.append(f"verdict: {st.last_verdict}")
        self._info_label.config(text="\n".join(info))

        # Show / hide the update banner.
        if st.update_tag:
            label_text = st.update_message or f"Update available: {st.update_tag}"
            self._update_label.config(text=label_text)
            if not self._update_frame.winfo_ismapped():
                self._update_frame.pack(fill="x", before=self._info_label)
        else:
            if self._update_frame.winfo_ismapped():
                self._update_frame.pack_forget()
