"""
DB-level simulation of the review queue auto-clear bug and fix.

Reproduces the production failure: batch AI scrape from review queue
completed successfully (ai_enriched + scenes_analyzed both True),
but items stayed in the review queue because:

  1. (v1.9.5) XML sidecar write + auto-clear shared a single try block.
     When XML write raised "database is locked", the entire block aborted,
     including the auto-clear commit.

  2. (v1.9.7) All pipelines now serialize through a single shared lock
     (app.db_lock._apply_lock), and XML write is wrapped in its own
     try/except so it can't abort the auto-clear.

This test creates a real in-memory SQLite DB, inserts a video with
review flags, and runs the coordinator's finally-block logic to verify
that review flags are cleared even if the XML write fails.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, JSON
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()


class FakeVideo(Base):
    """Minimal stand-in for VideoItem with just the columns we need."""
    __tablename__ = "fake_videos"
    id = Column(Integer, primary_key=True)
    artist = Column(String, default="")
    title = Column(String, default="")
    folder_path = Column(String, default="")
    review_status = Column(String, default="none")
    review_reason = Column(String, nullable=True)
    review_category = Column(String, nullable=True)
    processing_state = Column(JSON, default=dict)


def _make_ps(**flags):
    """Build a processing_state dict."""
    return {
        step: {
            "completed": done,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": "test",
        }
        for step, done in flags.items()
    }


def _make_engine():
    """Create an in-memory SQLite engine with WAL-like pragmas."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


def _run_coordinator_finally(Session, video_id, xml_write_fails=False):
    """
    Simulate the coordinator's finally block from pipeline_url/deferred.py.

    This is a faithful copy of the auto-clear logic, with the XML write
    optionally raising an exception to simulate "database is locked".
    """
    def _final_xml_and_clear():
        _fdb = Session()
        try:
            _fv = _fdb.query(FakeVideo).get(video_id)
            if _fv:
                # Simulate XML write (may fail)
                try:
                    if xml_write_fails:
                        raise Exception("(sqlite3.OperationalError) database is locked")
                except Exception as _xw_exc:
                    pass  # XML failure is caught separately — doesn't abort auto-clear

                # Auto-clear review flags when the underlying issue
                # has been resolved by the deferred tasks that just ran.
                _did_clear = False
                if _fv and _fv.review_status == "needs_human_review":
                    _rc = _fv.review_category
                    _ps = _fv.processing_state or {}
                    _flag_ok = lambda s: _ps.get(s, {}).get("completed", False)
                    _rr = _fv.review_reason or ""
                    _clear = False
                    if _rc in ("ai_partial", "ai_pending"):
                        _need_ai = "AI metadata" in _rr
                        _need_scenes = "scene analysis" in _rr
                        _clear = (not _need_ai or _flag_ok("ai_enriched")) and (
                            not _need_scenes or _flag_ok("scenes_analyzed")
                        )
                        if not (_need_ai or _need_scenes):
                            _clear = _flag_ok("ai_enriched")
                    elif _rc == "normalization":
                        _clear = _flag_ok("audio_normalized")
                    elif _rc == "scanned":
                        _clear = _flag_ok("metadata_scraped") or _flag_ok("metadata_resolved")
                    if _clear:
                        _fv.review_status = "none"
                        _fv.review_reason = None
                        _fv.review_category = None
                        _did_clear = True
                _fdb.commit()
                return _did_clear
        finally:
            _fdb.close()

    return _final_xml_and_clear()


# ═══════════════════════════════════════════════════════════════════════
#  Test cases
# ═══════════════════════════════════════════════════════════════════════

def test_clear_ai_pending_after_both_tasks_complete():
    """
    Scenario: Video flagged ai_pending with 'Missing AI metadata, scene analysis'.
    Both ai_enriched and scenes_analyzed now completed.
    Expected: review_status cleared to 'none'.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine)

    db = Session()
    v = FakeVideo(
        id=1, artist="Test", title="Test Video",
        review_status="needs_human_review",
        review_reason="Missing AI metadata, scene analysis",
        review_category="ai_pending",
        processing_state=_make_ps(ai_enriched=True, scenes_analyzed=True),
    )
    db.add(v)
    db.commit()
    db.close()

    cleared = _run_coordinator_finally(Session, video_id=1, xml_write_fails=False)
    assert cleared, "Review flag should have been cleared"

    db = Session()
    v = db.query(FakeVideo).get(1)
    assert v.review_status == "none", f"Expected 'none', got '{v.review_status}'"
    assert v.review_reason is None
    assert v.review_category is None
    db.close()


def test_clear_ai_partial_missing_scenes_only():
    """
    Scenario: Video flagged ai_partial with 'Missing scene analysis'.
    scenes_analyzed now completed, ai_enriched was already done.
    Expected: review_status cleared to 'none'.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine)

    db = Session()
    v = FakeVideo(
        id=1, artist="Test", title="Test Video",
        review_status="needs_human_review",
        review_reason="Missing scene analysis",
        review_category="ai_partial",
        processing_state=_make_ps(ai_enriched=True, scenes_analyzed=True),
    )
    db.add(v)
    db.commit()
    db.close()

    cleared = _run_coordinator_finally(Session, video_id=1, xml_write_fails=False)
    assert cleared

    db = Session()
    v = db.query(FakeVideo).get(1)
    assert v.review_status == "none"
    db.close()


def test_clear_even_when_xml_write_fails():
    """
    THE CRITICAL FIX TEST.

    Scenario: Video flagged ai_pending. Both enrichment flags completed.
    XML sidecar write FAILS with "database is locked".
    Expected: review_status STILL cleared, because the XML exception is
              caught separately and doesn't abort the auto-clear logic.

    This is the exact bug from v1.9.5 production: the XML write and
    auto-clear were in the same try block, so XML failure killed both.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine)

    db = Session()
    v = FakeVideo(
        id=1, artist="Test", title="Test Video",
        review_status="needs_human_review",
        review_reason="Missing AI metadata, scene analysis",
        review_category="ai_pending",
        processing_state=_make_ps(ai_enriched=True, scenes_analyzed=True),
    )
    db.add(v)
    db.commit()
    db.close()

    cleared = _run_coordinator_finally(Session, video_id=1, xml_write_fails=True)
    assert cleared, "Review flag should clear even when XML write fails"

    db = Session()
    v = db.query(FakeVideo).get(1)
    assert v.review_status == "none", \
        f"CRITICAL: Review status is '{v.review_status}' after XML failure — " \
        f"this is the v1.9.5 production bug!"
    assert v.review_reason is None
    assert v.review_category is None
    db.close()


def test_no_clear_when_tasks_incomplete():
    """
    Scenario: Video flagged ai_pending, but ai_enriched not completed.
    Expected: review flags remain.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine)

    db = Session()
    v = FakeVideo(
        id=1, artist="Test", title="Test Video",
        review_status="needs_human_review",
        review_reason="Missing AI metadata, scene analysis",
        review_category="ai_pending",
        processing_state=_make_ps(ai_enriched=False, scenes_analyzed=True),
    )
    db.add(v)
    db.commit()
    db.close()

    cleared = _run_coordinator_finally(Session, video_id=1, xml_write_fails=False)
    assert not cleared

    db = Session()
    v = db.query(FakeVideo).get(1)
    assert v.review_status == "needs_human_review"
    assert v.review_category == "ai_pending"
    db.close()


def test_no_clear_unrelated_review_category():
    """
    Scenario: Video has review category 'duplicate' — should not be auto-cleared
    even though all processing flags are completed.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine)

    db = Session()
    v = FakeVideo(
        id=1, artist="Test", title="Test Video",
        review_status="needs_human_review",
        review_reason="Possible duplicate",
        review_category="duplicate",
        processing_state=_make_ps(ai_enriched=True, scenes_analyzed=True),
    )
    db.add(v)
    db.commit()
    db.close()

    cleared = _run_coordinator_finally(Session, video_id=1)
    assert not cleared

    db = Session()
    v = db.query(FakeVideo).get(1)
    assert v.review_status == "needs_human_review"
    db.close()


def test_clear_normalization():
    """
    Scenario: Video flagged for normalization failure, now audio_normalized.
    Expected: clear.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine)

    db = Session()
    v = FakeVideo(
        id=1, artist="Test", title="Test Video",
        review_status="needs_human_review",
        review_reason="Audio normalization failed",
        review_category="normalization",
        processing_state=_make_ps(audio_normalized=True),
    )
    db.add(v)
    db.commit()
    db.close()

    cleared = _run_coordinator_finally(Session, video_id=1)
    assert cleared

    db = Session()
    v = db.query(FakeVideo).get(1)
    assert v.review_status == "none"
    db.close()


def test_clear_scanned_after_metadata_scraped():
    """
    Scenario: Video flagged 'scanned', metadata_scraped now completed.
    Expected: clear.
    """
    engine = _make_engine()
    Session = sessionmaker(bind=engine)

    db = Session()
    v = FakeVideo(
        id=1, artist="Test", title="Test Video",
        review_status="needs_human_review",
        review_reason="Scan incomplete",
        review_category="scanned",
        processing_state=_make_ps(metadata_scraped=True),
    )
    db.add(v)
    db.commit()
    db.close()

    cleared = _run_coordinator_finally(Session, video_id=1)
    assert cleared

    db = Session()
    v = db.query(FakeVideo).get(1)
    assert v.review_status == "none"
    db.close()


def test_shared_lock_exists_and_is_singleton():
    """
    Verify that app.db_lock._apply_lock exists and is the same object
    imported by all three pipelines.
    """
    from app.db_lock import _apply_lock as shared_lock
    from app.pipeline.db_apply import _apply_lock as pipeline_lock
    from app.pipeline_lib.db_apply import _apply_lock as pipeline_lib_lock

    assert shared_lock is pipeline_lock, \
        "pipeline/db_apply._apply_lock is NOT the shared lock!"
    assert shared_lock is pipeline_lib_lock, \
        "pipeline_lib/db_apply._apply_lock is NOT the shared lock!"
    assert isinstance(shared_lock, type(threading.Lock()))


def test_write_queue_acquires_shared_lock():
    """
    Verify that the write queue's _run method references app.db_lock._apply_lock.
    """
    import inspect
    from app.pipeline_url.write_queue import _DBWriteQueue

    source = inspect.getsource(_DBWriteQueue._run)
    assert "from app.db_lock import _apply_lock" in source, \
        "write_queue._run doesn't import the shared lock"
    assert "with _apply_lock:" in source, \
        "write_queue._run doesn't acquire the shared lock"


def test_pipeline_deferred_uses_shared_lock():
    """
    Verify that pipeline/deferred.py and pipeline_lib/deferred.py
    import _apply_lock from the shared module, not their own db_apply.
    """
    backend_dir = os.path.join(os.path.dirname(__file__), "..")

    for pipeline in ("pipeline", "pipeline_lib"):
        path = os.path.join(backend_dir, "app", pipeline, "deferred.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        assert "from app.db_lock import _apply_lock" in src, \
            f"{pipeline}/deferred.py doesn't import from app.db_lock"
        assert f"from app.{pipeline}.db_apply import _apply_lock" not in src, \
            f"{pipeline}/deferred.py still imports lock from its own db_apply"


def test_xml_write_separated_from_auto_clear():
    """
    Structural test: verify in all 3 deferred coordinator files that
    the XML write (write_playarr_xml / _final_write_xml) is wrapped in
    its own try/except, separate from the auto-clear commit.
    """
    backend_dir = os.path.join(os.path.dirname(__file__), "..")
    deferred_files = [
        os.path.join(backend_dir, "app", "pipeline", "deferred.py"),
        os.path.join(backend_dir, "app", "pipeline_lib", "deferred.py"),
        os.path.join(backend_dir, "app", "pipeline_url", "deferred.py"),
    ]

    for path in deferred_files:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        name = os.path.basename(os.path.dirname(path))

        # The XML write must be in its own try/except
        assert "try:" in src and "_final_write_xml" in src, \
            f"{name}/deferred.py: missing XML write"

        # Find the XML write try block and verify it's separate from auto-clear
        # The pattern should be: try: _final_write_xml(...) except: <log warning>
        # followed by auto-clear logic OUTSIDE that inner try/except
        lines = src.splitlines()
        xml_write_line = None
        auto_clear_line = None
        for i, line in enumerate(lines):
            if "_final_write_xml" in line and "import" not in line:
                xml_write_line = i
            if "Review flag cleared" in line and xml_write_line is not None:
                auto_clear_line = i
                break

        assert xml_write_line is not None, \
            f"{name}/deferred.py: can't find _final_write_xml call"
        assert auto_clear_line is not None, \
            f"{name}/deferred.py: can't find auto-clear after XML write"

        # Between XML write and auto-clear, there must be an except clause
        # that catches the XML write failure
        between = lines[xml_write_line:auto_clear_line]
        has_except = any("except" in line and "Exception" in line for line in between)
        assert has_except, \
            f"{name}/deferred.py: no except between XML write and auto-clear — " \
            f"XML failure can still abort auto-clear!"
