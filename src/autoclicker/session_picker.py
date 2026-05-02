"""Configure a per-window working session: goals + chat-input click target.

Flow:
  1. Pick a window from the list of windows you've already added regions
     for. (You must run "Add window region" first — sessions only make
     sense on a window the autoclicker is already watching.)
  2. Type the goals in a multi-line text box (one per line).
  3. Click on the assistant's chat-input field — recorded as the
     screen coords the autoclicker will click before pasting messages.
"""
from __future__ import annotations

import time
from typing import List, Optional

from .sessions import WindowSession
from .window import WindowInfo, bring_to_front, find_window


def configure_session(
    parent=None,
    available_matches: Optional[List[str]] = None,
    existing_sessions: Optional[List[WindowSession]] = None,
) -> Optional[WindowSession]:
    """Open the session config dialog.

    ``available_matches`` is the list of ``window_title_match`` strings
    that already have at least one monitored region. The user picks one
    of those. ``existing_sessions`` lets the dialog pre-fill the goals
    + chat-input position when re-editing an already-configured session.
    """
    import tkinter as tk

    owns_root = parent is None
    if owns_root:
        root = tk.Tk()
        root.withdraw()
    else:
        root = parent

    available_matches = list(available_matches or [])
    if not available_matches:
        _show_error(
            root,
            "No window regions yet",
            "Use 'Add window region' first to bind one or more regions to a window. "
            "Sessions monitor change inside those regions, so a session without "
            "regions has nothing to look at.",
        )
        if owns_root:
            root.destroy()
        return None

    existing_by_match = {
        s.title_match.lower(): s for s in (existing_sessions or [])
    }

    title_match = _pick_match(root, available_matches, existing_by_match)
    if title_match is None:
        if owns_root:
            root.destroy()
        return None
    existing = existing_by_match.get(title_match.lower())

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


def _pick_match(
    parent,
    matches: List[str],
    existing_by_match: dict,
) -> Optional[str]:
    """List the windows the autoclicker is already watching."""
    import tkinter as tk
    from tkinter import ttk

    dlg = tk.Toplevel(parent)
    dlg.title("Configure window session")
    dlg.geometry("560x340")
    dlg.attributes("-topmost", True)
    dlg.transient(parent)
    dlg.grab_set()

    pad = {"padx": 10, "pady": 6}
    tk.Label(
        dlg,
        text=(
            "Pick a window to attach a session to.\n"
            "(Only windows that already have monitored regions are listed.\n"
            "Items shown in green already have a session — re-pick to edit it.)"
        ),
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

    for m in matches:
        sess = existing_by_match.get(m.lower())
        if sess is None:
            label = m
        else:
            tag = "✓ done" if sess.completed else "● configured"
            label = f"{m}    [{tag} — {len(sess.goals)} goal(s)]"
        listbox.insert("end", label)
        # Color rows that already have a session.
        if sess is not None:
            color = "#ff9933" if sess.completed else "#37b24d"
            listbox.itemconfig("end", foreground=color)
    listbox.selection_set(0)

    chosen: dict = {"value": None}

    def on_ok():
        sel = listbox.curselection()
        if not sel:
            return
        chosen["value"] = matches[sel[0]]
        dlg.destroy()

    def on_cancel():
        dlg.destroy()

    btns = tk.Frame(dlg)
    btns.pack(fill="x", **pad, side="bottom")
    ttk.Button(btns, text="Cancel", command=on_cancel, width=10).pack(side="right", padx=4)
    ttk.Button(btns, text="Next ▶", command=on_ok).pack(side="right")
    dlg.bind("<Return>", lambda _e: on_ok())
    dlg.bind("<Escape>", lambda _e: on_cancel())
    listbox.focus_set()

    dlg.wait_window()
    return chosen["value"]


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
