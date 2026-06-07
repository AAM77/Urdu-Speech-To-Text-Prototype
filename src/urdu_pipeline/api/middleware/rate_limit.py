"""Simple fixed-window rate limiting.

``RateLimiter`` is the protocol; ``InMemoryRateLimiter`` is the default
implementation (suitable for a single-process deployment or tests).

In a multi-process production deployment this should be swapped for a
Redis-backed implementation so counts are shared across workers.

Usage
─────
Inject a ``RateLimiter`` via ``AppState.login_rate_limiter`` and call
``check_and_increment(key)`` in a FastAPI dependency.  Raise HTTP 429 when
the method returns ``False``.

The ``key`` is conventionally ``"{route}:{client_ip}"`` for anonymous
limiters or ``"{route}:{user_id}"`` for per-user limiters.
"""

from __future__ import annotations

import time
from typing import Protocol


class RateLimiter(Protocol):
    """Minimal interface for a token-bucket / fixed-window rate limiter."""

    def check_and_increment(self, key: str) -> bool:
        """Return ``True`` if the request is within the limit, ``False`` otherwise.

        A return of ``True`` also counts the request; callers should not call
        this twice for the same request.
        """
        ...


class InMemoryRateLimiter:
    """Fixed-window, in-memory rate limiter.

    Parameters
    ──────────
    limit:          Maximum number of requests allowed per window.
    window_seconds: Duration of a single counting window in seconds.

    Thread safety: not guaranteed — acceptable for single-threaded test use.
    Production usage should use a Redis-backed implementation with atomic ops.
    """

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        # key → (count, window_start_monotonic)
        self._counts: dict[str, tuple[int, float]] = {}

    def check_and_increment(self, key: str) -> bool:
        now = time.monotonic()
        count, window_start = self._counts.get(key, (0, now))

        if now - window_start >= self._window:
            # Window has expired — start a fresh one
            count, window_start = 0, now

        if count >= self._limit:
            # Store the current (exhausted) state without incrementing
            self._counts[key] = (count, window_start)
            return False

        self._counts[key] = (count + 1, window_start)
        return True


__all__ = ["InMemoryRateLimiter", "RateLimiter"]
