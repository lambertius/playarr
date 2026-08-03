"""Regression coverage for import-policy convergence and redacted traces."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.services.import_policy import (
    ImportPolicy,
    policy_from_request,
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


def test_legacy_ai_auto_fallback_means_ai_only_not_ai_auto():
    policy = policy_from_pipeline_options({
        "scrape": True,
        "scrape_musicbrainz": True,
        "ai_auto_fallback": True,
    })

    assert policy.metadata_mode == "ai_only"
    assert policy.ai_role == "ai_only"
    assert policy.providers == ()


def test_direct_source_url_enables_only_its_target_provider():
    request = SimpleNamespace(
        scrape_wikipedia=False,
        scrape_musicbrainz=False,
        wikipedia_url="https://en.wikipedia.org/wiki/Example",
        musicbrainz_url=None,
        ai_auto=False,
        ai_only=False,
    )

    policy = policy_from_request(request)

    assert policy.metadata_mode == "wiki_only"
    assert policy.providers == ("wikipedia",)


def test_scraper_tester_rejects_direct_urls_for_the_wrong_host():
    from app.routers.scraper_test import ScraperTestRequest

    with pytest.raises(ValidationError):
        ScraperTestRequest(
            url="https://youtube.com/watch?v=example",
            wikipedia_url="https://attacker.invalid/pretend-wiki-page",
        )


def test_metadata_stage_records_provider_requests_and_responses(monkeypatch):
    from app.pipeline.import_context import ImportContext, run_metadata_stage
    import app.scraper.unified_metadata as unified

    captured = {}

    def fake_resolver(**kwargs):
        captured.update(kwargs)
        return {
            "artist": "Artist", "title": "Title", "plot": "Biography",
            "scraper_sources_used": ["wikipedia:user_url"],
            "_source_urls": {"wikipedia": "https://en.wikipedia.org/wiki/Example"},
            "pipeline_log": [
                "stage:scraper_fetch:complete",
                "Wikipedia: scraped via user-provided URL successfully",
                "stage:validation:complete",
            ],
            "pipeline_failures": [],
            "ai_source_resolution": None,
            "ai_final_review": None,
        }

    monkeypatch.setattr(unified, "resolve_metadata_unified", fake_resolver)
    context = ImportContext(
        pathway="scraper_test",
        source="https://youtube.com/watch?v=example",
        dry_run=True,
        policy=ImportPolicy.from_legacy(scrape_wikipedia=True),
        wikipedia_url="https://en.wikipedia.org/wiki/Example",
    )

    metadata = run_metadata_stage(context, artist="Artist", title="Title")
    wiki_event = next(
        event for event in metadata["structured_trace"]
        if event["step"] == "wikipedia_fetch"
    )
    imdb_event = next(
        event for event in metadata["structured_trace"]
        if event["step"] == "imdb_lookup"
    )

    assert captured["wikipedia_url"] == "https://en.wikipedia.org/wiki/Example"
    assert captured["skip_musicbrainz"] is True
    assert captured["skip_ai"] is True
    assert wiki_event["request"]["target"] == "direct_url"
    assert wiki_event["response"]["fields"]["plot"] == "Biography"
    assert imdb_event["status"] == "skipped"
    assert imdb_event["request"] == {}


def test_production_context_rejects_cross_provider_direct_urls():
    from app.pipeline.import_context import ImportContext

    with pytest.raises(ValidationError):
        ImportContext(
            pathway="metadata_action",
            source="video:1",
            policy=ImportPolicy.from_legacy(scrape_wikipedia=True),
            wikipedia_url="https://musicbrainz.org/recording/deadbeef",
        )


def test_wiki_only_artwork_diagnostics_never_call_musicbrainz_or_caa(monkeypatch):
    from app.routers.scraper_test import (
        _album_artwork_for_policy,
        _artist_artwork_for_policy,
        _caa_artwork_for_policy,
    )
    import app.scraper.artist_album_scraper as artwork
    import app.scraper.artwork_selection as selection

    calls = []
    monkeypatch.setattr(
        artwork, "get_artist_artwork_wikipedia",
        lambda *args, **kwargs: calls.append("wiki_artist") or {"image_url": "https://img.test/artist.jpg"},
    )
    monkeypatch.setattr(
        artwork, "get_artist_artwork_musicbrainz",
        lambda *args, **kwargs: calls.append("mb_artist") or {},
    )
    monkeypatch.setattr(
        artwork, "get_album_artwork_wikipedia",
        lambda *args, **kwargs: calls.append("wiki_album") or {"image_url": "https://img.test/album.jpg"},
    )
    monkeypatch.setattr(
        artwork, "get_album_artwork_musicbrainz",
        lambda *args, **kwargs: calls.append("mb_album") or {},
    )
    monkeypatch.setattr(
        selection, "fetch_caa_artwork",
        lambda **kwargs: calls.append("caa") or (None, None, None),
    )
    policy = ImportPolicy.from_legacy(scrape_wikipedia=True)

    _artist_artwork_for_policy(policy, "Artist", None, None, lambda _: None)
    _album_artwork_for_policy(policy, "Album", "Artist", None, lambda _: None)
    _caa_artwork_for_policy(policy, {}, lambda _: None)

    assert calls == ["wiki_artist", "wiki_album"]


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
