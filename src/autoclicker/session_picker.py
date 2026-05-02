"""Configure a per-window working session: goals + chat-input click target.

Three steps in one Tk dialog flow:
  1. Pick the window (reuses :func:`window_picker.pick_window`).
  2. Type the goals in a multi-line text box (one per line).
  3. Click on the assistant's chat-input field — recorded as the
     screen coords the autoclicker will click before pasting messages.
"""
from __future__ import annotations

import time
from typing import List, Optional

from .sessions import WindowSession
from .window import WindowInfo, bring_to_front
from .window_picker import pick_window


def configure_session(
    parent=None,
    existing: Optional[WindowSession] = None,
) -> Optional[WindowSession]:
    import tkinter as tk
    from tkinter import ttk

    owns_root = parent is None
    if owns_root:
        root = tk.Tk()
        root.withdraw()
    else:
        root = parent

    window: Optional[WindowInfo] = None
    title_match: Optional[str] = None

    if existing is not None:
        title_match = existing.title_match
    else:
        picked = pick_window(parent=root)
        if picked is None:
            if owns_root:
                root.destroy()
            return None
        window, title_match = picked

    goals_text, idle_s, cooldown_s = _edit_goals(
        root,
        title_match=title_match,
        initial_goals=existing.goals if existing else [],
        initial_idle=existing.idle_threshold_s if existing else 60.0,
        initial_cooldown=existing.cooldown_s if existing else 300.0,
    )
    if goals_text is None:
        if owns_root:
            root.destroy()
        return None

    goals = [line.strip() for line in goals_text.splitlines() if line.strip()]

    if existing is not None and not _ask_yes_no(
        root,
        "Re-pick chat input?",
        "Click 'Yes' to re-capture the chat-input position, 'No' to keep it.",
    ):
        coords = (existing.prompt_input_x, existing.prompt_input_y)
    else:
        if window is None:
            # Existing session re-edit: find the window by its match.
            from .window import find_window
            window = find_window(title_match)
            if window is None:
                _show_error(root, "Window not found", f"No visible window matched {title_match!r}.")
                if owns_root:
                    root.destroy()
                return None
        try:
            bring_to_front(window.hwnd)
        except Exception:
            pass
        coords = _capture_click(root, window)
        if coords is None:
            if owns_root:
                root.destroy()
            return None

    session = WindowSession(
        title_match=title_match,
        goals=goals,
        prompt_input_x=coords[0],
        prompt_input_y=coords[1],
        idle_threshold_s=idle_s,
        cooldown_s=cooldown_s,
        completed=False,  # editing the goals always re-arms the session.
    )

    if owns_root:
        root.destroy()
    return session


def _edit_goals(
    parent,
    title_match: str,
    initial_goals: List[str],
    initial_idle: float,
    initial_cooldown: float,
):
    import tkinter as tk
    from tkinter import ttk

    dlg = tk.Toplevel(parent)
    dlg.title(f"Goals — {title_match}")
    dlg.geometry("520x420")
    dlg.attributes("-topmost", True)
    dlg.transient(parent)
    dlg.grab_set()

    pad = {"padx": 10, "pady": 6}
    tk.Label(
        dlg,
        text=f"Session goals for {title_match!r}\n(one per line — keep them short and actionable):",
        anchor="w", justify="left", wraplength=500,
    ).pack(fill="x", **pad)

    text = tk.Text(dlg, height=12, font=("Segoe UI", 10), wrap="word")
    text.pack(fill="both", expand=True, padx=10)
    if initial_goals:
        text.insert("1.0", "\n".join(initial_goals))

    timing = tk.Frame(dlg)
    timing.pack(fill="x", **pad)
    tk.Label(timing, text="Idle threshold (s):").grid(row=0, column=0, sticky="w")
    idle_var = tk.StringVar(value=str(int(initial_idle)))
    ttk.Entry(timing, textvariable=idle_var, width=6).grid(row=0, column=1, padx=(4, 16))
    tk.Label(timing, text="Cooldown after action (s):").grid(row=0, column=2, sticky="w")
    cd_var = tk.StringVar(value=str(int(initial_cooldown)))
    ttk.Entry(timing, textvariable=cd_var, width=6).grid(row=0, column=3, padx=4)

    result = {"text": None, "idle": initial_idle, "cooldown": initial_cooldown}

    def on_ok():
        try:
            idle = float(idle_var.get())
            cooldown = float(cd_var.get())
        except ValueError:
            return
        if idle < 10 or cooldown < 10:
            return
        result["text"] = text.get("1.0", "end-1c")
        result["idle"] = idle
        result["cooldown"] = cooldown
        dlg.destroy()

    def on_cancel():
        dlg.destroy()

    btns = tk.Frame(dlg)
    btns.pack(fill="x", **pad, side="bottom")
    ttk.Button(btns, text="Cancel", command=on_cancel, width=10).pack(side="right", padx=4)
    ttk.Button(btns, text="Next: Pick chat input ▶", command=on_ok).pack(side="right")

    text.focus_set()
    dlg.wait_window()
    return result["text"], result["idle"], result["cooldown"]


def _capture_click(parent, window: WindowInfo) -> Optional[tuple]:
    """Show a translucent overlay over the window; user clicks the chat input."""
    import tkinter as tk

    monitor_left = window.left
    monitor_top = window.top
    width = max(1, window.width)
    height = max(1, window.height)

    win = tk.Toplevel(parent)
    win.overrideredirect(True)
    win.geometry(f"{width}x{height}+{monitor_left}+{monitor_top}")
    win.attributes("-alpha", 0.30)
    win.attributes("-topmost", True)
    win.configure(bg="black")

    canvas = tk.Canvas(win, bg="black", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        width // 2, 30,
        text="Click on the AI chat input box (where you'd type your prompt)",
        fill="#8fd", font=("Segoe UI", 14, "bold"),
    )
    canvas.create_text(
        width // 2, 60,
        text="Esc = cancel",
        fill="#aaa", font=("Segoe UI", 11),
    )

    captured: dict = {"x": None, "y": None, "cancelled": False}

    def on_click(e):
        captured["x"] = monitor_left + e.x
        captured["y"] = monitor_top + e.y
        win.destroy()

    def on_cancel(_):
        captured["cancelled"] = True
        win.destroy()

    canvas.bind("<Button-1>", on_click)
    win.bind("<Escape>", on_cancel)
    win.after(150, lambda: win.focus_force())
    win.wait_window()

    if captured["cancelled"] or captured["x"] is None:
        return None
    return (captured["x"], captured["y"])


def _ask_yes_no(parent, title: str, prompt: str) -> bool:
    from tkinter import messagebox
    return bool(messagebox.askyesno(title, prompt, parent=parent))


def _show_error(parent, title: str, prompt: str) -> None:
    from tkinter import messagebox
    messagebox.showerror(title, prompt, parent=parent)
