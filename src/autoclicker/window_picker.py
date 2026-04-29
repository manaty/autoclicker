"""Tk dialog: list visible top-level windows, let the user pick one.

Returned tuple is ``(window_info, title_match)`` where ``title_match`` is
the substring the autoclicker will use at runtime to find the window
again. The user can edit it in the dialog (defaults to a stable
heuristic — see :func:`window.stable_title_match`).
"""
from __future__ import annotations

from typing import Optional, Tuple

from .window import WindowInfo, list_windows, stable_title_match


def pick_window(parent=None) -> Optional[Tuple[WindowInfo, str]]:
    import tkinter as tk
    from tkinter import ttk

    windows = [w for w in list_windows() if not w.minimized]
    if not windows:
        return None

    owns_root = parent is None
    if owns_root:
        root = tk.Tk()
        root.withdraw()
    else:
        root = parent

    dlg = tk.Toplevel(root)
    dlg.title("Pick a window to monitor")
    dlg.geometry("560x380")
    dlg.attributes("-topmost", True)
    dlg.transient(root)
    dlg.grab_set()

    pad = {"padx": 10, "pady": 6}

    tk.Label(
        dlg,
        text="Pick the window you want to monitor (one region group per window):",
        anchor="w", justify="left", wraplength=540,
    ).pack(fill="x", **pad)

    list_frame = tk.Frame(dlg)
    list_frame.pack(fill="both", expand=True, padx=10)

    scroll = ttk.Scrollbar(list_frame, orient="vertical")
    listbox = tk.Listbox(
        list_frame, height=10, yscrollcommand=scroll.set,
        font=("Segoe UI", 10), activestyle="dotbox",
    )
    scroll.config(command=listbox.yview)
    listbox.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    for w in windows:
        suffix = "" if not w.minimized else "  (minimized)"
        listbox.insert("end", f"{w.title}{suffix}")
    listbox.selection_set(0)

    match_frame = tk.Frame(dlg)
    match_frame.pack(fill="x", **pad)
    tk.Label(match_frame, text="Match by title contains:", anchor="w").pack(fill="x")
    match_var = tk.StringVar(value=stable_title_match(windows[0].title))
    match_entry = ttk.Entry(match_frame, textvariable=match_var)
    match_entry.pack(fill="x", pady=(2, 0))

    def on_listbox_select(_evt=None):
        sel = listbox.curselection()
        if sel:
            match_var.set(stable_title_match(windows[sel[0]].title))

    listbox.bind("<<ListboxSelect>>", on_listbox_select)

    result: dict = {"value": None}

    def on_ok():
        sel = listbox.curselection()
        if not sel:
            return
        match = match_var.get().strip()
        if not match:
            return
        result["value"] = (windows[sel[0]], match)
        dlg.destroy()

    def on_cancel():
        dlg.destroy()

    btns = tk.Frame(dlg)
    btns.pack(fill="x", **pad, side="bottom")
    ttk.Button(btns, text="Cancel", command=on_cancel, width=10).pack(side="right", padx=4)
    ttk.Button(btns, text="OK", command=on_ok, width=10).pack(side="right")

    dlg.bind("<Return>", lambda _e: on_ok())
    dlg.bind("<Escape>", lambda _e: on_cancel())
    listbox.focus_set()

    dlg.wait_window()
    if owns_root:
        root.destroy()

    return result["value"]
