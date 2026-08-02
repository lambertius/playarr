"""Bounded in-memory telemetry for SQLite write transaction duration."""
from __future__ import annotations

import threading
import time
from collections import deque
_lock = threading.Lock()
_durations_ms: deque[float] = deque(maxlen=5000)
_slow: deque[dict] = deque(maxlen=100)


def begin(connection) -> None:
    connection.info.setdefault("_playarr_write_started", time.monotonic())


def finish(connection, outcome: str) -> None:
    started = connection.info.pop("_playarr_write_started", None)
    if started is None:
        return
    duration = round((time.monotonic() - started) * 1000, 3)
    with _lock:
        _durations_ms.append(duration)
        if duration >= 250:
            _slow.append({"duration_ms": duration, "outcome": outcome})


def stats() -> dict:
    with _lock:
        values = list(_durations_ms)
        slow = list(_slow)
    if not values:
        return {"count": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0, "slow": []}
    ordered = sorted(values)
    percentile = lambda p: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]
    return {
        "count": len(ordered),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "slow": slow[-20:],
    }
