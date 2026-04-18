"""Fullscreen translucent overlay for drawing monitored regions.

Must be called on the main thread — Tk doesn't like being driven from threads.
"""
from __future__ import annotations

from typing import List, Tuple

from .capture import Monitor
from .regions import Region


def pick_regions(monitors: List[Monitor]) -> List[Region]:
    """Walk monitors one at a time, collecting drawn rectangles. Returns [] on Esc."""
    picked: List[Region] = []
    for idx, mon in enumerate(monitors, start=1):
        rects = _pick_on_monitor(mon, current=idx, total=len(monitors))
        for (x, y, w, h) in rects:
            picked.append(Region(monitor_index=mon.index, x=x, y=y, w=w, h=h))
    return picked


def _pick_on_monitor(monitor: Monitor, current: int, total: int) -> List[Tuple[int, int, int, int]]:
    import tkinter as tk

    rects: List[Tuple[int, int, int, int]] = []
    state = {"x0": None, "y0": None, "rid": None, "cancelled": False}

    root = tk.Tk()
    root.title("autoclicker — set regions")
    root.overrideredirect(True)
    root.geometry(f"{monitor.width}x{monitor.height}+{monitor.left}+{monitor.top}")
    root.attributes("-alpha", 0.35)
    root.attributes("-topmost", True)
    root.configure(bg="black")

    canvas = tk.Canvas(root, bg="black", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)

    hint = (
        f"Monitor {monitor.index}  ({current}/{total})   "
        "drag = add rect   right-click = clear last   Enter = save   Esc = cancel"
    )
    canvas.create_rectangle(0, 0, monitor.width, 40, fill="#111", outline="")
    canvas.create_text(
        20, 20, text=hint, fill="#8fd", anchor="w", font=("Segoe UI", 13),
    )

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
            x + 6, y + 6,
            text=f"#{len(rects)}",
            fill="lime", anchor="nw",
            font=("Segoe UI", 12, "bold"),
        )

    def clear_last(_):
        if rects:
            rects.pop()
            canvas.delete("all")
            canvas.create_rectangle(0, 0, monitor.width, 40, fill="#111", outline="")
            canvas.create_text(20, 20, text=hint, fill="#8fd", anchor="w", font=("Segoe UI", 13))
            for i, (x, y, w, h) in enumerate(rects, start=1):
                canvas.create_rectangle(x, y, x + w, y + h, outline="lime", width=2)
                canvas.create_text(x + 6, y + 6, text=f"#{i}", fill="lime", anchor="nw", font=("Segoe UI", 12, "bold"))

    def confirm(_):
        root.destroy()

    def cancel(_):
        state["cancelled"] = True
        rects.clear()
        root.destroy()

    canvas.bind("<Button-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_up)
    canvas.bind("<Button-3>", clear_last)
    root.bind("<Return>", confirm)
    root.bind("<Escape>", cancel)
    root.after(100, lambda: root.focus_force())

    root.mainloop()
    return [] if state["cancelled"] else rects
