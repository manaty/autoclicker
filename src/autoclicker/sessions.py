"""Per-window working session: a list of objectives + a chat-input click target.

Each session is matched to a window by ``title_match`` (case-insensitive
substring) — the same field used for window-bound regions.

Lifecycle:
  - ``completed=False`` → autoclicker watches for idleness. When the
    window has been static longer than ``idle_threshold_s``, it asks
    OpenAI whether the AI has finished the goals; depending on the
    verdict it either pings ``"Continue la tâche."`` or asks
    ``"as-tu tout terminé d'implémenter ?"`` (then sets ``completed``).
  - ``completed=True`` → session is parked. The overlay rectangles for
    its regions render in orange. Re-edit the goals to reactivate.
"""
from typing import List

from pydantic import BaseModel, Field


class WindowSession(BaseModel):
    title_match: str
    goals: List[str] = Field(default_factory=list)
    prompt_input_x: int
    prompt_input_y: int
    idle_threshold_s: float = Field(default=60.0, ge=10.0)
    cooldown_s: float = Field(default=300.0, ge=10.0)
    completed: bool = False

    def matches(self, title: str) -> bool:
        if not self.title_match:
            return False
        return self.title_match.lower() in (title or "").lower()
