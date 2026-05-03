"""Per-key rolling log buffer feeding the region-header news tickers.

Each region's header polls :meth:`LogTicker.text_for` to redraw its
scrolling banner. Keys are typically the region's
``window_title_match`` — that way the ticker on each window only shows
events relevant to that window, while regions without a match fall
back to the global stream.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Optional, Tuple


_GLOBAL_KEY = ""


class LogTicker:
    def __init__(self, max_per_key: int = 8, max_global: int = 16) -> None:
        self._lock = threading.Lock()
        self._per_key: dict[str, Deque[Tuple[float, str]]] = {}
        self._global: Deque[Tuple[float, str]] = deque(maxlen=max_global)
        self._max_per_key = max_per_key

    def add(self, msg: str, key: Optional[str] = None) -> None:
        msg = msg.strip()
        if not msg:
            return
        ts = time.time()
        with self._lock:
            self._global.append((ts, msg))
            if key:
                buf = self._per_key.get(key)
                if buf is None:
                    buf = deque(maxlen=self._max_per_key)
                    self._per_key[key] = buf
                buf.append((ts, msg))

    def text_for(
        self,
        key: Optional[str] = None,
        separator: str = "   •   ",
        fallback_global: bool = True,
    ) -> str:
        """Return a single string suitable for marquee display."""
        with self._lock:
            buf: Optional[Deque[Tuple[float, str]]] = None
            if key:
                buf = self._per_key.get(key)
            if buf is None or not buf:
                if fallback_global:
                    buf = self._global
                else:
                    return ""
            return separator.join(msg for _ts, msg in list(buf))

    def clear(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._per_key.clear()
                self._global.clear()
            else:
                self._per_key.pop(key, None)
