"""
Regression test for the New-Videos / interactive-endpoint lock-up bug.

Reproduces the production scenario: the write-queue daemon writes to the
library near-continuously (an import in progress) while interactive request
handlers commit on their own request session (add-to-cart / remove / dismiss /
refresh persist).

Before the fix, request commits bypassed the write queue and _apply_lock, so
they collided with the daemon at the SQLite level (busy_timeout stalls and
"database is locked" errors). After the fix, request sessions run on a guarded
engine (app.database._install_write_serialization) that acquires the SAME
_apply_lock before any write, so the two never write to SQLite concurrently.

The busy_timeout here is set deliberately LOW (500 ms). If the guard ever
failed to serialise a request write against the daemon, the collision would
surface immediately as OperationalError("database is locked"). Zero errors +
completion within the timeout proves the writes are serialised and there is no
deadlock.
"""
import os
import inspect
import re
import threading
import time

from sqlalchemy import create_engine, Column, Integer, String, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker, declarative_base

from app.db_lock import _apply_lock
from app.database import _install_write_serialization, _apply_sqlite_pragmas
from app.services.transaction_telemetry import stats as transaction_stats

Base = declarative_base()


class GuardRow(Base):
    __tablename__ = "guard_rows"
    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String, default="")
    n = Column(Integer, default=0)


def _mk_engine(db_path, guarded):
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_size=8, max_overflow=16,
    )

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, rec):
        # Low busy_timeout so any un-serialised collision fails fast instead
        # of silently waiting.
        _apply_sqlite_pragmas(dbapi_conn, 500)

    if guarded:
        _install_write_serialization(eng)
    return eng


def test_request_writes_serialise_against_write_queue(tmp_path):
    db_path = str(tmp_path / "guard_sim.db")

    # Two engines on the SAME file: the "pipeline" engine (unguarded, mirrors
    # SessionLocal) and the "request" engine (guarded, mirrors RequestSessionLocal).
    pipeline_eng = _mk_engine(db_path, guarded=False)
    request_eng = _mk_engine(db_path, guarded=True)
    Base.metadata.create_all(pipeline_eng)

    PipelineSession = sessionmaker(bind=pipeline_eng, autoflush=False, expire_on_commit=False)
    RequestSession = sessionmaker(bind=request_eng, autoflush=False, expire_on_commit=False)

    errors = []
    stop = threading.Event()

    # ── Daemon: mirrors write_queue._run — holds _apply_lock around each write,
    #    writing continuously to simulate an active import. ──
    DAEMON_WRITES = 300

    def _daemon():
        for i in range(DAEMON_WRITES):
            if stop.is_set():
                break
            try:
                with _apply_lock:
                    s = PipelineSession()
                    try:
                        s.add(GuardRow(kind="import", n=i))
                        s.commit()
                    finally:
                        s.close()
            except OperationalError as e:  # pragma: no cover
                errors.append(("daemon", str(e)))

    # ── Request threads: interactive endpoints committing on the guarded
    #    request session (no explicit lock — the guard must handle it). ──
    REQ_THREADS = 4
    REQ_WRITES = 40

    def _request(worker):
        for i in range(REQ_WRITES):
            try:
                s = RequestSession()
                try:
                    # A read (WAL-concurrent) then a write+commit, like the
                    # cart/dismiss endpoints (query then add/delete then commit).
                    _ = s.query(GuardRow).count()
                    s.add(GuardRow(kind=f"req{worker}", n=i))
                    s.commit()
                finally:
                    s.close()
            except OperationalError as e:
                errors.append((f"req{worker}", str(e)))

    threads = [threading.Thread(target=_daemon, name="daemon")]
    threads += [threading.Thread(target=_request, args=(w,), name=f"req-{w}")
                for w in range(REQ_THREADS)]

    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    stop.set()
    elapsed = time.monotonic() - start

    alive = [t.name for t in threads if t.is_alive()]
    assert not alive, f"DEADLOCK: threads still running after 60s: {alive}"
    assert not errors, f"SQLite contention errors (guard failed to serialise): {errors[:5]}"

    # Verify every write landed.
    s = PipelineSession()
    try:
        total = s.query(GuardRow).count()
        req_total = s.query(GuardRow).filter(GuardRow.kind.like("req%")).count()
    finally:
        s.close()
    assert total == DAEMON_WRITES + REQ_THREADS * REQ_WRITES, f"lost writes: {total}"
    assert req_total == REQ_THREADS * REQ_WRITES, f"lost request writes: {req_total}"
    telemetry = transaction_stats()
    assert telemetry["count"] >= REQ_THREADS * REQ_WRITES
    assert telemetry["p99_ms"] >= telemetry["p50_ms"] >= 0
    assert telemetry["wait_count"] >= REQ_THREADS * REQ_WRITES
    assert telemetry["wait_p99_ms"] >= telemetry["wait_p50_ms"] >= 0

    # Lock must be fully released at the end (no leak).
    got = _apply_lock.acquire(timeout=1)
    assert got, "LEAK: _apply_lock not released after run"
    _apply_lock.release()

    print(f"OK: {total} writes, 0 errors, no deadlock, {elapsed:.2f}s")

    pipeline_eng.dispose()
    request_eng.dispose()


def test_durable_reconcilers_use_the_serialized_session_boundary():
    from app.database import SerializedSessionLocal
    from app.services import mutation_runtime, reconciliation_runtime

    assert mutation_runtime.process_next_mutation.__defaults__[0] is SerializedSessionLocal
    assert mutation_runtime.recover_mutation_queue.__defaults__[0] is SerializedSessionLocal
    source = inspect.getsource(reconciliation_runtime)
    assert "from app.database import SerializedSessionLocal" in source
    assert not re.search(r"(?<!Serialized)SessionLocal\(", source)
