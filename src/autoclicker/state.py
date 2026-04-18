import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional


def _hash(command: str, monitor_index: int) -> str:
    h = hashlib.sha256()
    h.update(str(monitor_index).encode())
    h.update(b"\x00")
    h.update(command.strip().encode("utf-8", errors="replace"))
    return h.hexdigest()


@dataclass
class DedupCache:
    window_s: float = 5.0
    max_entries: int = 64

    def __post_init__(self) -> None:
        self._seen: "OrderedDict[str, float]" = OrderedDict()

    def seen_recently(self, command: str, monitor_index: int) -> bool:
        key = _hash(command, monitor_index)
        now = time.monotonic()
        self._evict(now)
        ts = self._seen.get(key)
        if ts is not None and now - ts < self.window_s:
            return True
        self._seen[key] = now
        self._seen.move_to_end(key)
        if len(self._seen) > self.max_entries:
            self._seen.popitem(last=False)
        return False

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_s
        stale = [k for k, ts in self._seen.items() if ts < cutoff]
        for k in stale:
            del self._seen[k]


@dataclass
class Cooldown:
    interval_s: float

    def __post_init__(self) -> None:
        self._last: Optional[float] = None

    def ready(self) -> bool:
        now = time.monotonic()
        if self._last is None or now - self._last >= self.interval_s:
            return True
        return False

    def trigger(self) -> None:
        self._last = time.monotonic()
