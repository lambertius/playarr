"""
Centralised DB write queue for the URL import pipeline.

Every database write in the pipeline is funnelled through this module's
``db_write()`` or ``db_write_soon()`` helpers.  A single daemon thread
processes the queue serially, making SQLite write contention **impossible
by construction**.

Public API
----------
db_write(fn)      – Submit *fn*, block until it completes, return result.
                    Use for critical writes where the caller needs the
                    result (e.g. apply_mutation_plan returning video_id).
db_write_soon(fn) – Submit *fn*, return immediately (fire-and-forget).
                    Use for cosmetic / progress writes where the caller
                    does not need the result.
drain()           – Block until all pending writes have been processed.
                    Useful at shutdown or test boundaries.
pending()         – Return the approximate number of queued writes.
"""
import logging
import queue
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable

logger = logging.getLogger(__name__)
DEFAULT_MAX_PENDING = 256


class DBWriteBackpressure(RuntimeError):
    pass


class _DBWriteQueue:
    """Single-writer queue.  Thread-safe, lazy-started."""

    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=max_pending)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._coalesced: dict[object, tuple[Callable, float]] = {}
        self._rejected = 0
        self._coalesced_count = 0

    # ── lifecycle ────────────────────────────────────────────────────

    def _ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                t = threading.Thread(
                    target=self._run, daemon=True, name="db-write-queue",
                )
                t.start()
                self._thread = t
                logger.info("[db-write-queue] writer thread started")

    def _run(self) -> None:
        """Writer loop — processes one item at a time, forever."""
        from app.db_lock import _apply_lock
        while True:
            item = self._q.get()
            if item is None:  # shutdown sentinel
                self._q.task_done()
                break
            kind, payload, future, enqueued_at = item
            if kind == "coalesced":
                with self._lock:
                    latest = self._coalesced.pop(payload, None)
                if latest is None:
                    self._q.task_done()
                    continue
                fn, enqueued_at = latest
            else:
                fn = payload
            try:
                with _apply_lock:
                    result = fn()
                if future is not None:
                    future.set_result(result)
            except Exception as exc:
                if future is not None:
                    future.set_exception(exc)
                else:
                    logger.warning(
                        "[db-write-queue] fire-and-forget write failed: %s",
                        exc, exc_info=True,
                    )
            finally:
                self._q.task_done()

    # ── public helpers ───────────────────────────────────────────────

    def submit_sync(self, fn: Callable[[], Any]) -> Any:
        """Submit *fn*, block until the writer thread executes it,
        return its result (or re-raise its exception)."""
        self._ensure_started()
        future: Future = Future()
        try:
            self._q.put(("direct", fn, future, time.monotonic()), timeout=5)
        except queue.Full as exc:
            raise DBWriteBackpressure("database write queue remained full for 5 seconds") from exc
        return future.result()  # blocks until writer finishes fn

    def submit_async(self, fn: Callable[[], None], coalesce_key: object = None) -> bool:
        """Submit a bounded cosmetic write, coalescing repeated entity updates."""
        self._ensure_started()
        if coalesce_key is not None:
            with self._lock:
                if coalesce_key in self._coalesced:
                    self._coalesced[coalesce_key] = (fn, time.monotonic())
                    self._coalesced_count += 1
                    return True
                self._coalesced[coalesce_key] = (fn, time.monotonic())
            item = ("coalesced", coalesce_key, None, time.monotonic())
        else:
            item = ("direct", fn, None, time.monotonic())
        try:
            self._q.put_nowait(item)
            return True
        except queue.Full:
            with self._lock:
                if coalesce_key is not None:
                    self._coalesced.pop(coalesce_key, None)
                self._rejected += 1
            logger.warning("[db-write-queue] bounded queue full; cosmetic write rejected")
            return False

    def drain(self) -> None:
        """Block until every pending item has been processed."""
        self._ensure_started()
        self._q.join()

    def pending(self) -> int:
        return self._q.qsize()

    def stats(self) -> dict[str, float | int]:
        with self._q.mutex:
            times = [item[3] for item in self._q.queue if item is not None]
        with self._lock:
            rejected, coalesced = self._rejected, self._coalesced_count
        oldest = max(0.0, time.monotonic() - min(times)) if times else 0.0
        return {
            "pending": self._q.qsize(), "max_pending": self._q.maxsize,
            "oldest_age_seconds": round(oldest, 3),
            "rejected": rejected, "coalesced": coalesced,
        }


# ═══════════════════════════════════════════════════════════════════════
#  Module-level singleton + public API
# ═══════════════════════════════════════════════════════════════════════

_instance = _DBWriteQueue()


def db_write(fn: Callable[[], Any]) -> Any:
    """Submit a DB write, **block** until done, return the result of *fn*.

    Use for writes where the caller depends on the outcome
    (e.g. ``video_id = db_write(lambda: _execute_plan(plan))``).
    """
    return _instance.submit_sync(fn)


def db_write_soon(fn: Callable[[], None], *, coalesce_key: object = None) -> bool:
    """Submit a cosmetic / progress DB write, return **immediately**.

    The write will be executed as soon as the writer thread is free.
    Use for progress-percent updates, step labels, display-name changes.
    """
    return _instance.submit_async(fn, coalesce_key)


def drain() -> None:
    """Block until every pending write has been processed."""
    _instance.drain()


def pending() -> int:
    """Approximate number of writes waiting in the queue."""
    return _instance.pending()


def stats() -> dict[str, float | int]:
    """Bound, pending age, rejection and coalescing health for diagnostics."""
    return _instance.stats()
