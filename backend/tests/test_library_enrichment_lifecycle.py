from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import VideoItem
from app.routers.library import (
    _apply_enrichment_filter,
    _enrichment_lifecycle,
    _enrichment_sort_expression,
)


def lifecycle(state=None, category=None):
    return _enrichment_lifecycle(state, category)[0]


def test_unrelated_pipeline_steps_do_not_look_partially_ai_enriched():
    assert lifecycle({"metadata_scraped": {"completed": True}}) == "not_requested"


def test_ai_lifecycle_states_are_mutually_explainable():
    assert lifecycle({"ai_enriched": {"status": "queued"}}) == "queued"
    assert lifecycle({"ai_enriched": {"status": "running"}}) == "running"
    # Scene analysis is optional. AI-only work is complete when that is all
    # the user requested.
    assert lifecycle({"ai_enriched": {"completed": True}}) == "complete"
    assert lifecycle({
        "ai_enriched": {"completed": True},
        "scenes_analyzed": {"completed": False},
    }) == "partial"
    assert lifecycle({
        "ai_enriched": {"completed": True},
        "scenes_analyzed": {"completed": True},
    }) == "complete"
    assert lifecycle({"ai_enriched": {"error": "provider unavailable"}}) == "failed"


def test_stale_status_matches_the_stale_filter_vocabulary():
    assert lifecycle({"ai_enriched": {"completed": True, "status": "stale"}}) == "stale"


def test_database_filters_and_sort_use_the_same_lifecycle_vocabulary():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    states = {
        "not-run": {"metadata_scraped": {"completed": True}},
        "queued": {"ai_enriched": {"status": "queued"}},
        "running": {"scenes_analyzed": {"status": "running"}},
        "partial": {"ai_enriched": {"completed": True}, "scenes_analyzed": {"completed": False}},
        "complete": {"ai_enriched": {"completed": True}},
        "failed": {"ai_enriched": {"error": "provider unavailable"}},
        "stale": {"ai_enriched": {"completed": True, "status": "stale"}},
    }
    for title, processing_state in states.items():
        session.add(VideoItem(
            artist="Artist",
            title=title,
            processing_state=processing_state,
            review_category=None,
        ))
    session.commit()

    expected = {
        "not_requested": "not-run",
        "queued": "queued",
        "running": "running",
        "partial": "partial",
        "complete": "complete",
        "failed": "failed",
        "stale": "stale",
    }
    for status, title in expected.items():
        matches = _apply_enrichment_filter(session.query(VideoItem), status).all()
        assert [video.title for video in matches] == [title]

    ordered = session.query(VideoItem).order_by(_enrichment_sort_expression()).all()
    assert [video.title for video in ordered] == list(states)
    session.close()
