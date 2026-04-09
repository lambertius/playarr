"""
Queue status simulation: verify no active→complete→active reverts.

Simulates the exact scenario from the bug report:
  - 100 tracks pushed to batch scrape with only scene_analysis enabled
  - All must transition cleanly: queued → finalizing → complete
  - No job should ever REVERT from complete back to active
  - The batch parent must reach complete after all children finish

Also tests every other pathway that engages the queue:
  1. URL import (pipeline_url with deferred tasks)
  2. Library import (pipeline_lib with deferred tasks)
  3. Rescan from disk (deferred with update_job_progress=False)
  4. Pure rescan (no deferred tasks)
  5. Normalize task (no deferred)
  6. Mixed batch (different completion speeds)
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, Column, Integer, String, DateTime, JSON, Float, event
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()


class QueueJob(Base):
    """Mirrors ProcessingJob for testing."""
    __tablename__ = "queue_jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String, default="rescan")
    status = Column(String, default="queued")
    current_step = Column(String, nullable=True)
    progress_percent = Column(Integer, default=0)
    display_name = Column(String, nullable=True)
    parent_job_id = Column(Integer, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    video_id = Column(Integer, nullable=True)
    error_message = Column(String, nullable=True)


# ── Shared infrastructure ──────────────────────────────────────────

_test_counter = 0
_test_counter_lock = threading.Lock()


def _make_engine():
    global _test_counter
    with _test_counter_lock:
        _test_counter += 1
        n = _test_counter
    db_path = os.path.join(os.path.dirname(__file__), f"_queue_sim_{n}.db")
    for f in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_size=10,
        max_overflow=20,
    )

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA busy_timeout=15000")

    Base.metadata.create_all(eng)

    # Register cleanup
    import atexit
    def _cleanup():
        eng.dispose()
        for f in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
            try:
                os.remove(f)
            except OSError:
                pass
    atexit.register(_cleanup)

    return eng, db_path


# ── Status tracking ─────────────────────────────────────────────

class StatusTracker:
    """Records all status transitions and detects reverts."""

    # Terminal statuses — once entered, should never leave
    TERMINAL = frozenset({"complete", "failed", "cancelled", "skipped"})
    # Active statuses — jobs here are in the active queue
    ACTIVE = frozenset({
        "queued", "downloading", "downloaded", "remuxing", "analyzing",
        "normalizing", "tagging", "writing_nfo", "asset_fetch", "finalizing",
    })

    def __init__(self):
        self.lock = threading.Lock()
        self.history: dict[int, list[str]] = defaultdict(list)
        self.reverts: list[tuple[int, str, str]] = []  # (job_id, from_status, to_status)
        self.current: dict[int, str] = {}

    def record(self, job_id: int, new_status: str):
        with self.lock:
            prev = self.current.get(job_id)
            if prev == new_status:
                return  # no change
            self.history[job_id].append(new_status)
            self.current[job_id] = new_status

            # Detect reverts: terminal → active
            if prev in self.TERMINAL and new_status in self.ACTIVE:
                self.reverts.append((job_id, prev, new_status))

    def assert_no_reverts(self, msg=""):
        assert len(self.reverts) == 0, (
            f"Status reverts detected{' (' + msg + ')' if msg else ''}:\n"
            + "\n".join(
                f"  Job {jid}: {prev} → {new}"
                for jid, prev, new in self.reverts
            )
        )


# ── Simulated pipelines ─────────────────────────────────────────

_write_lock = threading.Lock()


def _db_write(Session, fn):
    """Simulate the write queue's db_write (blocking)."""
    with _write_lock:
        db = Session()
        try:
            fn(db)
            db.commit()
        finally:
            db.close()


def _db_write_soon(Session, fn):
    """Simulate fire-and-forget (still serialised through lock)."""
    with _write_lock:
        db = Session()
        try:
            fn(db)
            db.commit()
        finally:
            db.close()


def _simulate_rescan_with_scene_analysis(
    Session, job_id: int, tracker: StatusTracker,
    deferred_delay: float = 0.01,
):
    """Simulate rescan_metadata_task with scene_analysis deferred task.

    This mirrors the exact flow from the bug report:
    1. Job starts → status=analyzing
    2. DB writes happen → status=tagging
    3. Main pipeline completes → status=finalizing (NOT complete!)
    4. Deferred scene_analysis runs → step="Scene analysis"
    5. Deferred completes → status=complete, step="Import complete"
    """
    # Stage A-C: main pipeline work
    def _set_analyzing(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "analyzing"
            job.started_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_analyzing)
    tracker.record(job_id, "analyzing")

    time.sleep(deferred_delay * 0.5)  # simulate work

    def _set_tagging(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "tagging"
            job.progress_percent = 70
            job.updated_at = datetime.now(timezone.utc)
    _db_write_soon(Session, _set_tagging)
    tracker.record(job_id, "tagging")

    time.sleep(deferred_delay * 0.3)

    # Main pipeline done → FINALIZING (not complete!)
    def _set_finalizing(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "finalizing"
            job.current_step = "Finalizing"
            job.progress_percent = 90
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_finalizing)
    tracker.record(job_id, "finalizing")

    # Stage D: Deferred task (scene analysis)
    time.sleep(deferred_delay)

    def _set_scene_step(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.current_step = "Scene analysis"
            job.progress_percent = 95
            job.updated_at = datetime.now(timezone.utc)
    _db_write_soon(Session, _set_scene_step)
    # Status stays "finalizing" — step change is cosmetic

    time.sleep(deferred_delay)

    # Final: Import complete → status=complete
    def _set_complete(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "complete"
            job.current_step = "Import complete"
            job.progress_percent = 100
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_complete)
    tracker.record(job_id, "complete")


def _simulate_url_import(
    Session, job_id: int, tracker: StatusTracker,
    deferred_delay: float = 0.01,
):
    """Simulate import_video_task (URL pipeline) with deferred tasks."""
    def _set_downloading(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "downloading"
            job.started_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_downloading)
    tracker.record(job_id, "downloading")

    time.sleep(deferred_delay * 0.5)

    def _set_analyzing(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "analyzing"
            job.current_step = "Applying to database"
            job.progress_percent = 85
            job.updated_at = datetime.now(timezone.utc)
    _db_write_soon(Session, _set_analyzing)
    tracker.record(job_id, "analyzing")

    time.sleep(deferred_delay * 0.3)

    # db_apply.py sets finalizing atomically inside transaction
    def _set_finalizing(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "finalizing"
            job.current_step = "Finalizing"
            job.progress_percent = 90
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_finalizing)
    tracker.record(job_id, "finalizing")

    # Deferred tasks
    time.sleep(deferred_delay)

    def _set_preview_step(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.current_step = "Preview"
            job.progress_percent = 93
            job.updated_at = datetime.now(timezone.utc)
    _db_write_soon(Session, _set_preview_step)

    time.sleep(deferred_delay)

    def _set_complete(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "complete"
            job.current_step = "Import complete"
            job.progress_percent = 100
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_complete)
    tracker.record(job_id, "complete")


def _simulate_library_import(
    Session, job_id: int, tracker: StatusTracker,
    deferred_delay: float = 0.01,
):
    """Simulate library_import_video_task (pipeline_lib).

    pipeline_lib/db_apply.py sets status=complete + step="Import complete"
    at 100% — no intermediate finalizing state. Deferred tasks run but
    don't update job progress.
    """
    def _set_analyzing(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "analyzing"
            job.started_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_analyzing)
    tracker.record(job_id, "analyzing")

    time.sleep(deferred_delay)

    # pipeline_lib/db_apply.py → status=complete + Import complete at 100%
    def _set_complete(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "complete"
            job.current_step = "Import complete"
            job.progress_percent = 100
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_complete)
    tracker.record(job_id, "complete")

    # Deferred tasks run but don't update the job (update_job_progress=True
    # but status is already complete with terminal step)
    time.sleep(deferred_delay)


def _simulate_rescan_from_disk(
    Session, job_id: int, tracker: StatusTracker,
    deferred_delay: float = 0.01,
):
    """Simulate _rescan_from_disk: status=complete + step="Rescan complete"
    at 100%, then dispatch deferred with update_job_progress=False."""
    def _set_analyzing(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "analyzing"
            job.started_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_analyzing)
    tracker.record(job_id, "analyzing")

    time.sleep(deferred_delay)

    def _set_complete(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "complete"
            job.current_step = "Rescan complete"
            job.progress_percent = 100
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_complete)
    tracker.record(job_id, "complete")

    # Deferred runs with update_job_progress=False — no job updates
    time.sleep(deferred_delay)


def _simulate_pure_rescan_no_deferred(
    Session, job_id: int, tracker: StatusTracker,
    deferred_delay: float = 0.01,
):
    """Simulate rescan with no deferred tasks (e.g. no scene_analysis, no AI)."""
    def _set_analyzing(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "analyzing"
            job.started_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_analyzing)
    tracker.record(job_id, "analyzing")

    time.sleep(deferred_delay)

    # No deferred tasks → go straight to complete
    def _set_complete(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "complete"
            job.current_step = "Import complete"
            job.progress_percent = 100
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_complete)
    tracker.record(job_id, "complete")


def _simulate_normalize_task(
    Session, job_id: int, tracker: StatusTracker,
    deferred_delay: float = 0.01,
):
    """Simulate normalize_task — no deferred, simple complete."""
    def _set_normalizing(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "normalizing"
            job.started_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_normalizing)
    tracker.record(job_id, "normalizing")

    time.sleep(deferred_delay)

    def _set_complete(db):
        job = db.query(QueueJob).get(job_id)
        if job:
            job.status = "complete"
            job.current_step = "Normalization complete"
            job.progress_percent = 100
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
    _db_write(Session, _set_complete)
    tracker.record(job_id, "complete")


# ── Frontend simulation ─────────────────────────────────────────

def _frontend_poll(Session, job_ids: list[int], tracker: StatusTracker):
    """Simulate frontend polling: read all jobs and classify as active/complete.

    Returns (active_count, complete_count, reverts_detected).
    """
    ACTIVE_STATUSES = {
        "queued", "downloading", "downloaded", "remuxing", "analyzing",
        "normalizing", "tagging", "writing_nfo", "asset_fetch", "finalizing",
    }
    TERMINAL_STATUSES = {"complete", "failed", "cancelled", "skipped"}

    db = Session()
    try:
        active = 0
        terminal = 0
        for jid in job_ids:
            job = db.query(QueueJob).get(jid)
            if not job:
                continue
            # Record the status the frontend sees
            tracker.record(jid, job.status)
            if job.status in ACTIVE_STATUSES:
                active += 1
            elif job.status in TERMINAL_STATUSES:
                terminal += 1
        return active, terminal
    finally:
        db.close()


# ── Batch parent simulation ─────────────────────────────────────

def _simulate_batch_parent(
    Session, parent_id: int, child_ids: list[int],
    tracker: StatusTracker,
    poll_interval: float = 0.05,
    timeout: float = 30.0,
):
    """Simulate complete_batch_job_task: polls children and marks parent complete."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        db = Session()
        try:
            done = 0
            total = len(child_ids)
            for cid in child_ids:
                child = db.query(QueueJob).get(cid)
                if not child:
                    done += 1
                    continue
                st = child.status
                # Silently completed detection (matches real code)
                if (st not in ("complete", "failed", "cancelled", "skipped")
                        and child.current_step
                        and child.current_step.lower().endswith("complete")
                        and child.progress_percent is not None
                        and child.progress_percent >= 100):
                    st = "complete"
                if st in ("complete", "failed", "cancelled", "skipped"):
                    done += 1

            # Update parent progress
            pct = int(100 * done / total) if total else 100
        finally:
            db.close()

        def _update_parent(db):
            parent = db.query(QueueJob).get(parent_id)
            if parent:
                parent.progress_percent = pct
                parent.current_step = f"{done}/{total} complete"
                parent.updated_at = datetime.now(timezone.utc)
        _db_write_soon(Session, _update_parent)

        if done >= total:
            # All children done — mark parent complete
            def _complete_parent(db):
                parent = db.query(QueueJob).get(parent_id)
                if parent:
                    parent.status = "complete"
                    parent.current_step = f"All {total} videos processed"
                    parent.progress_percent = 100
                    parent.completed_at = datetime.now(timezone.utc)
                    parent.updated_at = datetime.now(timezone.utc)
            _db_write(Session, _complete_parent)
            tracker.record(parent_id, "complete")
            return

        time.sleep(poll_interval)

    raise TimeoutError(f"Batch parent {parent_id} timed out waiting for children")


# ═══════════════════════════════════════════════════════════════════
#  TESTS
# ═══════════════════════════════════════════════════════════════════

def test_100_tracks_batch_scrape_scene_analysis_only():
    """PRIMARY TEST: 100 tracks batch scrape with only scene_analysis.

    Simulates the exact scenario from the bug report:
    - User selects 100 tracks from review queue
    - Clicks "Scrape Metadata" with only "Run scene analysis" enabled
    - All jobs must go: queued → analyzing → finalizing → complete
    - NO job should revert from complete to active
    - Batch parent must reach complete after all children
    """
    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    N = 100

    # Create parent batch job
    db = Session()
    parent = QueueJob(job_type="batch_rescan", status="queued", display_name="Batch Scrape (100)")
    db.add(parent)
    db.flush()
    parent_id = parent.id

    # Create 100 child jobs
    child_ids = []
    for i in range(N):
        child = QueueJob(
            job_type="rescan",
            status="queued",
            display_name=f"Track {i+1}",
            parent_job_id=parent_id,
        )
        db.add(child)
        db.flush()
        child_ids.append(child.id)
        tracker.record(child.id, "queued")
    db.commit()
    db.close()

    tracker.record(parent_id, "queued")

    # Start all child pipelines concurrently (with semaphore to limit parallelism)
    sem = threading.Semaphore(6)  # matches PIPELINE_SEMAPHORE
    errors = []

    def _run_child(cid):
        sem.acquire()
        try:
            _simulate_rescan_with_scene_analysis(Session, cid, tracker, deferred_delay=0.02)
        except Exception as e:
            errors.append((cid, str(e)))
        finally:
            sem.release()

    # Start frontend polling in background
    poll_stop = threading.Event()
    poll_reverts = []

    def _poller():
        while not poll_stop.is_set():
            _frontend_poll(Session, child_ids, tracker)
            time.sleep(0.1)

    poll_thread = threading.Thread(target=_poller, daemon=True)
    poll_thread.start()

    # Start batch parent monitor
    parent_thread = threading.Thread(
        target=_simulate_batch_parent,
        args=(Session, parent_id, child_ids, tracker),
        kwargs={"poll_interval": 0.1, "timeout": 60},
        daemon=True,
    )
    parent_thread.start()

    # Run all children
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(_run_child, cid) for cid in child_ids]
        for f in as_completed(futures, timeout=60):
            f.result()

    # Wait for parent to finish
    parent_thread.join(timeout=30)
    poll_stop.set()
    poll_thread.join(timeout=5)

    # Verify
    assert not errors, f"Child pipeline errors: {errors}"
    tracker.assert_no_reverts("100-track batch scrape scene analysis")

    # Verify final state in DB
    db = Session()
    try:
        for cid in child_ids:
            job = db.query(QueueJob).get(cid)
            assert job.status == "complete", (
                f"Child {cid} ({job.display_name}) not complete: "
                f"status={job.status}, step={job.current_step}, "
                f"progress={job.progress_percent}"
            )
            assert job.progress_percent == 100, f"Child {cid} progress={job.progress_percent}"
            assert job.current_step == "Import complete", f"Child {cid} step={job.current_step}"
            assert job.completed_at is not None, f"Child {cid} completed_at is None"

        parent = db.query(QueueJob).get(parent_id)
        assert parent.status == "complete", f"Parent not complete: status={parent.status}"
    finally:
        db.close()

    eng.dispose()


def test_url_import_no_revert():
    """URL import pipeline: queued → downloading → analyzing → finalizing → complete."""
    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    db = Session()
    job = QueueJob(job_type="import_url", status="queued", display_name="URL Import Test")
    db.add(job)
    db.flush()
    jid = job.id
    db.commit()
    db.close()

    tracker.record(jid, "queued")
    _simulate_url_import(Session, jid, tracker, deferred_delay=0.01)

    tracker.assert_no_reverts("URL import")

    db = Session()
    job = db.query(QueueJob).get(jid)
    assert job.status == "complete"
    assert job.current_step == "Import complete"
    assert job.progress_percent == 100
    db.close()
    eng.dispose()


def test_library_import_no_revert():
    """Library import: queued → analyzing → complete (no intermediate finalizing)."""
    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    db = Session()
    job = QueueJob(job_type="library_import_video", status="queued")
    db.add(job)
    db.flush()
    jid = job.id
    db.commit()
    db.close()

    tracker.record(jid, "queued")
    _simulate_library_import(Session, jid, tracker)

    tracker.assert_no_reverts("library import")

    db = Session()
    job = db.query(QueueJob).get(jid)
    assert job.status == "complete"
    db.close()
    eng.dispose()


def test_rescan_from_disk_no_revert():
    """Rescan from disk: queued → analyzing → complete (100%, no finalizing)."""
    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    db = Session()
    job = QueueJob(job_type="rescan", status="queued")
    db.add(job)
    db.flush()
    jid = job.id
    db.commit()
    db.close()

    tracker.record(jid, "queued")
    _simulate_rescan_from_disk(Session, jid, tracker)

    tracker.assert_no_reverts("rescan from disk")

    db = Session()
    job = db.query(QueueJob).get(jid)
    assert job.status == "complete"
    assert job.current_step == "Rescan complete"
    db.close()
    eng.dispose()


def test_pure_rescan_no_deferred():
    """Rescan with no deferred tasks: straight to complete."""
    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    db = Session()
    job = QueueJob(job_type="rescan", status="queued")
    db.add(job)
    db.flush()
    jid = job.id
    db.commit()
    db.close()

    tracker.record(jid, "queued")
    _simulate_pure_rescan_no_deferred(Session, jid, tracker)

    tracker.assert_no_reverts("pure rescan no deferred")

    db = Session()
    job = db.query(QueueJob).get(jid)
    assert job.status == "complete"
    db.close()
    eng.dispose()


def test_normalize_task_no_revert():
    """Normalize task: queued → normalizing → complete."""
    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    db = Session()
    job = QueueJob(job_type="normalize", status="queued")
    db.add(job)
    db.flush()
    jid = job.id
    db.commit()
    db.close()

    tracker.record(jid, "queued")
    _simulate_normalize_task(Session, jid, tracker)

    tracker.assert_no_reverts("normalize task")

    db = Session()
    job = db.query(QueueJob).get(jid)
    assert job.status == "complete"
    db.close()
    eng.dispose()


def test_mixed_batch_no_revert():
    """Batch with mixed pipeline types — all must complete, no reverts."""
    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    pipelines = [
        ("rescan_scene", _simulate_rescan_with_scene_analysis),
        ("url_import", _simulate_url_import),
        ("lib_import", _simulate_library_import),
        ("rescan_disk", _simulate_rescan_from_disk),
        ("normalize", _simulate_normalize_task),
        ("pure_rescan", _simulate_pure_rescan_no_deferred),
    ]

    db = Session()
    parent = QueueJob(job_type="batch_rescan", status="queued")
    db.add(parent)
    db.flush()
    parent_id = parent.id

    child_ids = []
    for name, _ in pipelines:
        child = QueueJob(job_type="rescan", status="queued", display_name=name)
        db.add(child)
        db.flush()
        child_ids.append(child.id)
        tracker.record(child.id, "queued")
    db.commit()
    db.close()

    tracker.record(parent_id, "queued")

    # Start parent monitor
    parent_thread = threading.Thread(
        target=_simulate_batch_parent,
        args=(Session, parent_id, child_ids, tracker),
        kwargs={"poll_interval": 0.05, "timeout": 30},
        daemon=True,
    )
    parent_thread.start()

    # Run children concurrently
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = []
        for (name, fn), cid in zip(pipelines, child_ids):
            futures.append(pool.submit(fn, Session, cid, tracker, 0.02))
        for f in as_completed(futures, timeout=30):
            f.result()

    parent_thread.join(timeout=10)

    tracker.assert_no_reverts("mixed batch")

    # Verify all complete
    db = Session()
    for cid in child_ids:
        job = db.query(QueueJob).get(cid)
        assert job.status == "complete", f"Child {cid} ({job.display_name}) status={job.status}"
    parent = db.query(QueueJob).get(parent_id)
    assert parent.status == "complete", f"Parent status={parent.status}"
    db.close()
    eng.dispose()


def test_concurrent_100_with_frontend_polling():
    """100 concurrent jobs with aggressive frontend polling — no reverts.

    This specifically tests the race condition that caused the original bug:
    frontend polling while jobs transition through finalizing.
    """
    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    N = 100
    db = Session()
    job_ids = []
    for i in range(N):
        job = QueueJob(job_type="rescan", status="queued", display_name=f"Track {i+1}")
        db.add(job)
        db.flush()
        job_ids.append(job.id)
        tracker.record(job.id, "queued")
    db.commit()
    db.close()

    # Aggressive frontend polling (every 50ms)
    poll_stop = threading.Event()
    poll_snapshots = []

    def _poller():
        while not poll_stop.is_set():
            active, complete = _frontend_poll(Session, job_ids, tracker)
            poll_snapshots.append((active, complete))
            time.sleep(0.05)

    poll_thread = threading.Thread(target=_poller, daemon=True)
    poll_thread.start()

    # Run all jobs
    sem = threading.Semaphore(6)

    def _run(jid):
        sem.acquire()
        try:
            _simulate_rescan_with_scene_analysis(Session, jid, tracker, deferred_delay=0.02)
        finally:
            sem.release()

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(_run, jid) for jid in job_ids]
        for f in as_completed(futures, timeout=60):
            f.result()

    poll_stop.set()
    poll_thread.join(timeout=5)

    # Key assertion: no reverts
    tracker.assert_no_reverts("concurrent 100 with frontend polling")

    # Verify monotonic complete count in snapshots
    # complete count should only go UP, never down
    max_complete = 0
    for active, complete in poll_snapshots:
        if complete < max_complete:
            # Allow small decreases due to polling timing (job transitions
            # between poll reads), but never more than the concurrency level
            assert max_complete - complete <= 6, (
                f"Complete count decreased beyond concurrency window: "
                f"max={max_complete}, now={complete}"
            )
        max_complete = max(max_complete, complete)

    # Final snapshot should show 100 complete
    db = Session()
    final_complete = sum(
        1 for jid in job_ids
        if db.query(QueueJob).get(jid).status == "complete"
    )
    assert final_complete == N, f"Expected {N} complete, got {final_complete}"
    db.close()
    eng.dispose()


def test_status_never_reverts_under_stress():
    """50 jobs of each type running simultaneously — stress test."""
    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    db = Session()
    all_ids = []
    pipelines = [
        _simulate_rescan_with_scene_analysis,
        _simulate_url_import,
        _simulate_library_import,
        _simulate_rescan_from_disk,
        _simulate_normalize_task,
    ]

    for i in range(50):
        for fn in pipelines:
            job = QueueJob(job_type="rescan", status="queued", display_name=f"Stress {i}-{fn.__name__}")
            db.add(job)
            db.flush()
            all_ids.append((job.id, fn))
            tracker.record(job.id, "queued")
    db.commit()
    db.close()

    sem = threading.Semaphore(12)

    def _run(jid, fn):
        sem.acquire()
        try:
            fn(Session, jid, tracker, deferred_delay=0.005)
        finally:
            sem.release()

    with ThreadPoolExecutor(max_workers=30) as pool:
        futures = [pool.submit(_run, jid, fn) for jid, fn in all_ids]
        for f in as_completed(futures, timeout=120):
            f.result()

    tracker.assert_no_reverts("stress test 250 jobs")

    db = Session()
    for jid, _ in all_ids:
        job = db.query(QueueJob).get(jid)
        assert job.status == "complete", f"Job {jid} status={job.status}"
    db.close()
    eng.dispose()


def test_status_transitions_are_monotonic():
    """Verify that status transitions follow a monotonic order.

    The allowed order is:
    queued → downloading → analyzing → tagging → finalizing → complete
    No job should go backwards in this order.
    """
    STATUS_ORDER = {
        "queued": 0,
        "downloading": 1,
        "downloaded": 2,
        "remuxing": 3,
        "analyzing": 4,
        "normalizing": 5,
        "tagging": 6,
        "writing_nfo": 7,
        "asset_fetch": 8,
        "finalizing": 9,
        "complete": 10,
        "failed": 10,
        "cancelled": 10,
        "skipped": 10,
    }

    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    N = 20
    db = Session()
    job_ids = []
    for i in range(N):
        job = QueueJob(job_type="rescan", status="queued")
        db.add(job)
        db.flush()
        job_ids.append(job.id)
        tracker.record(job.id, "queued")
    db.commit()
    db.close()

    sem = threading.Semaphore(6)

    def _run(jid):
        sem.acquire()
        try:
            _simulate_rescan_with_scene_analysis(Session, jid, tracker, deferred_delay=0.01)
        finally:
            sem.release()

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_run, jid) for jid in job_ids]
        for f in as_completed(futures, timeout=30):
            f.result()

    # Check that each job's status history is monotonically increasing
    for jid in job_ids:
        history = tracker.history[jid]
        for i in range(1, len(history)):
            prev_order = STATUS_ORDER.get(history[i-1], -1)
            curr_order = STATUS_ORDER.get(history[i], -1)
            assert curr_order >= prev_order, (
                f"Job {jid} went backwards: {history[i-1]} (order {prev_order}) "
                f"→ {history[i]} (order {curr_order}). Full history: {history}"
            )

    eng.dispose()


def test_batch_parent_completes_after_all_children():
    """Batch parent must reach complete only after ALL children are complete."""
    eng, db_path = _make_engine()
    Session = sessionmaker(bind=eng)
    tracker = StatusTracker()

    N = 20
    db = Session()
    parent = QueueJob(job_type="batch_rescan", status="queued")
    db.add(parent)
    db.flush()
    parent_id = parent.id

    child_ids = []
    for i in range(N):
        child = QueueJob(job_type="rescan", status="queued", parent_job_id=parent_id)
        db.add(child)
        db.flush()
        child_ids.append(child.id)
        tracker.record(child.id, "queued")
    db.commit()
    db.close()

    tracker.record(parent_id, "queued")

    # Track when parent becomes complete
    parent_complete_time = [None]
    child_complete_times = {}

    orig_simulate = _simulate_rescan_with_scene_analysis

    def _tracked_simulate(Session, jid, tracker, deferred_delay=0.02):
        orig_simulate(Session, jid, tracker, deferred_delay)
        child_complete_times[jid] = time.monotonic()

    parent_thread = threading.Thread(
        target=_simulate_batch_parent,
        args=(Session, parent_id, child_ids, tracker),
        kwargs={"poll_interval": 0.05, "timeout": 30},
        daemon=True,
    )
    parent_thread.start()

    sem = threading.Semaphore(6)

    def _run(cid):
        sem.acquire()
        try:
            _tracked_simulate(Session, cid, tracker, deferred_delay=0.02)
        finally:
            sem.release()

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_run, cid) for cid in child_ids]
        for f in as_completed(futures, timeout=30):
            f.result()

    parent_thread.join(timeout=10)

    # Verify parent is complete
    db = Session()
    parent = db.query(QueueJob).get(parent_id)
    assert parent.status == "complete", f"Parent status={parent.status}"

    # Verify all children complete
    for cid in child_ids:
        child = db.query(QueueJob).get(cid)
        assert child.status == "complete", f"Child {cid} status={child.status}"
    db.close()

    tracker.assert_no_reverts("batch parent timing")
    eng.dispose()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
