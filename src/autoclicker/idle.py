"""Per-window text-change tracker.

The autoclicker hashes the OCR text of each monitored window every tick.
When the hash stays the same for longer than the session's
``idle_threshold_s`` (and we're past the post-action cooldown), the
worker asks OpenAI whether the AI assistant has finished its task.
"""
import hashlib
import time
from dataclasses import dataclass
from typing import Dict, Optional


def _hash(text: str) -> str:
    h = hashlib.sha256()
    h.update(text.strip().encode("utf-8", errors="replace"))
    return h.hexdigest()


@dataclass
class WindowIdleState:
    title_match: str
    last_text_hash: Optional[str] = None
    last_change_at: float = 0.0
    last_action_at: float = 0.0

    def observe(self, text: str, now: Optional[float] = None) -> bool:
        """Record an observation. Returns True if the text changed."""
        if now is None:
            now = time.monotonic()
        h = _hash(text)
        if h != self.last_text_hash:
            self.last_text_hash = h
            self.last_change_at = now
            return True
        return False

    def idle_for(self, now: Optional[float] = None) -> float:
        if self.last_text_hash is None:
            return 0.0
        if now is None:
            now = time.monotonic()
        return max(0.0, now - self.last_change_at)

    def can_act(self, cooldown_s: float, now: Optional[float] = None) -> bool:
        if self.last_action_at == 0.0:
            return True
        if now is None:
            now = time.monotonic()
        return (now - self.last_action_at) >= cooldown_s

    def mark_acted(self, now: Optional[float] = None) -> None:
        if now is None:
            now = time.monotonic()
        self.last_action_at = now
        # Reset the change clock so we don't immediately re-trigger when
        # the AI's response shows up in the next OCR pass.
        self.last_change_at = now


class IdleRegistry:
    """Holds one ``WindowIdleState`` per session, keyed by title_match."""

    def __init__(self) -> None:
        self._by_match: Dict[str, WindowIdleState] = {}

    def get(self, title_match: str) -> WindowIdleState:
        st = self._by_match.get(title_match)
        if st is None:
            st = WindowIdleState(title_match=title_match)
            self._by_match[title_match] = st
        return st

    def forget(self, title_match: str) -> None:
        self._by_match.pop(title_match, None)
