"""
Playarr Database Engine & Session Management.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from typing import Generator

from app.config import get_settings

settings = get_settings()

# SQLite needs check_same_thread=False for FastAPI async usage
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

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
if settings.database_url.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")  # wait up to 30 s for lock
        cursor.execute("PRAGMA synchronous=NORMAL")   # safe with WAL, faster
        cursor.execute("PRAGMA foreign_keys=ON")       # enforce FK constraints + CASCADE
        cursor.close()

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
if settings.database_url.startswith("sqlite"):
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
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")  # 15 s — survive deferred-task storms
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
else:
    _cosmetic_engine = engine  # Non-SQLite: reuse main engine

CosmeticSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=_cosmetic_engine,
)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
