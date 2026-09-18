"""
http_utils.py

Shared utilities used by every real (non-stub) tool implementation from
Day 5 onward:

  1. RateLimiter -- a simple sleep-based throttle that enforces a minimum
     interval between calls to a given external API, so this project stays
     a polite, well-behaved client of free/rate-limited services (SEC
     EDGAR's own guidance: max 10 req/sec with a descriptive User-Agent;
     most free-tier APIs are far stricter than that).
  2. ttl_cache -- a lightweight in-memory cache decorator with a
     time-to-live, so repeated calls for the same ticker within a research
     session don't re-hit the network. This is a simple stand-in for the
     "vector_db_search before external API call" memory-check pattern
     described in architecture_specification.md Section 5.4 -- Day 6
     replaces this in-memory cache with the real long-term vector memory.

Both are dependency-free (standard library only) so they carry no risk of
breaking in any environment.
"""

from __future__ import annotations

import functools
import threading
import time
from typing import Any, Callable, Dict, Tuple


class RateLimiter:
    """
    Enforces a minimum time interval between successive calls. Thread-safe
    via a simple lock, since a future async/concurrent Executor (per the
    "parallelize independent tool calls" lesson) might call this from
    multiple threads at once.

    Usage:
        limiter = RateLimiter(min_interval_seconds=0.2)  # max 5 req/sec
        limiter.wait()
        # ... make the actual API call ...
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._last_call_time: float = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Block until enough time has passed since the last call."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call_time
            remaining = self.min_interval_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
            self._last_call_time = time.monotonic()


class TTLCache:
    """A minimal in-memory cache with per-entry expiry."""

    def __init__(self) -> None:
        self._store: Dict[Any, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: Any, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + ttl_seconds, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


def ttl_cache(ttl_seconds: float = 3600, cache: TTLCache | None = None) -> Callable:
    """
    Decorator: cache a function's return value for `ttl_seconds`, keyed on
    its arguments. Use a shared `cache` instance across calls if you want
    module-level caching (the default -- a fresh cache per decoration --
    is usually what you want for a single tool function).
    """
    _cache = cache if cache is not None else TTLCache()

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = (args, tuple(sorted(kwargs.items())))
            cached_value = _cache.get(key)
            if cached_value is not None:
                return cached_value
            result = func(*args, **kwargs)
            _cache.set(key, result, ttl_seconds)
            return result

        wrapper.cache = _cache  # exposed for tests / manual clearing
        return wrapper

    return decorator
