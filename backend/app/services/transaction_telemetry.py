"""Bounded in-memory telemetry for SQLite write transaction duration."""
from __future__ import annotations

import threading
import time
from collections import deque
_lock = threading.Lock()
_durations_ms: deque[float] = deque(maxlen=5000)
_waits_ms: deque[float] = deque(maxlen=5000)
_slow: deque[dict] = deque(maxlen=100)


def record_wait(duration_ms: float) -> None:
    with _lock:
        _waits_ms.append(round(max(0.0, duration_ms), 3))


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
        waits = list(_waits_ms)
        slow = list(_slow)
    ordered = sorted(values)
    ordered_waits = sorted(waits)
    def percentile(items: list[float], p: float) -> float:
        return items[min(len(items) - 1, int((len(items) - 1) * p))] if items else 0
    return {
        "count": len(ordered),
        "p50_ms": percentile(ordered, 0.50),
        "p95_ms": percentile(ordered, 0.95),
        "p99_ms": percentile(ordered, 0.99),
        "wait_count": len(ordered_waits),
        "wait_p50_ms": percentile(ordered_waits, 0.50),
        "wait_p95_ms": percentile(ordered_waits, 0.95),
        "wait_p99_ms": percentile(ordered_waits, 0.99),
        "slow": slow[-20:],
    }
