"""
Celery Worker Configuration

Desktop / installed mode (default):
  Tasks run in-process via threads. No Redis required. This is the
  recommended mode for single-user Windows installs.

Advanced / server mode (CELERY_WORKER_ENABLED=1 + Redis):
  Tasks dispatch to Celery via Redis for multi-process scalability.
  Requires a running Redis instance and a separate Celery worker process.
"""
import logging
import os
import threading
from celery import Celery
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _redis_available() -> bool:
    """Check if Redis is reachable."""
    try:
        import redis
        r = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        return True
    except Exception:
        return False


def _should_use_celery() -> bool:
    """
    Determine whether to dispatch tasks to Celery.

    Requires BOTH:
      1. The env var CELERY_WORKER_ENABLED=1 (opt-in)
      2. Redis to be reachable

    Without an explicit opt-in, tasks always run in-process via threads.
    This prevents tasks silently queuing in Redis when no Celery worker
    is running to consume them.
    """
    opt_in = os.environ.get("CELERY_WORKER_ENABLED", "").lower() in ("1", "true", "yes")
    if not opt_in:
        return False
    return _redis_available()


_use_celery = _should_use_celery()

celery_app = Celery(
    "playarr",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Retry settings
    task_default_retry_delay=30,
    task_max_retries=3,
)

if not _use_celery:
    logger.warning("Running tasks in-process via threads (set CELERY_WORKER_ENABLED=1 to use Celery)")
else:
    logger.info("Celery worker enabled — tasks will be dispatched via Redis")

# --- Eager mode: run tasks synchronously if env var set ---
if os.environ.get("CELERY_ALWAYS_EAGER", "").lower() in ("1", "true", "yes"):
    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
    )


# ── Job cancellation registry ─────────────────────────────────────────────
# Thread-safe set of job IDs that have been requested to cancel.
# Running tasks check this set at each pipeline checkpoint.
_cancelled_jobs: set[int] = set()
_cancel_lock = threading.Lock()


class JobCancelledError(Exception):
    """Raised when a running task detects that its job has been cancelled."""


def request_cancel(job_id: int) -> None:
    """Mark a job for cancellation (thread-safe)."""
    with _cancel_lock:
        _cancelled_jobs.add(job_id)


def is_cancelled(job_id: int) -> bool:
    """Check whether a job has been marked for cancellation."""
    with _cancel_lock:
        return job_id in _cancelled_jobs


def clear_cancel(job_id: int) -> None:
    """Remove a job from the cancellation set (cleanup)."""
    with _cancel_lock:
        _cancelled_jobs.discard(job_id)


# ── Pipeline concurrency limiter ──────────────────────────────────────────
# Without Celery, threads are the only executor.  Playlist imports can create
# dozens of child import_video_task threads; without a cap they thrash SQLite
# and the download semaphore.  This semaphore limits how many *pipeline*
# threads run concurrently.  Batch rescans also need throttling — spawning
# one thread per video causes thread explosion and SQLite write contention.
_PIPELINE_SEMAPHORE = threading.Semaphore(6)  # max 6 concurrent pipelines
_THROTTLED_TASKS = {"app.tasks.import_video_task", "app.tasks.rescan_metadata_task"}

# ── Shared deferred-task concurrency limiter ──────────────────────────────
# Limits the total number of DB-writing deferred-task threads across ALL
# pipeline types (pipeline_url, pipeline_lib, pipeline).  Now that all DB
# writes are serialised through the shared write queue / _apply_lock, the
# semaphore only needs to limit I/O concurrency (ffmpeg, downloads, AI).
# Raised from 3 to 6 to reduce queue wait times in large batches.
GLOBAL_DEFERRED_SLOTS = threading.Semaphore(6)


def dispatch_task(task, *args, **kwargs):
    """
    Dispatch a task: use Celery .delay() if Redis is available,
    otherwise run the underlying function in a background thread.
    """
    if _use_celery:
        return task.delay(*args, **kwargs)
    else:
        throttled = task.name in _THROTTLED_TASKS

        def _run():
            if throttled:
                _PIPELINE_SEMAPHORE.acquire()
            try:
                task.run(*args, **kwargs)
            except Exception as e:
                logger.error(f"Background task {task.name} failed: {e}", exc_info=True)
            finally:
                if throttled:
                    _PIPELINE_SEMAPHORE.release()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t
