"""
Global database write lock.

ALL database write operations — regardless of which pipeline or request they
originate from — serialise through this single lock.  This makes SQLite write
contention impossible by construction.

Usage patterns:
  * ``pipeline_url``:  The write-queue daemon thread acquires this lock
    around every queued write function.
  * ``pipeline`` / ``pipeline_lib``:  Deferred tasks and ``apply_mutation_plan``
    acquire this lock directly via ``with _apply_lock:``.
  * Interactive HTTP requests:  The request-scoped write-serialisation guard in
    ``app.database`` acquires this lock automatically on the first write
    statement of a transaction and releases it on commit/rollback, so router
    endpoints that commit on the request session no longer collide with the
    write-queue daemon at the SQLite level.

This is a **re-entrant** lock (``RLock``).  Re-entrancy is required because some
request handlers (e.g. ``routers/jobs.py``) run on the guarded request session
*and* still wrap their commit in an explicit ``with _apply_lock:``.  The ``with``
acquires the lock once; the subsequent flush/commit then re-acquires it a second
time on the same thread via the request-guard events in ``app.database``.  A
plain ``Lock`` would self-deadlock in that case; ``RLock`` lets the same thread
re-enter while still blocking other threads.
"""
import threading

_apply_lock = threading.RLock()
