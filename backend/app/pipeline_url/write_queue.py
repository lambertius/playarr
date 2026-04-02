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
from concurrent.futures import Future
from typing import Any, Callable

logger = logging.getLogger(__name__)


class _DBWriteQueue:
    """Single-writer queue.  Thread-safe, lazy-started."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

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
        while True:
            item = self._q.get()
            if item is None:  # shutdown sentinel
                self._q.task_done()
                break
            fn, future = item
            try:
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
        self._q.put((fn, future))
        return future.result()  # blocks until writer finishes fn

    def submit_async(self, fn: Callable[[], None]) -> None:
        """Submit *fn* for fire-and-forget execution."""
        self._ensure_started()
        self._q.put((fn, None))

    def drain(self) -> None:
        """Block until every pending item has been processed."""
        self._ensure_started()
        self._q.join()

    def pending(self) -> int:
        return self._q.qsize()


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


def db_write_soon(fn: Callable[[], None]) -> None:
    """Submit a cosmetic / progress DB write, return **immediately**.

    The write will be executed as soon as the writer thread is free.
    Use for progress-percent updates, step labels, display-name changes.
    """
    _instance.submit_async(fn)


def drain() -> None:
    """Block until every pending write has been processed."""
    _instance.drain()


def pending() -> int:
    """Approximate number of writes waiting in the queue."""
    return _instance.pending()
