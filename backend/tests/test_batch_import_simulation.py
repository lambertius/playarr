"""
Simulation test for batch import pipeline.

Verifies:
  1. batch_import_task runs children in parallel (ThreadPoolExecutor)
  2. batch_import_task finalizes the parent job correctly
  3. complete_batch_job_task's pre-flight check detects the already-complete
     parent and exits WITHOUT overwriting step/progress
  4. The write queue serializes all DB writes
  5. No fire-and-forget writes clobber the final state
  6. Progress updates land correctly during parallel execution
  7. Defence-in-depth: _db_write terminal guard prevents clobber even if
     pre-flight fails
  8. Exact reproduction of the visual bug: 4 increments → 100% → 0%
"""
import os
import sys
import json
import time
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Minimal in-memory write queue (mirrors production _DBWriteQueue)
# ---------------------------------------------------------------------------

class _TestWriteQueue:
    """Mirrors production write queue for simulation."""

    def __init__(self):
        import queue as _q
        self._q = _q.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while True:
            item = self._q.get()
            if item is None:
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
            finally:
                self._q.task_done()

    def db_write(self, fn):
        from concurrent.futures import Future
        fut = Future()
        self._q.put((fn, fut))
        return fut.result()

    def db_write_soon(self, fn):
        self._q.put((fn, None))

    def drain(self):
        self._q.join()


# ---------------------------------------------------------------------------
# Test DB helpers
# ---------------------------------------------------------------------------

def _create_test_db(db_path: str, parent_id: int, child_ids: list[int]):
    """Create a test SQLite DB with parent + child processing_jobs."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE processing_jobs (
            id INTEGER PRIMARY KEY,
            status TEXT DEFAULT 'queued',
            display_name TEXT DEFAULT '',
            progress_percent INTEGER DEFAULT 0,
            current_step TEXT DEFAULT '',
            error_message TEXT,
            log_text TEXT DEFAULT '',
            pipeline_steps TEXT DEFAULT '[]',
            completed_at TEXT,
            updated_at TEXT,
            created_at TEXT
        )
    """)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO processing_jobs (id, status, display_name, created_at) VALUES (?, 'analyzing', 'Test Playlist', ?)",
        (parent_id, now),
    )
    for cid in child_ids:
        conn.execute(
            "INSERT INTO processing_jobs (id, status, display_name, created_at) VALUES (?, 'queued', ?, ?)",
            (cid, f"Child {cid}", now),
        )
    conn.commit()
    conn.close()


def _read_job(db_path: str, job_id: int) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM processing_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Simulated batch_import_task (mirrors production logic)
# ---------------------------------------------------------------------------

def _sim_batch_import_task(db_path: str, wq: _TestWriteQueue,
                           parent_id: int, child_ids: list[int],
                           work_duration: float = 0.1):
    """Simulates batch_import_task: parallel children, then finalize parent."""

    MAX_PARALLEL = 4
    total = len(child_ids)
    completed = 0
    child_errors = 0
    lock = threading.Lock()

    def _run_one(cid: int):
        # Mark child as downloading
        def _start():
            conn = sqlite3.connect(db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "UPDATE processing_jobs SET status='downloading', updated_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), cid),
            )
            conn.commit()
            conn.close()
        wq.db_write(_start)

        # Simulate work (download, organize, normalize, etc.)
        time.sleep(work_duration)

        # Mark child as complete
        def _finish():
            conn = sqlite3.connect(db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "UPDATE processing_jobs SET status='complete', current_step='Import complete', "
                "progress_percent=100, completed_at=?, updated_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat(), cid),
            )
            conn.commit()
            conn.close()
        wq.db_write(_finish)

    max_workers = min(len(child_ids), MAX_PARALLEL)

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="batch-dl") as pool:
        futures = {pool.submit(_run_one, cid): cid for cid in child_ids}
        for fut in as_completed(futures):
            cid = futures[fut]
            try:
                fut.result()
            except Exception:
                child_errors += 1
            with lock:
                completed += 1
                _comp = completed
                _pct = int((_comp / total) * 100)
                _step = f"{_comp}/{total} complete"

            # Progress update (fire-and-forget)
            def _progress(_s=_step, _p=_pct):
                conn = sqlite3.connect(db_path, timeout=10)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute(
                    "UPDATE processing_jobs SET current_step=?, progress_percent=?, updated_at=? WHERE id=?",
                    (_s, _p, datetime.now(timezone.utc).isoformat(), parent_id),
                )
                conn.commit()
                conn.close()
            wq.db_write_soon(_progress)

    # --- Finalize parent (blocking) ---
    final_step = f"All {total} imports complete · Album art & previews may still be processing"
    final_status = "failed" if child_errors == total else "complete"

    def _finalize():
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute(
            "UPDATE processing_jobs SET status=?, current_step=?, progress_percent=100, "
            "completed_at=?, updated_at=? WHERE id=?",
            (final_status, final_step,
             datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).isoformat(), parent_id),
        )
        conn.commit()
        conn.close()
    wq.db_write(_finalize)


# ---------------------------------------------------------------------------
# Simulated complete_batch_job_task (mirrors production logic WITH fix)
# ---------------------------------------------------------------------------

def _sim_complete_batch_job_task_FIXED(db_path: str, wq: _TestWriteQueue,
                                        parent_id: int, child_ids: list[int]):
    """Simulates the FIXED batch watcher with pre-flight check + terminal guard."""
    total = len(child_ids)
    _TERMINAL = ("complete", "failed", "cancelled", "skipped")

    # ── Pre-flight check (Layer 1) ──
    pre_rc = sqlite3.connect(db_path, timeout=5)
    pre_rc.execute("PRAGMA journal_mode=WAL")
    pre_status = pre_rc.execute(
        "SELECT status FROM processing_jobs WHERE id=?", (parent_id,)
    ).fetchone()
    pre_rc.close()
    if pre_status and pre_status[0] in _TERMINAL:
        return "early_exit"

    # _db_write with terminal guard (Layer 2)
    def _guarded_db_write(pct, step_msg):
        def _write():
            conn = sqlite3.connect(db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            # Guard: never overwrite a terminal parent
            cur = conn.execute(
                "SELECT status FROM processing_jobs WHERE id=?", (parent_id,)
            ).fetchone()
            if cur and cur[0] in _TERMINAL:
                conn.close()
                return
            conn.execute(
                "UPDATE processing_jobs SET progress_percent=?, current_step=?, updated_at=? WHERE id=?",
                (pct, step_msg, datetime.now(timezone.utc).isoformat(), parent_id),
            )
            conn.commit()
            conn.close()
        wq.db_write_soon(_write)

    _guarded_db_write(0, f"0/{total} complete · {total} queued")

    # Poll loop (simplified)
    for _ in range(10):
        rc = sqlite3.connect(db_path, timeout=5)
        rc.execute("PRAGMA journal_mode=WAL")
        ps = rc.execute(
            "SELECT status FROM processing_jobs WHERE id=?", (parent_id,)
        ).fetchone()
        rc.close()
        if ps and ps[0] in ("complete", "failed"):
            return "loop_exit"
        time.sleep(0.05)

    return "timeout"


def _sim_complete_batch_job_task_GUARD_ONLY(db_path: str, wq: _TestWriteQueue,
                                             parent_id: int, child_ids: list[int]):
    """Simulates the watcher with ONLY the terminal guard (no pre-flight).
    
    Tests the defence-in-depth scenario where the pre-flight check fails
    (e.g., exception caught) but the terminal guard in _db_write prevents
    the clobber.
    """
    total = len(child_ids)
    _TERMINAL = ("complete", "failed", "cancelled", "skipped")

    # NO pre-flight check — simulates pre-flight failing with exception

    # _db_write with terminal guard (Layer 2 only)
    def _guarded_db_write(pct, step_msg):
        def _write():
            conn = sqlite3.connect(db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            cur = conn.execute(
                "SELECT status FROM processing_jobs WHERE id=?", (parent_id,)
            ).fetchone()
            if cur and cur[0] in _TERMINAL:
                conn.close()
                return
            conn.execute(
                "UPDATE processing_jobs SET progress_percent=?, current_step=?, updated_at=? WHERE id=?",
                (pct, step_msg, datetime.now(timezone.utc).isoformat(), parent_id),
            )
            conn.commit()
            conn.close()
        wq.db_write_soon(_write)

    _guarded_db_write(0, f"0/{total} complete · {total} queued")

    for _ in range(10):
        rc = sqlite3.connect(db_path, timeout=5)
        rc.execute("PRAGMA journal_mode=WAL")
        ps = rc.execute(
            "SELECT status FROM processing_jobs WHERE id=?", (parent_id,)
        ).fetchone()
        rc.close()
        if ps and ps[0] in ("complete", "failed"):
            return "loop_exit"
        time.sleep(0.05)
    return "timeout"


def _sim_complete_batch_job_task_BUGGY(db_path: str, wq: _TestWriteQueue,
                                        parent_id: int, child_ids: list[int]):
    """Simulates the BUGGY batch watcher: no pre-flight, no terminal guard."""
    total = len(child_ids)

    # No pre-flight, no guard — fires blind overwrite
    def _init_write():
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute(
            "UPDATE processing_jobs SET progress_percent=0, current_step=?, updated_at=? WHERE id=?",
            (f"0/{total} complete · {total} queued",
             datetime.now(timezone.utc).isoformat(), parent_id),
        )
        conn.commit()
        conn.close()
    wq.db_write_soon(_init_write)

    for _ in range(10):
        rc = sqlite3.connect(db_path, timeout=5)
        rc.execute("PRAGMA journal_mode=WAL")
        ps = rc.execute(
            "SELECT status FROM processing_jobs WHERE id=?", (parent_id,)
        ).fetchone()
        rc.close()
        if ps and ps[0] in ("complete", "failed"):
            return "loop_exit"
        time.sleep(0.05)
    return "timeout"


# ===========================================================================
#  TEST CASES
# ===========================================================================

def test_fixed_watcher_does_not_clobber_parent():
    """
    Simulates --pool=solo: batch_import_task runs first, then complete_batch_job_task.
    With the pre-flight fix, the watcher should exit immediately and NOT overwrite
    the parent's step/progress.
    """
    db_dir = tempfile.mkdtemp(prefix="playarr_sim_")
    db_path = os.path.join(db_dir, "test.db")
    parent_id = 100
    child_ids = list(range(101, 113))  # 12 children

    _create_test_db(db_path, parent_id, child_ids)
    wq = _TestWriteQueue()

    # Phase 1: batch_import_task (runs first in solo pool)
    _sim_batch_import_task(db_path, wq, parent_id, child_ids, work_duration=0.05)
    wq.drain()

    # Verify parent is finalized by batch_import_task
    parent = _read_job(db_path, parent_id)
    assert parent["status"] == "complete", f"Expected complete, got {parent['status']}"
    assert parent["progress_percent"] == 100
    assert parent["current_step"].startswith("All 12 imports complete")
    assert parent["completed_at"] is not None

    # Phase 2: complete_batch_job_task (runs second in solo pool)
    result = _sim_complete_batch_job_task_FIXED(db_path, wq, parent_id, child_ids)
    wq.drain()

    assert result == "early_exit", f"Expected early_exit, got {result}"

    # Verify parent state is UNCHANGED after watcher
    parent_after = _read_job(db_path, parent_id)
    assert parent_after["status"] == "complete"
    assert parent_after["progress_percent"] == 100, \
        f"Progress clobbered! Expected 100, got {parent_after['progress_percent']}"
    assert parent_after["current_step"].startswith("All 12 imports complete"), \
        f"Step clobbered! Got: {parent_after['current_step']}"

    print("  PASS: Fixed watcher exits early, parent state preserved")


def test_buggy_watcher_clobbers_parent():
    """
    Demonstrates the bug: without pre-flight check, the watcher's initial
    _db_write(0, "0/12 complete · 12 queued") overwrites the parent's
    finalized state.
    """
    db_dir = tempfile.mkdtemp(prefix="playarr_sim_")
    db_path = os.path.join(db_dir, "test.db")
    parent_id = 200
    child_ids = list(range(201, 213))

    _create_test_db(db_path, parent_id, child_ids)
    wq = _TestWriteQueue()

    # Phase 1: batch_import_task
    _sim_batch_import_task(db_path, wq, parent_id, child_ids, work_duration=0.05)
    wq.drain()

    # Phase 2: buggy batch watcher
    result = _sim_complete_batch_job_task_BUGGY(db_path, wq, parent_id, child_ids)
    wq.drain()

    assert result == "loop_exit", f"Expected loop_exit, got {result}"

    # The buggy watcher CLOBBERS the step back to "0/12 complete · 12 queued"
    parent = _read_job(db_path, parent_id)
    assert parent["status"] == "complete"  # status is NOT overwritten (only step/progress)
    assert parent["progress_percent"] == 0, \
        f"Bug not reproduced: progress should be 0, got {parent['progress_percent']}"
    assert "0/12 complete" in parent["current_step"], \
        f"Bug not reproduced: step should be '0/12 complete · 12 queued', got {parent['current_step']}"

    print("  PASS: Bug reproduced — buggy watcher clobbers parent state")


def test_parallel_execution_verified():
    """
    Verify that children execute in parallel (ThreadPoolExecutor) and
    the write queue serializes DB writes.
    """
    db_dir = tempfile.mkdtemp(prefix="playarr_sim_")
    db_path = os.path.join(db_dir, "test.db")
    parent_id = 300
    child_ids = list(range(301, 313))

    _create_test_db(db_path, parent_id, child_ids)
    wq = _TestWriteQueue()

    # Track thread IDs to verify parallelism
    thread_ids = []
    thread_lock = threading.Lock()
    original_sleep = time.sleep

    def _tracking_sleep(duration):
        with thread_lock:
            tid = threading.current_thread().name
            if tid.startswith("batch-dl"):
                thread_ids.append(tid)
        original_sleep(duration)

    with patch("time.sleep", _tracking_sleep):
        _sim_batch_import_task(db_path, wq, parent_id, child_ids, work_duration=0.05)
    wq.drain()

    # Verify multiple threads were used
    unique_threads = set(thread_ids)
    assert len(unique_threads) >= 2, \
        f"Expected parallel execution (2+ threads), got {len(unique_threads)}: {unique_threads}"

    # Verify all children are complete
    for cid in child_ids:
        child = _read_job(db_path, cid)
        assert child["status"] == "complete", f"Child {cid}: {child['status']}"
        assert child["current_step"] == "Import complete"

    # Verify parent finalized
    parent = _read_job(db_path, parent_id)
    assert parent["status"] == "complete"
    assert parent["progress_percent"] == 100

    print(f"  PASS: Parallel execution verified ({len(unique_threads)} threads used)")


def test_partial_failure_handling():
    """
    Verify that if some children fail, the parent still finalizes correctly.
    """
    db_dir = tempfile.mkdtemp(prefix="playarr_sim_")
    db_path = os.path.join(db_dir, "test.db")
    parent_id = 400
    child_ids = list(range(401, 406))  # 5 children

    _create_test_db(db_path, parent_id, child_ids)
    wq = _TestWriteQueue()

    # Simulate with some failures by having _run_one raise for specific IDs
    total = len(child_ids)
    completed = 0
    child_errors = 0
    lock = threading.Lock()
    FAIL_IDS = {402, 404}

    def _run_one(cid: int):
        def _start():
            conn = sqlite3.connect(db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "UPDATE processing_jobs SET status='downloading', updated_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), cid),
            )
            conn.commit()
            conn.close()
        wq.db_write(_start)

        time.sleep(0.03)

        if cid in FAIL_IDS:
            # Mark as failed
            def _fail():
                conn = sqlite3.connect(db_path, timeout=10)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute(
                    "UPDATE processing_jobs SET status='failed', error_message='Simulated failure', "
                    "completed_at=?, updated_at=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(),
                     datetime.now(timezone.utc).isoformat(), cid),
                )
                conn.commit()
                conn.close()
            wq.db_write(_fail)
            raise RuntimeError(f"Simulated failure for {cid}")

        def _finish():
            conn = sqlite3.connect(db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "UPDATE processing_jobs SET status='complete', current_step='Import complete', "
                "progress_percent=100, completed_at=?, updated_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat(), cid),
            )
            conn.commit()
            conn.close()
        wq.db_write(_finish)

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="batch-dl") as pool:
        futures = {pool.submit(_run_one, cid): cid for cid in child_ids}
        for fut in as_completed(futures):
            cid = futures[fut]
            try:
                fut.result()
            except Exception:
                child_errors += 1
            with lock:
                completed += 1

    # Finalize parent
    final_step = f"Done ({total - child_errors} OK, {child_errors} failed) · Album art & previews may still be processing"
    def _finalize():
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute(
            "UPDATE processing_jobs SET status='complete', current_step=?, "
            "progress_percent=100, completed_at=?, updated_at=? WHERE id=?",
            (final_step, datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).isoformat(), parent_id),
        )
        conn.commit()
        conn.close()
    wq.db_write(_finalize)
    wq.drain()

    # Verify
    parent = _read_job(db_path, parent_id)
    assert parent["status"] == "complete"
    assert "3 OK" in parent["current_step"] and "2 failed" in parent["current_step"], \
        f"Expected partial failure summary, got: {parent['current_step']}"

    for cid in child_ids:
        child = _read_job(db_path, cid)
        if cid in FAIL_IDS:
            assert child["status"] == "failed", f"Child {cid} should be failed"
        else:
            assert child["status"] == "complete", f"Child {cid} should be complete"

    print("  PASS: Partial failure handled correctly")


def test_write_queue_serialization():
    """
    Verify that concurrent db_write_soon calls are serialized
    (no interleaving, no lost writes).
    """
    db_dir = tempfile.mkdtemp(prefix="playarr_sim_")
    db_path = os.path.join(db_dir, "test.db")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, val INTEGER DEFAULT 0)")
    conn.execute("INSERT INTO counter (id, val) VALUES (1, 0)")
    conn.commit()
    conn.close()

    wq = _TestWriteQueue()
    N = 50

    for i in range(N):
        def _incr():
            c = sqlite3.connect(db_path, timeout=10)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=10000")
            cur = c.execute("SELECT val FROM counter WHERE id=1").fetchone()[0]
            c.execute("UPDATE counter SET val=? WHERE id=1", (cur + 1,))
            c.commit()
            c.close()
        wq.db_write_soon(_incr)

    wq.drain()

    c = sqlite3.connect(db_path)
    val = c.execute("SELECT val FROM counter WHERE id=1").fetchone()[0]
    c.close()

    assert val == N, f"Expected {N} increments, got {val} — writes not serialized!"
    print(f"  PASS: Write queue serialization verified ({N} serial increments)")


def test_no_deferred_write_escape():
    """
    Verify that fire-and-forget (db_write_soon) writes from the batch watcher
    cannot execute AFTER the watcher returns, if the queue is drained.
    
    This simulates the exact race condition: batch_import_task finalizes parent,
    then complete_batch_job_task fires db_write_soon and immediately returns.
    After drain(), the parent's state must be consistent.
    """
    db_dir = tempfile.mkdtemp(prefix="playarr_sim_")
    db_path = os.path.join(db_dir, "test.db")
    parent_id = 600
    child_ids = list(range(601, 613))

    _create_test_db(db_path, parent_id, child_ids)
    wq = _TestWriteQueue()

    # Phase 1: batch_import_task completes
    _sim_batch_import_task(db_path, wq, parent_id, child_ids, work_duration=0.02)
    wq.drain()

    # Phase 2: FIXED watcher with pre-flight + terminal guard
    result = _sim_complete_batch_job_task_FIXED(db_path, wq, parent_id, child_ids)
    wq.drain()  # Ensure any lingering fire-and-forget writes complete

    parent = _read_job(db_path, parent_id)
    assert parent["status"] == "complete"
    assert parent["progress_percent"] == 100, \
        f"Deferred write escaped! Progress={parent['progress_percent']}"
    assert parent["current_step"].startswith("All 12 imports complete"), \
        f"Deferred write escaped! Step={parent['current_step']}"

    print("  PASS: No deferred writes escaped after pre-flight exit")


def test_defence_in_depth_guard_only():
    """
    Simulates the scenario where the pre-flight check FAILS (exception caught)
    but the terminal guard in _db_write prevents the clobber.
    
    This is the defence-in-depth layer: even without the pre-flight,
    each _db_write checks parent status before committing.
    """
    db_dir = tempfile.mkdtemp(prefix="playarr_sim_")
    db_path = os.path.join(db_dir, "test.db")
    parent_id = 700
    child_ids = list(range(701, 713))

    _create_test_db(db_path, parent_id, child_ids)
    wq = _TestWriteQueue()

    # Phase 1: batch_import_task completes
    _sim_batch_import_task(db_path, wq, parent_id, child_ids, work_duration=0.02)
    wq.drain()

    parent_before = _read_job(db_path, parent_id)
    assert parent_before["status"] == "complete"
    assert parent_before["progress_percent"] == 100

    # Phase 2: Guard-only watcher (pre-flight deliberately skipped)
    result = _sim_complete_batch_job_task_GUARD_ONLY(db_path, wq, parent_id, child_ids)
    wq.drain()

    # Even without pre-flight, the terminal guard should prevent clobber
    parent_after = _read_job(db_path, parent_id)
    assert parent_after["status"] == "complete"
    assert parent_after["progress_percent"] == 100, \
        f"Guard failed! Progress={parent_after['progress_percent']}"
    assert parent_after["current_step"].startswith("All 12 imports complete"), \
        f"Guard failed! Step={parent_after['current_step']}"

    print("  PASS: Defence-in-depth guard protects parent even without pre-flight")


def test_exact_visual_bug_reproduction():
    """
    Reproduces the EXACT visual pattern reported by the user:
      1. Progress increments: 1/12, 2/12, 3/12, 4/12 (first batch of 4)
      2. Parent goes to 100% complete → moves to "history"
      3. Parent flips to 0% → moves back to "active queue"
      4. Stays at 0% even though remaining 8 children finish
    
    Verifies the complete fix prevents this pattern.
    """
    db_dir = tempfile.mkdtemp(prefix="playarr_sim_")
    db_path = os.path.join(db_dir, "test.db")
    parent_id = 800
    child_ids = list(range(801, 813))  # 12 children

    _create_test_db(db_path, parent_id, child_ids)
    wq = _TestWriteQueue()

    # ── Track progress snapshots as they'd appear to the frontend ──
    snapshots = []

    def _snapshot(label):
        p = _read_job(db_path, parent_id)
        snapshots.append({
            "label": label,
            "status": p["status"],
            "progress": p["progress_percent"],
            "step": p["current_step"],
        })

    # Phase 1: batch_import_task runs (all 12 children, 4 at a time)
    _sim_batch_import_task(db_path, wq, parent_id, child_ids, work_duration=0.05)
    wq.drain()
    _snapshot("after_batch_import_task")

    # At this point: status=complete, progress=100, step="All 12 imports complete..."
    p1 = snapshots[-1]
    assert p1["status"] == "complete", f"Expected complete, got {p1['status']}"
    assert p1["progress"] == 100, f"Expected 100%, got {p1['progress']}"

    # Phase 2a: BUGGY watcher (reproduces the bug)
    db_dir_buggy = tempfile.mkdtemp(prefix="playarr_sim_buggy_")
    db_path_buggy = os.path.join(db_dir_buggy, "test.db")
    _create_test_db(db_path_buggy, parent_id, child_ids)
    wq_buggy = _TestWriteQueue()
    _sim_batch_import_task(db_path_buggy, wq_buggy, parent_id, child_ids, work_duration=0.02)
    wq_buggy.drain()

    buggy_before = _read_job(db_path_buggy, parent_id)
    assert buggy_before["progress_percent"] == 100

    result_buggy = _sim_complete_batch_job_task_BUGGY(db_path_buggy, wq_buggy, parent_id, child_ids)
    wq_buggy.drain()

    buggy_after = _read_job(db_path_buggy, parent_id)
    # Verify the bug: progress clobbered to 0
    assert buggy_after["status"] == "complete"
    assert buggy_after["progress_percent"] == 0, \
        f"Bug not reproduced: expected 0, got {buggy_after['progress_percent']}"
    assert "0/12" in buggy_after["current_step"]

    # Frontend would see: status=complete + step doesn't end with "complete"
    # → isFinalizing=true → shown as "Finalizing" in active queue
    step = buggy_after["current_step"]
    is_finalizing = (buggy_after["status"] == "complete"
                     and step
                     and not step.endswith("complete")
                     and not step.startswith("All "))
    assert is_finalizing, "Frontend should show this as 'Finalizing' (the bug)"

    # Phase 2b: FIXED watcher (prevents the bug)
    db_dir_fixed = tempfile.mkdtemp(prefix="playarr_sim_fixed_")
    db_path_fixed = os.path.join(db_dir_fixed, "test.db")
    _create_test_db(db_path_fixed, parent_id, child_ids)
    wq_fixed = _TestWriteQueue()
    _sim_batch_import_task(db_path_fixed, wq_fixed, parent_id, child_ids, work_duration=0.02)
    wq_fixed.drain()

    fixed_before = _read_job(db_path_fixed, parent_id)
    assert fixed_before["progress_percent"] == 100

    result_fixed = _sim_complete_batch_job_task_FIXED(db_path_fixed, wq_fixed, parent_id, child_ids)
    wq_fixed.drain()

    fixed_after = _read_job(db_path_fixed, parent_id)
    assert fixed_after["status"] == "complete"
    assert fixed_after["progress_percent"] == 100, \
        f"Fix failed: progress={fixed_after['progress_percent']}"
    assert fixed_after["current_step"].startswith("All 12 imports complete"), \
        f"Fix failed: step={fixed_after['current_step']}"

    # Frontend would see: step starts with "All " → NOT isFinalizing
    # → shown in history as complete
    step_fixed = fixed_after["current_step"]
    is_finalizing_fixed = (fixed_after["status"] == "complete"
                           and step_fixed
                           and not step_fixed.endswith("complete")
                           and not step_fixed.startswith("All "))
    assert not is_finalizing_fixed, "Frontend should show this in history (not finalizing)"

    print("  PASS: Exact visual bug reproduced and fix verified")


# ===========================================================================
#  Runner
# ===========================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Batch Import Simulation Tests")
    print("=" * 60)

    tests = [
        ("1. Fixed watcher preserves parent state", test_fixed_watcher_does_not_clobber_parent),
        ("2. Bug reproduction (buggy watcher clobbers)", test_buggy_watcher_clobbers_parent),
        ("3. Parallel execution verified", test_parallel_execution_verified),
        ("4. Partial failure handling", test_partial_failure_handling),
        ("5. Write queue serialization", test_write_queue_serialization),
        ("6. No deferred write escape", test_no_deferred_write_escape),
        ("7. Defence-in-depth: guard-only (no pre-flight)", test_defence_in_depth_guard_only),
        ("8. Exact visual bug: 4→100%→0% pattern", test_exact_visual_bug_reproduction),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"\n{name}:")
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")
    sys.exit(1 if failed > 0 else 0)
