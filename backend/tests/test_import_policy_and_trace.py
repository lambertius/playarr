"""Regression coverage for import-policy convergence and redacted traces."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.services.import_policy import (
    ImportPolicy,
    policy_from_pipeline_options,
)
from app.services.scraper_trace import build_trace, diagnostic_bundle, persist_trace, redact


def _db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_url_and_library_flags_converge_to_the_same_policy():
    expected = ImportPolicy.from_legacy(
        scrape_wikipedia=True,
        scrape_musicbrainz=False,
        ai_auto=False,
    )
    url_policy = policy_from_pipeline_options({
        "scrape": True,
        "scrape_musicbrainz": False,
    })
    library_policy = policy_from_pipeline_options({
        "options": {"scrape_wikipedia": True, "scrape_musicbrainz": False},
    }, library=True)

    assert expected == url_policy == library_policy
    assert expected.metadata_mode == "wiki_only"
    assert expected.skip_wikipedia is False
    assert expected.skip_musicbrainz is True
    assert expected.skip_ai is True


def test_ai_modes_have_explicit_provider_and_ai_roles():
    proofread = ImportPolicy.from_legacy(ai_auto=True)
    ai_only = ImportPolicy.from_legacy(ai_only=True, scrape_wikipedia=True)

    assert proofread.metadata_mode == "ai_proofread"
    assert proofread.providers == ("wikipedia", "musicbrainz")
    assert proofread.ai_role == "proofread"
    assert ai_only.metadata_mode == "ai_only"
    assert ai_only.providers == ()
    assert ai_only.skip_ai is False


def test_trace_is_structured_persisted_and_redacted():
    db = _db()
    run_id, events = build_trace(
        policy={"metadata_mode": "wiki_only", "api_key": "never-store-me"},
        input_summary={
            "source": r"C:\Private\Music\Artist - Title.mkv",
            "authorization": "Bearer secret",
        },
        metadata={
            "artist": "Artist",
            "title": "Title",
            "year": 2024,
            "scraper_sources_used": ["wikipedia:track"],
            "pipeline_log": [
                "stage:scraper_fetch:started",
                "scraper:wikipedia:track found",
                "stage:scraper_fetch:complete",
                "stage:validation:complete",
                "year:accepted:infobox",
            ],
            "pipeline_failures": [],
        },
        duration_ms=842,
        source_kind="library_file",
    )
    persist_trace(db, run_id, events)
    bundle = diagnostic_bundle(db, run_id)
    encoded = str(bundle)

    assert bundle["run_id"] == run_id
    assert any(event["step"] == "metadata.scraper_fetch" for event in bundle["events"])
    assert "never-store-me" not in encoded
    assert "Private" not in encoded
    assert "Bearer secret" not in encoded
    assert bundle["policy"]["api_key"] == "<redacted>"


def test_redaction_preserves_remote_urls_but_masks_tokens():
    assert redact("https://example.test/video?id=1") == "https://example.test/video?id=1"
    assert "topsecret" not in redact("https://example.test/?token=topsecret")
