"""Fullscreen translucent overlay for drawing monitored regions.

Uses a Toplevel per monitor so it can share the main app's Tk root. If no
parent is supplied (CLI --pick path), creates its own Tk root.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .capture import Monitor
from .regions import Region


def pick_regions(
    monitors: List[Monitor],
    parent=None,
    only_monitor_index: Optional[int] = None,
    window_title_match: Optional[str] = None,
    hint_suffix: str = "",
) -> List[Region]:
    import tkinter as tk

    owns_root = parent is None
    root = parent if parent is not None else tk.Tk()
    if owns_root:
        root.withdraw()

    if only_monitor_index is not None:
        target_monitors = [m for m in monitors if m.index == only_monitor_index]
    else:
        target_monitors = list(monitors)

    picked: List[Region] = []
    for idx, mon in enumerate(target_monitors, start=1):
        rects = _pick_on_monitor(
            root, mon,
            current=idx,
            total=len(target_monitors),
            hint_suffix=hint_suffix,
        )
        for (x, y, w, h) in rects:
            picked.append(Region(
                monitor_index=mon.index, x=x, y=y, w=w, h=h,
                window_title_match=window_title_match,
            ))

    if owns_root:
        root.destroy()
    return picked


def _pick_on_monitor(
    parent,
    monitor: Monitor,
    current: int,
    total: int,
    hint_suffix: str = "",
) -> List[Tuple[int, int, int, int]]:
    import tkinter as tk

    rects: List[Tuple[int, int, int, int]] = []
    state = {"x0": None, "y0": None, "rid": None, "cancelled": False}

    win = tk.Toplevel(parent)
    win.overrideredirect(True)
    win.geometry(f"{monitor.width}x{monitor.height}+{monitor.left}+{monitor.top}")
    win.attributes("-alpha", 0.35)
    win.attributes("-topmost", True)
    win.configure(bg="black")

    canvas = tk.Canvas(win, bg="black", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)

    hint_base = (
        f"Monitor {monitor.index}  ({current}/{total})   "
        "drag = add rect   right-click = clear last   Enter = save   Esc = cancel"
    )
    hint = f"{hint_base}    [{hint_suffix}]" if hint_suffix else hint_base

    def redraw_header():
        canvas.delete("header")
        canvas.create_rectangle(0, 0, monitor.width, 40, fill="#111", outline="", tags="header")
        canvas.create_text(
            20, 20, text=hint, fill="#8fd", anchor="w",
            font=("Segoe UI", 13), tags="header",
        )

    redraw_header()

    def on_down(e):
        state["x0"] = e.x
        state["y0"] = e.y
        state["rid"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="lime", width=2)

    def on_drag(e):
        if state["rid"] is not None:
            canvas.coords(state["rid"], state["x0"], state["y0"], e.x, e.y)

    def on_up(e):
        if state["x0"] is None:
            return
        x0, y0 = state["x0"], state["y0"]
        x, y = min(x0, e.x), min(y0, e.y)
        w, h = abs(e.x - x0), abs(e.y - y0)
        state["x0"] = state["y0"] = state["rid"] = None
        if w < 10 or h < 10:
            return
        rects.append((x, y, w, h))
        canvas.create_text(
            x + 6, y + 6, text=f"#{len(rects)}", fill="lime",
            anchor="nw", font=("Segoe UI", 12, "bold"),
        )

    def clear_last(_):
        if rects:
            rects.pop()
            canvas.delete("all")
            redraw_header()
            for i, (x, y, w, h) in enumerate(rects, start=1):
                canvas.create_rectangle(x, y, x + w, y + h, outline="lime", width=2)
                canvas.create_text(
                    x + 6, y + 6, text=f"#{i}", fill="lime",
                    anchor="nw", font=("Segoe UI", 12, "bold"),
                )

    def confirm(_):
        win.destroy()

    def cancel(_):
        state["cancelled"] = True
        rects.clear()
        win.destroy()

    canvas.bind("<Button-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_up)
    canvas.bind("<Button-3>", clear_last)
    win.bind("<Return>", confirm)
    win.bind("<Escape>", cancel)
    win.after(100, lambda: win.focus_force())

    win.wait_window()
    return [] if state["cancelled"] else rects
