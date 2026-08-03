"""
Playarr Database Engine & Session Management.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from typing import Generator
import re
import time

from app.config import get_settings
from app.db_lock import _apply_lock

settings = get_settings()

# SQLite needs check_same_thread=False for FastAPI async usage
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

_is_sqlite = settings.database_url.startswith("sqlite")


def _apply_sqlite_pragmas(dbapi_conn, busy_timeout_ms: int) -> None:
    """Apply the standard Playarr SQLite pragmas to a raw DBAPI connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    cursor.execute("PRAGMA synchronous=NORMAL")   # safe with WAL, faster
    cursor.execute("PRAGMA foreign_keys=ON")        # enforce FK constraints + CASCADE
    cursor.close()


engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=False,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=40,
    pool_timeout=10,
)

# Enable WAL journal mode for SQLite — allows concurrent readers + single
# writer without "database is locked" errors in multi-threaded task dispatch.
if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        _apply_sqlite_pragmas(dbapi_conn, 30000)  # wait up to 30 s for lock

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,  # Keep entity attributes accessible after commit
                             # (needed for intermediate commits in pipeline tasks)
)

# A separate engine with a moderate busy_timeout for "cosmetic" helpers
# (_update_job, _append_job_log, _set_pipeline_step).  These helpers open
# their own sessions to write status updates.  If the main pipeline session
# holds a RESERVED lock (from db.flush/commit), these helpers wait up to 5 s
# for the lock to clear.  With a retry loop in each helper, total worst-case
# is 5 retries × ~6 s = 30 s, which comfortably covers the brief commits
# made by the main session even when the pipeline lock is held.
if _is_sqlite:
    _cosmetic_engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )

    @event.listens_for(_cosmetic_engine, "connect")
    def _set_cosmetic_pragmas(dbapi_conn, connection_record):
        _apply_sqlite_pragmas(dbapi_conn, 15000)  # 15 s — survive deferred-task storms
else:
    _cosmetic_engine = engine  # Non-SQLite: reuse main engine

CosmeticSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=_cosmetic_engine,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Request-scoped write-serialisation guard
# ═══════════════════════════════════════════════════════════════════════════
#
# Interactive HTTP endpoints commit on their own request session.  The pipeline
# serialises its writes through the write queue / _apply_lock, but request
# sessions historically bypassed that entirely — so during an import (when the
# write-queue daemon writes near-continuously) a router commit would collide
# with the daemon at the SQLite level and block up to busy_timeout (30 s), which
# is what made New Videos / video-editor / library actions "appear to lock".
#
# To fix this WITHOUT touching every endpoint, request sessions run on a
# dedicated engine that carries an automatic guard:
#
#   * before_cursor_execute — when a statement is a write (INSERT/UPDATE/DELETE/
#     REPLACE) and this connection is not already holding the lock, acquire
#     _apply_lock BEFORE the write hits SQLite.  From that point the request
#     holds the same global lock the pipeline uses, so the two never write to
#     SQLite concurrently → no SQLITE_BUSY, no 30 s stalls, no "database is
#     locked" errors.
#   * commit / rollback — release the lock when the transaction ends.
#   * checkin — safety net: release if a connection ever returns to the pool
#     still flagged (a write that never committed/rolled back).
#
# Why request-scoped (its own engine) and NOT the main/pipeline engine:
# pipeline coordinator threads do bare db.flush() calls interleaved with slow
# network I/O (e.g. source re-resolution) and then hand the commit to the write
# queue.  If those flushes acquired _apply_lock on the coordinator thread and
# the coordinator then blocked on db_write() (which needs the daemon, which
# needs _apply_lock), it would deadlock.  Request handlers never call blocking
# db_write() mid-transaction, so guarding only the request engine is safe.
# _apply_lock is an RLock, so an endpoint that also wraps its commit in an
# explicit ``with _apply_lock:`` (e.g. routers/jobs.py) re-enters cleanly.

_WRITE_STATEMENT_PREFIXES = (
    "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP",
    "VACUUM", "ATTACH", "DETACH", "REINDEX",
)
_GUARD_FLAG = "_playarr_holds_apply_lock"


def _statement_is_write(statement) -> bool:
    if not statement:
        return False
    normalized = re.sub(r"/\*.*?\*/|--[^\r\n]*", " ", str(statement), flags=re.S).lstrip().upper()
    if normalized.startswith(_WRITE_STATEMENT_PREFIXES):
        return True
    if normalized.startswith("PRAGMA"):
        return "=" in normalized or normalized.startswith(("PRAGMA WAL_CHECKPOINT", "PRAGMA OPTIMIZE"))
    # CTE-first writes are common in generated SQL. Read-only WITH statements
    # have no mutation keyword before their first top-level SELECT.
    if normalized.startswith("WITH"):
        return bool(re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", normalized))
    return False


def _install_write_serialization(target_engine) -> None:
    """Install the automatic _apply_lock acquire/release guard on an engine.

    Every write statement executed on a connection from this engine acquires
    the global write lock before touching SQLite and releases it when the
    surrounding transaction ends.
    """

    @event.listens_for(target_engine, "before_cursor_execute")
    def _acquire_on_write(conn, cursor, statement, parameters, context, executemany):
        if conn.info.get(_GUARD_FLAG):
            return  # already holding it for this transaction
        if _statement_is_write(statement):
            wait_started = time.monotonic()
            _apply_lock.acquire()
            from app.services.transaction_telemetry import record_wait
            record_wait((time.monotonic() - wait_started) * 1000)
            conn.info[_GUARD_FLAG] = True
            from app.services.transaction_telemetry import begin
            begin(conn)

    @event.listens_for(target_engine, "commit")
    def _release_on_commit(conn):
        from app.services.transaction_telemetry import finish
        finish(conn, "commit")
        if conn.info.pop(_GUARD_FLAG, False):
            _apply_lock.release()

    @event.listens_for(target_engine, "rollback")
    def _release_on_rollback(conn):
        from app.services.transaction_telemetry import finish
        finish(conn, "rollback")
        if conn.info.pop(_GUARD_FLAG, False):
            _apply_lock.release()

    @event.listens_for(target_engine, "checkin")
    def _release_on_checkin(dbapi_connection, connection_record):
        # Safety net: a connection should never return to the pool still
        # holding the lock (the Session rolls back on close, firing the
        # rollback event above), but if it somehow does, release it here so
        # the lock can never leak.  checkin runs on the thread that used the
        # connection, so the release targets the correct owner.
        if connection_record.info.pop(_GUARD_FLAG, False):
            try:
                _apply_lock.release()
            except RuntimeError:
                pass


# Dedicated engine + session factory for interactive HTTP requests.  Same
# database, its own pool, plus the write-serialisation guard above.
if _is_sqlite:
    _request_engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        echo=False,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
        pool_timeout=10,
    )

    @event.listens_for(_request_engine, "connect")
    def _set_request_pragmas(dbapi_conn, connection_record):
        _apply_sqlite_pragmas(dbapi_conn, 30000)

    _install_write_serialization(_request_engine)
else:
    _request_engine = engine  # Non-SQLite handles concurrent writers natively

RequestSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=_request_engine,
    expire_on_commit=False,  # match SessionLocal — endpoints read attributes
                             # after commit (e.g. db.refresh(job); return job)
)

# The guarded factory is also the mandatory session for durable actors and
# outbox reconcilers. ``RequestSessionLocal`` remains as a compatibility name;
# new non-pipeline writers should use the role-oriented alias below.
SerializedSessionLocal = RequestSessionLocal

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session.

    Uses the guarded request engine so any write committed on this session is
    automatically serialised against the pipeline's write queue via _apply_lock.
    """
    db = RequestSessionLocal()
    try:
        yield db
    finally:
        db.close()
