"""Simple in-process rate limiter for auth endpoints."""

from __future__ import annotations

import time
from collections import defaultdict

_hits: dict[str, list[float]] = defaultdict(list)


def allow(key: str, *, limit: int = 10, window_seconds: float = 60.0) -> bool:
    """Return True if the request is allowed; False if rate-limited."""
    now = time.monotonic()
    bucket = _hits[key]
    # Drop expired timestamps
    kept = [t for t in bucket if now - t < window_seconds]
    if len(kept) >= limit:
        _hits[key] = kept
        return False
    kept.append(now)
    _hits[key] = kept
    return True
