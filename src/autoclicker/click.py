import time
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class ClickResult:
    clicked: bool
    reason: str


class Clicker:
    """Thin wrapper around pyautogui with user-activity and rate guards."""

    def __init__(self, activity_radius_px: int = 50) -> None:
        import pyautogui

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        self._pg = pyautogui
        self._activity_radius = activity_radius_px
        self._last_observed_pos: Optional[Tuple[int, int]] = None
        self._last_observed_at: float = 0.0

    def _user_is_moving(self) -> bool:
        pos = tuple(self._pg.position())
        now = time.monotonic()
        moved = False
        if self._last_observed_pos is not None and (now - self._last_observed_at) < 1.0:
            dx = pos[0] - self._last_observed_pos[0]
            dy = pos[1] - self._last_observed_pos[1]
            if (dx * dx + dy * dy) ** 0.5 > self._activity_radius:
                moved = True
        self._last_observed_pos = pos
        self._last_observed_at = now
        return moved

    def click(self, x: int, y: int) -> ClickResult:
        if self._user_is_moving():
            return ClickResult(False, "user moving mouse")
        origin = tuple(self._pg.position())
        try:
            self._pg.moveTo(x, y, duration=0.05)
            self._pg.click(x, y)
        except Exception as exc:  # noqa: BLE001
            return ClickResult(False, f"click failed: {exc}")
        try:
            self._pg.moveTo(origin[0], origin[1], duration=0.05)
        except Exception:
            pass
        return ClickResult(True, f"clicked ({x},{y})")
