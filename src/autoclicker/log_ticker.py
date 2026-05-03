"""Per-key rolling log buffer feeding the region-header marquees and
the per-region log panels.

Each region's header polls :meth:`LogTicker.text_for` to redraw its
scrolling banner. Keys are typically the region's
``window_title_match`` (so all regions of the same window share a
stream) or a synthetic ``region_<idx>`` key for unbound regions.

Each per-key buffer is capped by *bytes* — roughly 10 KB per region —
so the panels stay snappy and the memory footprint stays bounded
even on long runs.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, List, Optional, Tuple


_DEFAULT_PER_KEY_BYTES = 10 * 1024  # 10 KB per region
_DEFAULT_GLOBAL_BYTES = 64 * 1024


def _entry_bytes(msg: str) -> int:
    # Rough byte size; we don't need to be exact.
    return len(msg.encode("utf-8", errors="replace")) + 32  # +overhead per entry


class _Buffer:
    __slots__ = ("entries", "size", "cap")

    def __init__(self, cap: int) -> None:
        self.entries: Deque[Tuple[float, str]] = deque()
        self.size: int = 0
        self.cap: int = cap

    def append(self, ts: float, msg: str) -> None:
        b = _entry_bytes(msg)
        self.entries.append((ts, msg))
        self.size += b
        while self.size > self.cap and self.entries:
            old_ts, old_msg = self.entries.popleft()
            self.size -= _entry_bytes(old_msg)
            if self.size < 0:
                self.size = 0


class LogTicker:
    def __init__(
        self,
        max_per_key_bytes: int = _DEFAULT_PER_KEY_BYTES,
        max_global_bytes: int = _DEFAULT_GLOBAL_BYTES,
    ) -> None:
        self._lock = threading.Lock()
        self._per_key: dict[str, _Buffer] = {}
        self._global: _Buffer = _Buffer(max_global_bytes)
        self._max_per_key_bytes = max_per_key_bytes

    def add(self, msg: str, key: Optional[str] = None) -> None:
        msg = msg.strip()
        if not msg:
            return
        ts = time.time()
        with self._lock:
            self._global.append(ts, msg)
            if key:
                buf = self._per_key.get(key)
                if buf is None:
                    buf = _Buffer(self._max_per_key_bytes)
                    self._per_key[key] = buf
                buf.append(ts, msg)

    def text_for(
        self,
        key: Optional[str] = None,
        separator: str = "   •   ",
        fallback_global: bool = True,
    ) -> str:
        """Joined string of the buffer entries, ordered oldest → newest."""
        with self._lock:
            buf: Optional[_Buffer] = None
            if key:
                buf = self._per_key.get(key)
            if buf is None or not buf.entries:
                if fallback_global:
                    buf = self._global
                else:
                    return ""
            return separator.join(msg for _ts, msg in list(buf.entries))

    def lines_for(
        self,
        key: Optional[str] = None,
        fallback_global: bool = True,
    ) -> List[Tuple[float, str]]:
        """Raw timestamped entries — used by the per-region log panel."""
        with self._lock:
            buf: Optional[_Buffer] = None
            if key:
                buf = self._per_key.get(key)
            if buf is None or not buf.entries:
                if fallback_global:
                    buf = self._global
                else:
                    return []
            return list(buf.entries)

    def size_for(self, key: Optional[str] = None) -> int:
        with self._lock:
            if key is None:
                return self._global.size
            buf = self._per_key.get(key)
            return buf.size if buf else 0

    def clear(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._per_key.clear()
                self._global.entries.clear()
                self._global.size = 0
            else:
                self._per_key.pop(key, None)
