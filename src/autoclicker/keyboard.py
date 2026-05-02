"""Type-and-submit a text into whatever input has focus.

We go through the OS clipboard (Ctrl+V) instead of ``pyautogui.typewrite``
because the latter is ASCII-only — French sentences with accents would
come out mangled. Brief sleeps between actions give the target window
time to process the keystrokes; without them, Claude Code / Codex chat
inputs occasionally drop characters or submit before the paste lands.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class TypeResult:
    sent: bool
    reason: str


def type_and_submit(
    text: str,
    settle_before_s: float = 0.10,
    settle_after_paste_s: float = 0.15,
) -> TypeResult:
    """Paste ``text`` into the focused control and press Enter."""
    if not text.strip():
        return TypeResult(False, "empty text")
    try:
        import pyperclip
        import pyautogui
    except Exception as exc:  # noqa: BLE001
        return TypeResult(False, f"missing dependency: {exc}")

    try:
        pyperclip.copy(text)
    except Exception as exc:  # noqa: BLE001
        return TypeResult(False, f"clipboard copy failed: {exc}")

    if settle_before_s > 0:
        time.sleep(settle_before_s)
    try:
        pyautogui.hotkey("ctrl", "v")
    except Exception as exc:  # noqa: BLE001
        return TypeResult(False, f"paste failed: {exc}")

    if settle_after_paste_s > 0:
        time.sleep(settle_after_paste_s)
    try:
        pyautogui.press("enter")
    except Exception as exc:  # noqa: BLE001
        return TypeResult(False, f"enter failed: {exc}")

    return TypeResult(True, f"sent {len(text)} chars")
