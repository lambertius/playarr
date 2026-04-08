"""
End-to-end concurrency simulation: all pipeline types running simultaneously.

Simulates the scenario where a user starts ALL of these at the same time:
  1. URL import       (pipeline_url → write queue + shared lock)
  2. Library import   (pipeline_lib → direct _apply_lock)
  3. Review scan      (pipeline_url → write queue + shared lock)
  4. Scrape metadata  (pipeline_url → write queue + shared lock)
  5. Rescan task      (pipeline_url → write queue + shared lock)

Verifies:
  - No deadlocks when all run concurrently
  - All writes complete successfully (no "database is locked")
  - The shared _apply_lock serialises ALL writes across pipeline types
  - Write queue and direct lock users never overlap
  - Review flags are auto-cleared after deferred tasks complete
  - CosmeticSessionLocal writes through write queue don't deadlock
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, JSON, event
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()


class SimVideo(Base):
    __tablename__ = "sim_videos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    artist = Column(String, default="")
    title = Column(String, default="")
    pipeline_source = Column(String, default="")
    review_status = Column(String, default="none")
    review_reason = Column(String, nullable=True)
    review_category = Column(String, nullable=True)
    processing_state = Column(JSON, default=dict)
    write_count = Column(Integer, default=0)


class SimJob(Base):
    __tablename__ = "sim_jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String, default="")
    status = Column(String, default="queued")
    current_step = Column(String, default="")
    progress = Column(Integer, default=0)


# ── Shared infrastructure ──────────────────────────────────────────

_test_counter = 0
_test_counter_lock = threading.Lock()


def _make_engine():
    """Create a file-backed SQLite engine with WAL mode (like production).
    Each call creates a unique DB file to avoid Windows file locking issues."""
    global _test_counter
    with _test_counter_lock:
        _test_counter += 1
        n = _test_counter
    db_path = os.path.join(os.path.dirname(__file__), f"_concurrent_sim_{n}.db")
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
    def _set_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    Base.metadata.create_all(eng)
    eng._test_db_path = db_path  # stash for cleanup
    return eng


def _cleanup_engine(engine):
    """Dispose engine and remove DB files."""
    db_path = getattr(engine, '_test_db_path', None)
    engine.dispose()
    if db_path:
        import time as _t
        _t.sleep(0.1)  # let WAL checkpoint flush
        for f in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


# ── Lock under test (the shared lock from app.db_lock) ──────────────

from app.db_lock import _apply_lock

# Track lock acquisition order for contention analysis
_lock_log = []
_log_lock = threading.Lock()


def _record(pipeline: str, action: str):
    with _log_lock:
        _lock_log.append((time.monotonic(), threading.current_thread().name, pipeline, action))


# ── Simulated pipeline_url write queue ──────────────────────────────
#
# This mirrors the real write_queue.py but uses the test DB session
# and the REAL shared _apply_lock.

import queue
from concurrent.futures import Future


class _TestWriteQueue:
    def __init__(self):
        self._q = queue.Queue()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                         name="test-write-queue")
        self._thread.start()

    def _run(self):
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                break
            fn, future = item
            try:
                with _apply_lock:
                    _record("write_queue", "locked")
                    result = fn()
                    _record("write_queue", "unlocked")
                if future is not None:
                    future.set_result(result)
            except Exception as exc:
                if future is not None:
                    future.set_exception(exc)
            finally:
                self._q.task_done()

    def db_write(self, fn):
        fut = Future()
        self._q.put((fn, fut))
        return fut.result()

    def db_write_soon(self, fn):
        self._q.put((fn, None))

    def drain(self):
        self._q.join()

    def stop(self):
        self._q.put(None)
        if self._thread:
            self._thread.join(timeout=5)


# ── Simulated pipeline tasks ───────────────────────────────────────

def _sim_url_import(Session, write_queue, video_id, num_writes=5):
    """Simulate pipeline_url import: multiple writes via write queue."""
    for i in range(num_writes):
        def _write(i=i):
            db = Session()
            try:
                v = db.query(SimVideo).get(video_id)
                if v:
                    v.write_count += 1
                    v.current_step = f"url_import_step_{i}"
                    db.commit()
            finally:
                db.close()
        write_queue.db_write(_write)
        _record("url_import", f"write_{i}")

    # Final: mark processing complete + auto-clear review flags
    def _final():
        db = Session()
        try:
            v = db.query(SimVideo).get(video_id)
            if v:
                ps = v.processing_state or {}
                ps["ai_enriched"] = {"completed": True, "timestamp": datetime.now(timezone.utc).isoformat()}
                ps["scenes_analyzed"] = {"completed": True, "timestamp": datetime.now(timezone.utc).isoformat()}
                v.processing_state = ps
                v.write_count += 1

                # Auto-clear logic (copied from coordinator)
                if v.review_status == "needs_human_review":
                    rc = v.review_category
                    flag_ok = lambda s: (v.processing_state or {}).get(s, {}).get("completed", False)
                    clear = False
                    if rc in ("ai_partial", "ai_pending"):
                        clear = flag_ok("ai_enriched") and flag_ok("scenes_analyzed")
                    if clear:
                        v.review_status = "none"
                        v.review_reason = None
                        v.review_category = None
                db.commit()
        finally:
            db.close()
    write_queue.db_write(_final)
    _record("url_import", "final_clear")


def _sim_library_import(Session, video_id, num_writes=5):
    """Simulate pipeline_lib import: multiple writes via direct _apply_lock."""
    for i in range(num_writes):
        db = Session()
        try:
            with _apply_lock:
                _record("lib_import", f"locked_write_{i}")
                v = db.query(SimVideo).get(video_id)
                if v:
                    v.write_count += 1
                    v.current_step = f"lib_import_step_{i}"
                    db.commit()
                _record("lib_import", f"unlocked_write_{i}")
        finally:
            db.close()

    # Final write with auto-clear
    db = Session()
    try:
        with _apply_lock:
            _record("lib_import", "locked_final")
            v = db.query(SimVideo).get(video_id)
            if v:
                ps = v.processing_state or {}
                ps["ai_enriched"] = {"completed": True, "timestamp": datetime.now(timezone.utc).isoformat()}
                ps["scenes_analyzed"] = {"completed": True, "timestamp": datetime.now(timezone.utc).isoformat()}
                v.processing_state = ps
                v.write_count += 1

                if v.review_status == "needs_human_review":
                    rc = v.review_category
                    flag_ok = lambda s: (v.processing_state or {}).get(s, {}).get("completed", False)
                    clear = False
                    if rc in ("ai_partial", "ai_pending"):
                        clear = flag_ok("ai_enriched") and flag_ok("scenes_analyzed")
                    if clear:
                        v.review_status = "none"
                        v.review_reason = None
                        v.review_category = None
                db.commit()
            _record("lib_import", "unlocked_final")
    finally:
        db.close()


def _sim_rescan(Session, write_queue, video_id, num_writes=3):
    """Simulate rescan_metadata_task: writes via write queue."""
    for i in range(num_writes):
        def _write(i=i):
            db = Session()
            try:
                v = db.query(SimVideo).get(video_id)
                if v:
                    v.write_count += 1
                    db.commit()
            finally:
                db.close()
        write_queue.db_write(_write)
        _record("rescan", f"write_{i}")


def _sim_scrape(Session, write_queue, video_id, num_writes=3):
    """Simulate scrape_metadata_task: writes via write queue."""
    for i in range(num_writes):
        def _write(i=i):
            db = Session()
            try:
                v = db.query(SimVideo).get(video_id)
                if v:
                    v.write_count += 1
                    db.commit()
            finally:
                db.close()
        write_queue.db_write(_write)
        _record("scrape", f"write_{i}")


def _sim_review_scan(Session, write_queue, video_id, num_writes=3):
    """Simulate a review queue scan: writes via write queue."""
    for i in range(num_writes):
        def _write(i=i):
            db = Session()
            try:
                v = db.query(SimVideo).get(video_id)
                if v:
                    v.write_count += 1
                    db.commit()
            finally:
                db.close()
        write_queue.db_write(_write)
        _record("review_scan", f"write_{i}")


def _sim_cosmetic_update(Session, write_queue, job_id, num_writes=4):
    """Simulate CosmeticSessionLocal _update_job writes (fire-and-forget)."""
    for i in range(num_writes):
        def _write(i=i):
            db = Session()
            try:
                j = db.query(SimJob).get(job_id)
                if j:
                    j.progress = (i + 1) * 25
                    j.current_step = f"step_{i}"
                    db.commit()
            finally:
                db.close()
        write_queue.db_write_soon(_write)
        _record("cosmetic", f"fire_forget_{i}")


# ═══════════════════════════════════════════════════════════════════════
#  Tests
# ═══════════════════════════════════════════════════════════════════════

def test_concurrent_all_pipelines_no_deadlock():
    """
    Run all 5 task types concurrently against the same DB.
    Verify: no deadlinks, all writes succeed, correct final state.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    wq = _TestWriteQueue()
    wq.start()

    try:
        # Seed: 5 videos (one per pipeline), 1 job for cosmetic updates
        db = Session()
        videos = []
        for i, pipeline in enumerate(["url_import", "lib_import", "rescan", "scrape", "review_scan"]):
            v = SimVideo(
                artist=f"Artist_{pipeline}", title=f"Title_{pipeline}",
                pipeline_source=pipeline,
                review_status="needs_human_review",
                review_reason="Missing AI metadata, scene analysis",
                review_category="ai_pending",
                processing_state={},
                write_count=0,
            )
            db.add(v)
        job = SimJob(job_type="test", status="running")
        db.add(job)
        db.commit()
        video_ids = [v.id for v in db.query(SimVideo).all()]
        job_id = job.id
        db.close()

        assert len(video_ids) == 5

        # Clear lock log
        with _log_lock:
            _lock_log.clear()

        # Run all pipelines concurrently
        errors = []
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="pipeline") as pool:
            futures = {
                pool.submit(_sim_url_import, Session, wq, video_ids[0], 5): "url_import",
                pool.submit(_sim_library_import, Session, video_ids[1], 5): "lib_import",
                pool.submit(_sim_rescan, Session, wq, video_ids[2], 3): "rescan",
                pool.submit(_sim_scrape, Session, wq, video_ids[3], 3): "scrape",
                pool.submit(_sim_review_scan, Session, wq, video_ids[4], 3): "review_scan",
                pool.submit(_sim_cosmetic_update, Session, wq, job_id, 4): "cosmetic",
            }
            for future in as_completed(futures, timeout=30):
                name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    errors.append((name, str(e)))

        # Drain any remaining fire-and-forget writes
        wq.drain()

        assert not errors, f"Pipeline errors: {errors}"

        # Verify final state
        db = Session()

        # URL import video: should have 6 writes (5 steps + 1 final)
        v_url = db.query(SimVideo).get(video_ids[0])
        assert v_url.write_count == 6, f"url_import: expected 6 writes, got {v_url.write_count}"
        assert v_url.review_status == "none", \
            f"url_import: review should be cleared, got '{v_url.review_status}'"

        # Library import video: should have 6 writes (5 steps + 1 final)
        v_lib = db.query(SimVideo).get(video_ids[1])
        assert v_lib.write_count == 6, f"lib_import: expected 6 writes, got {v_lib.write_count}"
        assert v_lib.review_status == "none", \
            f"lib_import: review should be cleared, got '{v_lib.review_status}'"

        # Rescan video: 3 writes only (no auto-clear logic in rescan sim)
        v_rescan = db.query(SimVideo).get(video_ids[2])
        assert v_rescan.write_count == 3, f"rescan: expected 3 writes, got {v_rescan.write_count}"

        # Scrape video: 3 writes
        v_scrape = db.query(SimVideo).get(video_ids[3])
        assert v_scrape.write_count == 3, f"scrape: expected 3 writes, got {v_scrape.write_count}"

        # Review scan video: 3 writes
        v_review = db.query(SimVideo).get(video_ids[4])
        assert v_review.write_count == 3, f"review: expected 3 writes, got {v_review.write_count}"

        # Cosmetic job: progress should be 100%
        j = db.query(SimJob).get(job_id)
        assert j.progress == 100, f"cosmetic: expected progress=100, got {j.progress}"

        db.close()

    finally:
        wq.stop()
        _cleanup_engine(engine)


def test_lock_serialisation_no_overlaps():
    """
    Verify that the shared lock truly serialises: no two writes overlap.

    Uses pipeline_url (write queue) and pipeline_lib (direct lock) 
    concurrently on the same DB. Checks lock log for overlapping holds.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    wq = _TestWriteQueue()
    wq.start()

    try:
        db = Session()
        v1 = SimVideo(artist="A", title="B", pipeline_source="url", write_count=0)
        v2 = SimVideo(artist="C", title="D", pipeline_source="lib", write_count=0)
        db.add_all([v1, v2])
        db.commit()
        vid1, vid2 = v1.id, v2.id
        db.close()

        with _log_lock:
            _lock_log.clear()

        # Run both pipeline types concurrently with many writes
        t1 = threading.Thread(
            target=_sim_url_import, args=(Session, wq, vid1, 10),
            name="url-writer",
        )
        t2 = threading.Thread(
            target=_sim_library_import, args=(Session, vid2, 10),
            name="lib-writer",
        )
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)
        wq.drain()

        # Analyse lock log: extract lock/unlock pairs
        with _log_lock:
            log_copy = list(_lock_log)

        # Build intervals of lock holds
        intervals = []
        pending = {}
        for ts, thread, pipeline, action in log_copy:
            if "locked" in action and "unlocked" not in action:
                pending[thread] = (ts, pipeline)
            elif "unlocked" in action and thread in pending:
                start_ts, start_pipeline = pending.pop(thread)
                intervals.append((start_ts, ts, start_pipeline, thread))

        # Check no intervals overlap
        intervals.sort()
        for i in range(len(intervals) - 1):
            _, end_i, pipe_i, thread_i = intervals[i]
            start_j, _, pipe_j, thread_j = intervals[i + 1]
            if pipe_i != pipe_j and thread_i != thread_j:
                assert end_i <= start_j, (
                    f"OVERLAP: {pipe_i}({thread_i}) ended at {end_i}, "
                    f"but {pipe_j}({thread_j}) started at {start_j}"
                )

        # Verify writes completed
        db = Session()
        assert db.query(SimVideo).get(vid1).write_count == 11  # 10 + 1 final
        assert db.query(SimVideo).get(vid2).write_count == 11  # 10 + 1 final
        db.close()

    finally:
        wq.stop()
        _cleanup_engine(engine)


def test_review_clear_under_concurrent_load():
    """
    Verify review auto-clear works correctly even under heavy concurrent
    write load from multiple pipelines.

    All 5 videos start with review flags. URL import and library import
    should clear their flags. Others should remain flagged.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    wq = _TestWriteQueue()
    wq.start()

    try:
        db = Session()
        video_ids = []
        for i, pipeline in enumerate(["url_import", "lib_import", "rescan", "scrape", "review"]):
            v = SimVideo(
                artist=f"Artist_{i}", title=f"Title_{i}",
                pipeline_source=pipeline,
                review_status="needs_human_review",
                review_reason="Missing AI metadata, scene analysis",
                review_category="ai_pending",
                processing_state={},
                write_count=0,
            )
            db.add(v)
            db.flush()
            video_ids.append(v.id)
        db.commit()
        db.close()

        # Run all concurrently
        threads = [
            threading.Thread(target=_sim_url_import, args=(Session, wq, video_ids[0], 8)),
            threading.Thread(target=_sim_library_import, args=(Session, video_ids[1], 8)),
            threading.Thread(target=_sim_rescan, args=(Session, wq, video_ids[2], 5)),
            threading.Thread(target=_sim_scrape, args=(Session, wq, video_ids[3], 5)),
            threading.Thread(target=_sim_review_scan, args=(Session, wq, video_ids[4], 5)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        wq.drain()

        # Verify
        db = Session()

        # URL import: cleared
        v = db.query(SimVideo).get(video_ids[0])
        assert v.review_status == "none", \
            f"url_import should be cleared, got '{v.review_status}'"
        assert v.processing_state.get("ai_enriched", {}).get("completed") is True

        # Library import: cleared
        v = db.query(SimVideo).get(video_ids[1])
        assert v.review_status == "none", \
            f"lib_import should be cleared, got '{v.review_status}'"

        # Rescan, scrape, review: still flagged (no auto-clear in their sim)
        for vid_id, name in [(video_ids[2], "rescan"), (video_ids[3], "scrape"),
                              (video_ids[4], "review")]:
            v = db.query(SimVideo).get(vid_id)
            assert v.review_status == "needs_human_review", \
                f"{name} should still be flagged, got '{v.review_status}'"

        db.close()

    finally:
        wq.stop()
        _cleanup_engine(engine)


def test_write_queue_and_direct_lock_interleave():
    """
    Stress test: rapid interleaving of write-queue writes and direct-lock writes.
    50 writes from each side, all targeting the same video row.
    Must not deadlock and final write_count must equal total writes.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    wq = _TestWriteQueue()
    wq.start()

    WRITES_PER_SIDE = 50

    try:
        db = Session()
        v = SimVideo(artist="Stress", title="Test", write_count=0)
        db.add(v)
        db.commit()
        vid = v.id
        db.close()

        errors = []

        def _queue_writes():
            for i in range(WRITES_PER_SIDE):
                def _w(i=i):
                    s = Session()
                    try:
                        row = s.query(SimVideo).get(vid)
                        if row:
                            row.write_count += 1
                            s.commit()
                    finally:
                        s.close()
                try:
                    wq.db_write(_w)
                except Exception as e:
                    errors.append(("queue", i, str(e)))

        def _direct_writes():
            for i in range(WRITES_PER_SIDE):
                s = Session()
                try:
                    with _apply_lock:
                        row = s.query(SimVideo).get(vid)
                        if row:
                            row.write_count += 1
                            s.commit()
                except Exception as e:
                    errors.append(("direct", i, str(e)))
                finally:
                    s.close()

        t1 = threading.Thread(target=_queue_writes, name="queue-stress")
        t2 = threading.Thread(target=_direct_writes, name="direct-stress")
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)
        wq.drain()

        assert not errors, f"Write errors: {errors}"

        db = Session()
        v = db.query(SimVideo).get(vid)
        expected = WRITES_PER_SIDE * 2
        assert v.write_count == expected, \
            f"Expected {expected} writes, got {v.write_count} — writes were lost!"
        db.close()

    finally:
        wq.stop()
        _cleanup_engine(engine)
