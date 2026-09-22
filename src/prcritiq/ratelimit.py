"""In-process sliding-window rate limit for the public demo.

One process serves the deployed demo (ADR-019), so the window lives in memory.
A restart forgets it, which costs at most one extra window of requests. A
second replica would need a shared store, and that is the point to add one.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from threading import Lock

#: Past this many tracked clients, idle ones are pruned so a scan of spoofed
#: or rotating addresses cannot grow the table without bound.
_PRUNE_THRESHOLD = 1024


class SlidingWindowLimiter:
    """Allow at most `limit` hits per key within any `window_seconds` span."""

    def __init__(
        self,
        limit: int,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("A rate limit must allow at least one request per window")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def acquire(self, key: str) -> int | None:
        """Record a hit and return None, or return whole seconds until one is allowed."""

        now = self._clock()
        horizon = now - self._window
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= horizon:
                hits.popleft()
            if len(hits) >= self._limit:
                return max(1, math.ceil(hits[0] - horizon))
            hits.append(now)
            if len(self._hits) > _PRUNE_THRESHOLD:
                self._prune(horizon)
        return None

    def _prune(self, horizon: float) -> None:
        idle = [key for key, hits in self._hits.items() if not hits or hits[-1] <= horizon]
        for key in idle:
            del self._hits[key]
