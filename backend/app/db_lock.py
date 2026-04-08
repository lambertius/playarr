"""
Global database write lock.

ALL database write operations — regardless of which pipeline they originate
from — must serialise through this single lock.  This makes SQLite write
contention impossible by construction.

Usage patterns:
  * ``pipeline_url``:  The write-queue daemon thread acquires this lock
    around every queued write function.
  * ``pipeline`` / ``pipeline_lib``:  Deferred tasks and ``apply_mutation_plan``
    acquire this lock directly via ``with _apply_lock:``.
"""
import threading

_apply_lock = threading.Lock()
