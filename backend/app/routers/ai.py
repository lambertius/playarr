"""
AI Router — API endpoints for AI metadata enrichment, scene analysis,
fingerprinting, undo, and connection testing.

Endpoints:
- POST /api/ai/{video_id}/enrich       — Run AI metadata enrichment
- GET  /api/ai/{video_id}/comparison    — Get scraped vs AI comparison
- POST /api/ai/{video_id}/apply         — Apply specific AI fields
- POST /api/ai/{video_id}/undo          — Undo an AI enrichment
- POST /api/ai/{video_id}/fingerprint   — Run audio fingerprint identification
- POST /api/ai/{video_id}/scenes        — Run scene analysis
- GET  /api/ai/{video_id}/scenes        — Get scene analysis results
- POST /api/ai/{video_id}/thumbnail     — Select a thumbnail
- GET  /api/ai/{video_id}/thumbnails                    — List thumbnail candidates
- GET  /api/ai/{video_id}/thumbnails/{id}/image         — Serve thumbnail image
- GET  /api/ai/{video_id}/results       — List all AI results for a video
- GET  /api/ai/settings                 — Get AI configuration
- PUT  /api/ai/settings                 — Update AI configuration
- POST /api/ai/test                     — Test AI provider connection
- POST /api/ai/batch/enrich             — Batch enrich multiple videos
- POST /api/ai/batch/scenes             — Batch scene analysis
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.database import get_db, SessionLocal
from app.models import VideoItem, AppSetting
from app.ai.models import AIMetadataResult, AISceneAnalysis, AIThumbnail, AIResultStatus
from app.ai.schemas import (
    AIEnrichRequest, AIComparisonResponse, AIApplyFieldsRequest,
    AIMetadataResultOut, SceneAnalysisRequest, SceneAnalysisOut,
    AIThumbnailOut, SelectThumbnailRequest, AISettingsOut, AISettingsUpdate,
    AITestConnectionRequest, AITestConnectionResponse, AIUndoRequest,
    FingerprintResultOut, FingerprintMatchOut,
    ModelCatalogOut, ModelInfoOut, RoutingPreviewOut, RoutingPreviewEntry,
    ModelAvailabilityOut, ModelAvailabilityEntry,
    AIPromptSettingsOut, AIPromptSettingsUpdate,
)
from app.ai.metadata_service import (
    enrich_video_metadata, apply_ai_fields, compare_metadata, undo_enrichment,
)
from app.ai.scene_analysis import analyze_scenes, select_thumbnail
from app.ai.model_catalog import get_model_catalog, validate_model_id
from app.ai.model_router import get_model_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["AI"])


def _set_processing_flag(video: VideoItem, step: str, *, method: str = "manual", version: str = "1.0"):
    """Set a processing step flag on a video's processing_state JSON."""
    state = dict(video.processing_state or {})
    state[step] = {
        "completed": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "version": version,
    }
    video.processing_state = state
    flag_modified(video, "processing_state")


def _backfill_platform_metadata_if_needed(db: Session, video: VideoItem):
    """Re-fetch platform metadata from yt-dlp if Source is missing channel_name."""
    if not video.sources:
        return
    source = video.sources[0]
    if source.channel_name:
        return
    url = source.canonical_url or source.original_url
    if not url:
        return
    try:
        from app.services.downloader import get_available_formats, extract_metadata_from_ytdlp
        _, info = get_available_formats(url)
        meta = extract_metadata_from_ytdlp(info)
        source.channel_name = meta.get("channel") or meta.get("uploader")
        source.platform_title = meta.get("title")
        source.platform_description = meta.get("description")
        source.platform_tags = meta.get("tags")
        db.flush()
    except Exception as e:
        logger.warning(f"Platform metadata backfill failed for video {video.id}: {e}")


# ---------------------------------------------------------------------------
# Metadata Enrichment
# ---------------------------------------------------------------------------

@router.post("/{video_id}/enrich", response_model=AIMetadataResultOut)
def enrich_metadata(
    video_id: int,
    request: AIEnrichRequest = AIEnrichRequest(),
    db: Session = Depends(get_db),
):
    """Run AI metadata enrichment for a video."""
    video = db.query(VideoItem).get(video_id)
    if not video:
        raise HTTPException(404, "Video not found")

    # Backfill platform metadata if missing (for older imports)
    _backfill_platform_metadata_if_needed(db, video)

    # If review_description_only, override fields to only enrich plot
    requested_fields = request.fields
    if request.review_description_only:
        requested_fields = ["plot"]

    result = enrich_video_metadata(
        db, video_id,
        provider_name=request.provider,
        auto_apply=request.auto_apply,
        force=request.force,
        requested_fields=requested_fields,
        run_fingerprint=request.run_fingerprint,
        skip_mismatch_check=request.skip_mismatch_check,
        review_description_only=request.review_description_only,
    )

    if not result:
        raise HTTPException(400, "AI provider not configured or disabled")

    # Mark processing step complete
    _set_processing_flag(video, "metadata_ai_analyzed", method="ai")
    db.commit()

    return _ai_result_to_out(result)


@router.get("/{video_id}/comparison", response_model=AIComparisonResponse)
def get_comparison(video_id: int, db: Session = Depends(get_db)):
    """Get scraped vs AI metadata comparison for a video."""
    video = db.query(VideoItem).get(video_id)
    if not video:
        raise HTTPException(404, "Video not found")

    data = compare_metadata(db, video_id)
    return data


@router.post("/{video_id}/apply")
def apply_fields(
    video_id: int,
    request: AIApplyFieldsRequest,
    db: Session = Depends(get_db),
):
    """Apply specific AI fields to a video."""
    try:
        video = apply_ai_fields(
            db, video_id, request.ai_result_id, request.fields,
            rename_files=request.rename_files,
        )
        return {"message": "Fields applied", "fields": request.fields}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/{video_id}/undo")
def undo_ai_fields(
    video_id: int,
    request: AIUndoRequest,
    db: Session = Depends(get_db),
):
    """Undo an AI enrichment by restoring previous metadata."""
    video = undo_enrichment(db, video_id, request.ai_result_id)
    if not video:
        raise HTTPException(404, "No snapshot found for undo")
    return {"message": "Metadata restored", "video_id": video_id}


@router.post("/{video_id}/fingerprint")
def run_fingerprint(
    video_id: int,
    db: Session = Depends(get_db),
):
    """Run audio fingerprint identification for a video."""
    video = db.query(VideoItem).get(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if not video.file_path:
        raise HTTPException(400, "Video has no file")

    import os
    if not os.path.isfile(video.file_path):
        raise HTTPException(400, "Video file not found on disk")

    try:
        from app.ai.fingerprint_service import identify_track
        from app.config import get_settings
        settings = get_settings()
        result = identify_track(
            file_path=video.file_path,
            ffmpeg_path=settings.resolved_ffmpeg,
        )

        matches = []
        for m in result.matches:
            matches.append(FingerprintMatchOut(
                artist=m.artist,
                title=m.title,
                album=m.album,
                year=m.year,
                mb_recording_id=m.mb_recording_id,
                confidence=m.confidence,
            ))

        best = None
        if result.best_match:
            best = FingerprintMatchOut(
                artist=result.best_match.artist,
                title=result.best_match.title,
                album=result.best_match.album,
                year=result.best_match.year,
                mb_recording_id=result.best_match.mb_recording_id,
                confidence=result.best_match.confidence,
            )

        # Mark processing step complete
        _set_processing_flag(video, "track_identified", method="fingerprint")
        db.commit()

        return FingerprintResultOut(
            fpcalc_available=result.fpcalc_available,
            match_count=len(matches),
            best_match=best,
            matches=matches,
            error=result.error,
        )
    except Exception as e:
        logger.error(f"Fingerprint failed for video {video_id}: {e}")
        raise HTTPException(500, f"Fingerprint identification failed: {e}")


@router.get("/{video_id}/results", response_model=List[AIMetadataResultOut])
def list_results(video_id: int, db: Session = Depends(get_db)):
    """List all AI metadata results for a video."""
    results = (
        db.query(AIMetadataResult)
        .filter(AIMetadataResult.video_id == video_id)
        .order_by(AIMetadataResult.created_at.desc())
        .all()
    )
    return [_ai_result_to_out(r) for r in results]


@router.post("/{video_id}/dismiss-scrape")
def dismiss_scrape_result(video_id: int, db: Session = Depends(get_db)):
    """Dismiss the latest scrape metadata result for a video (persists in DB)."""
    from datetime import datetime, timezone
    _SCRAPE_MODELS = {"ai_auto_analyse", "wikipedia_scrape", "musicbrainz_scrape"}
    result = (
        db.query(AIMetadataResult)
        .filter(
            AIMetadataResult.video_id == video_id,
            AIMetadataResult.model_name.in_(_SCRAPE_MODELS),
        )
        .order_by(AIMetadataResult.created_at.desc())
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="No scrape result found")
    result.dismissed_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "dismissed_id": result.id}


# ---------------------------------------------------------------------------
# Scene Analysis
# ---------------------------------------------------------------------------

@router.post("/{video_id}/scenes", response_model=SceneAnalysisOut)
def run_scene_analysis(
    video_id: int,
    request: SceneAnalysisRequest = SceneAnalysisRequest(),
    db: Session = Depends(get_db),
):
    """Run scene analysis and thumbnail extraction for a video."""
    video = db.query(VideoItem).get(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if not video.file_path:
        raise HTTPException(400, "Video has no file to analyze")

    analysis = analyze_scenes(
        db, video_id,
        threshold=request.threshold,
        max_thumbnails=request.max_thumbnails,
        force=request.force,
    )

    if not analysis:
        raise HTTPException(500, "Scene analysis failed")

    # Mark processing step complete
    _set_processing_flag(video, "scenes_analyzed", method="manual")
    db.commit()

    # Persist to disk: update XML sidecar + copy thumb files to video folder
    try:
        from app.services.playarr_xml import write_playarr_xml
        write_playarr_xml(video, db)

        import shutil as _shutil
        thumbs = db.query(AIThumbnail).filter(AIThumbnail.video_id == video_id).all()
        for t in thumbs:
            if t.file_path and os.path.isfile(t.file_path) and video.folder_path:
                dest = os.path.join(video.folder_path, os.path.basename(t.file_path))
                if not os.path.isfile(dest):
                    _shutil.copy2(t.file_path, dest)
    except Exception:
        pass  # best-effort; don't fail the response

    return _scene_analysis_to_out(analysis)


@router.get("/{video_id}/scenes", response_model=SceneAnalysisOut)
def get_scene_analysis(video_id: int, db: Session = Depends(get_db)):
    """Get the latest scene analysis for a video."""
    analysis = (
        db.query(AISceneAnalysis)
        .filter(AISceneAnalysis.video_id == video_id)
        .order_by(AISceneAnalysis.created_at.desc())
        .first()
    )

    if not analysis:
        raise HTTPException(404, "No scene analysis found for this video")

    return _scene_analysis_to_out(analysis)


@router.get("/{video_id}/thumbnails", response_model=List[AIThumbnailOut])
def list_thumbnails(video_id: int, db: Session = Depends(get_db)):
    """List all thumbnail candidates for a video, ranked by score."""
    thumbnails = (
        db.query(AIThumbnail)
        .filter(AIThumbnail.video_id == video_id)
        .order_by(AIThumbnail.score_overall.desc())
        .all()
    )
    return [_thumbnail_to_out(t) for t in thumbnails]


@router.get("/{video_id}/thumbnails/{thumbnail_id}/image")
def get_thumbnail_image(video_id: int, thumbnail_id: int, db: Session = Depends(get_db)):
    """Serve a thumbnail image file."""
    thumb = (
        db.query(AIThumbnail)
        .filter(AIThumbnail.id == thumbnail_id, AIThumbnail.video_id == video_id)
        .first()
    )
    if not thumb:
        raise HTTPException(404, "Thumbnail not found")
    if not thumb.file_path or not os.path.isfile(thumb.file_path):
        raise HTTPException(404, "Thumbnail file not found on disk")
    return FileResponse(thumb.file_path, media_type="image/jpeg")


@router.post("/{video_id}/thumbnail")
def choose_thumbnail(
    video_id: int,
    request: SelectThumbnailRequest,
    db: Session = Depends(get_db),
):
    """Select a specific thumbnail as the video's poster."""
    thumb = select_thumbnail(db, video_id, request.thumbnail_id, apply_to_poster=request.apply_to_poster)
    if not thumb:
        raise HTTPException(404, "Thumbnail not found")
    return {"message": "Thumbnail selected", "thumbnail_id": thumb.id}


# ---------------------------------------------------------------------------
# Model Catalog & Routing Preview
# ---------------------------------------------------------------------------

@router.get("/models", response_model=ModelCatalogOut)
def get_models(
    provider: str,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
):
    """Get available models for a provider."""
    settings = _load_ai_settings(db)
    local_url = settings.local_llm_base_url
    catalog = get_model_catalog(provider, local_base_url=local_url, force_refresh=force_refresh)
    return ModelCatalogOut(
        provider=catalog.provider,
        models=[ModelInfoOut(**m.to_dict()) for m in catalog.models],
        defaults=catalog.defaults,
        updated_at=catalog.updated_at,
    )


@router.post("/models/test-availability", response_model=ModelAvailabilityOut)
def test_model_availability_endpoint(
    force: bool = False,
    db: Session = Depends(get_db),
):
    """Test which models are accessible with the current API key.

    Results are cached for 1 hour. Pass force=true to bypass cache.
    """
    from app.ai.model_catalog import (
        test_model_availability as _test_avail,
        get_cached_availability,
    )

    settings = _load_ai_settings(db)
    provider_name = settings.provider
    if not provider_name or provider_name == "none":
        raise HTTPException(400, "No AI provider configured")

    # Resolve API key
    key_map = {
        "openai": "openai_api_key",
        "gemini": "gemini_api_key",
        "claude": "claude_api_key",
    }
    api_key = ""
    db_key_name = key_map.get(provider_name)
    if db_key_name:
        row = db.query(AppSetting).filter(
            AppSetting.key == db_key_name,
            AppSetting.user_id.is_(None),
        ).first()
        if row:
            api_key = row.value
    if not api_key:
        raise HTTPException(400, f"No API key configured for {provider_name}")

    # Check cache first
    was_cached = False
    if not force:
        cached = get_cached_availability(provider_name)
        if cached is not None:
            was_cached = True
            results_dict = cached
        else:
            results_dict = _test_avail(
                provider=provider_name, api_key=api_key,
                base_url=settings.local_llm_base_url, force=True,
            )
    else:
        results_dict = _test_avail(
            provider=provider_name, api_key=api_key,
            base_url=settings.local_llm_base_url, force=True,
        )

    from datetime import datetime, timezone
    return ModelAvailabilityOut(
        provider=provider_name,
        results=[
            ModelAvailabilityEntry(**r.to_dict())
            for r in results_dict.values()
        ],
        cached=was_cached,
        tested_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/routing-preview", response_model=RoutingPreviewOut)
def get_routing_preview(db: Session = Depends(get_db)):
    """Get a preview of what model the router would select for each task."""
    settings = _load_ai_settings(db)
    router_inst = get_model_router(provider_name=settings.provider)
    preview = router_inst.get_routing_preview()
    return RoutingPreviewOut(
        provider=settings.provider,
        mode=settings.model_selection_mode,
        entries=[RoutingPreviewEntry(**e) for e in preview],
    )


# ---------------------------------------------------------------------------
# Test Connection
# ---------------------------------------------------------------------------

@router.post("/test", response_model=AITestConnectionResponse)
def test_connection(
    request: AITestConnectionRequest,
    db: Session = Depends(get_db),
):
    """Test AI provider connection with a simple request."""
    import time as _time
    from app.ai.provider_factory import get_ai_provider

    try:
        # Determine which model to test with
        test_model = request.model
        provider_name = request.provider

        # Load the saved settings so we can resolve provider/key from DB
        saved = _load_ai_settings(db)
        if not provider_name:
            provider_name = saved.provider

        # If no explicit API key in the request, load from DB
        test_api_key = request.api_key
        if not test_api_key and provider_name:
            key_map = {
                "openai": "openai_api_key",
                "gemini": "gemini_api_key",
                "claude": "claude_api_key",
            }
            db_key_name = key_map.get(provider_name)
            if db_key_name:
                row = db.query(AppSetting).filter(
                    AppSetting.key == db_key_name,
                    AppSetting.user_id.is_(None),
                ).first()
                if row:
                    test_api_key = row.value

        if not test_model and provider_name:
            # Use the model that the router would actually pick
            try:
                router_inst = get_model_router(provider_name=provider_name)
                sel = router_inst.select_model("enrichment")
                if sel.model and sel.model != "configured" and sel.model != "unknown":
                    test_model = sel.model
            except Exception:
                pass

        t0 = _time.monotonic()
        provider = get_ai_provider(
            provider_name=provider_name,
            api_key=test_api_key,
            model=test_model,
            base_url=request.base_url,
        )
        if not provider:
            return AITestConnectionResponse(
                success=False,
                provider=provider_name or "",
                message="Provider not available or API key missing",
            )

        response = provider.enrich_metadata(
            scraped={
                "artist": "Queen",
                "title": "Bohemian Rhapsody",
                "album": None,
                "year": None,
                "plot": None,
                "genres": [],
            },
            video_filename="Queen - Bohemian Rhapsody.mp4",
        )
        elapsed_ms = int((_time.monotonic() - t0) * 1000)

        return AITestConnectionResponse(
            success=True,
            provider=provider_name or "",
            model_name=response.model_name or test_model or "unknown",
            message=f"Connected to {provider.name} using {response.model_name or test_model or 'unknown model'}",
            tokens_used=response.tokens_used,
            response_time_ms=elapsed_ms,
        )

    except Exception as e:
        return AITestConnectionResponse(
            success=False,
            provider=request.provider or "",
            message=f"Connection failed: {str(e)}",
        )


# ---------------------------------------------------------------------------
# AI Settings
# ---------------------------------------------------------------------------

@router.get("/settings", response_model=AISettingsOut)
def get_ai_settings(db: Session = Depends(get_db)):
    """Get current AI configuration."""
    return _load_ai_settings(db)


@router.put("/settings", response_model=AISettingsOut)
def update_ai_settings(
    update: AISettingsUpdate,
    db: Session = Depends(get_db),
):
    """Update AI configuration."""
    AI_SETTINGS_MAP = {
        "provider": ("ai_provider", "string"),
        "openai_api_key": ("openai_api_key", "string"),
        "gemini_api_key": ("gemini_api_key", "string"),
        "claude_api_key": ("claude_api_key", "string"),
        "local_llm_base_url": ("local_llm_base_url", "string"),
        "local_llm_model": ("local_llm_model", "string"),
        "auto_enrich_on_import": ("ai_auto_enrich", "bool"),
        "auto_scene_analysis": ("ai_auto_scenes", "bool"),
        "auto_apply_threshold": ("ai_auto_apply_threshold", "float"),
        "model_selection_mode": ("ai_model_selection_mode", "string"),
        "model_default": ("ai_model_default", "string"),
        "model_fallback": ("ai_model_fallback", "string"),
        "model_metadata": ("ai_model_metadata", "string"),
        "model_verification": ("ai_model_verification", "string"),
        "model_scene": ("ai_model_scene", "string"),
        "auto_tier_preference": ("ai_auto_tier_preference", "string"),
        "rename_on_metadata_update": ("ai_rename_on_update", "bool"),
        "scene_analysis_mode": ("ai_scene_mode", "string"),
        "acoustid_api_key": ("acoustid_api_key", "string"),
    }

    data = update.model_dump(exclude_none=True)

    # Handle enrichable_fields specially (JSON list)
    if "enrichable_fields" in data:
        _upsert_setting(db, "ai_enrichable_fields", json.dumps(data["enrichable_fields"]), "json")

    for field_name, value in data.items():
        if field_name not in AI_SETTINGS_MAP:
            continue
        db_key, value_type = AI_SETTINGS_MAP[field_name]
        _upsert_setting(db, db_key, str(value), value_type)

    db.commit()
    return _load_ai_settings(db)


# ---------------------------------------------------------------------------
# Batch Operations
# ---------------------------------------------------------------------------

@router.post("/batch/enrich")
def batch_enrich(
    video_ids: Optional[List[int]] = None,
    provider: Optional[str] = None,
    auto_apply: bool = False,
    force: bool = False,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
):
    """Batch AI enrichment for multiple videos."""
    if video_ids:
        videos = db.query(VideoItem).filter(VideoItem.id.in_(video_ids)).all()
    else:
        videos = db.query(VideoItem).all()

    count = len(videos)
    ids = [v.id for v in videos]

    # Run in background
    background_tasks.add_task(
        _batch_enrich_task, ids, provider, auto_apply, force,
    )

    return {"message": f"AI enrichment started for {count} videos", "video_count": count}


@router.post("/batch/scenes")
def batch_scenes(
    video_ids: Optional[List[int]] = None,
    force: bool = False,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
):
    """Batch scene analysis for multiple videos."""
    if video_ids:
        videos = db.query(VideoItem).filter(VideoItem.id.in_(video_ids)).all()
    else:
        videos = db.query(VideoItem).filter(VideoItem.file_path.isnot(None)).all()

    count = len(videos)
    ids = [v.id for v in videos]

    background_tasks.add_task(_batch_scenes_task, ids, force)

    return {"message": f"Scene analysis started for {count} videos", "video_count": count}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ai_result_to_out(r: AIMetadataResult) -> AIMetadataResultOut:
    return AIMetadataResultOut(
        id=r.id,
        video_id=r.video_id,
        provider=r.provider.value,
        model_name=r.model_name,
        model_task=r.model_task,
        status=r.status.value,
        ai_artist=r.ai_artist,
        ai_title=r.ai_title,
        ai_album=r.ai_album,
        ai_year=r.ai_year,
        ai_plot=r.ai_plot,
        ai_genres=r.ai_genres,
        ai_director=r.ai_director,
        ai_studio=r.ai_studio,
        ai_actors=r.ai_actors,
        ai_tags=r.ai_tags,
        confidence_score=r.confidence_score,
        field_scores=r.field_scores,
        accepted_fields=r.accepted_fields,
        verification_status=r.verification_status,
        requested_fields=r.requested_fields,
        mismatch_score=r.mismatch_score,
        mismatch_signals=r.mismatch_signals,
        fingerprint_result=r.fingerprint_result,
        change_summary=r.change_summary,
        tokens_used=r.tokens_used,
        error_message=r.error_message,
        created_at=r.created_at.isoformat() if r.created_at else None,
        completed_at=r.completed_at.isoformat() if r.completed_at else None,
        dismissed_at=r.dismissed_at.isoformat() if getattr(r, 'dismissed_at', None) else None,
    )


def _thumbnail_to_out(t: AIThumbnail) -> AIThumbnailOut:
    return AIThumbnailOut(
        id=t.id,
        video_id=t.video_id,
        timestamp_sec=t.timestamp_sec,
        file_path=t.file_path,
        score_sharpness=t.score_sharpness,
        score_contrast=t.score_contrast,
        score_color_variance=t.score_color_variance,
        score_composition=t.score_composition,
        score_overall=t.score_overall,
        is_selected=t.is_selected,
        provenance=t.provenance,
    )


def _scene_analysis_to_out(a: AISceneAnalysis) -> SceneAnalysisOut:
    thumbnails = [_thumbnail_to_out(t) for t in (a.thumbnails or [])]
    return SceneAnalysisOut(
        id=a.id,
        video_id=a.video_id,
        status=a.status.value,
        total_scenes=a.total_scenes,
        duration_seconds=a.duration_seconds,
        scenes=a.scenes,
        thumbnails=thumbnails,
        error_message=a.error_message,
        created_at=a.created_at.isoformat() if a.created_at else None,
        completed_at=a.completed_at.isoformat() if a.completed_at else None,
    )


_ALL_AI_SETTING_KEYS = [
    "ai_provider", "openai_api_key", "gemini_api_key", "claude_api_key",
    "local_llm_base_url", "local_llm_model",
    "ai_auto_enrich", "ai_auto_scenes", "ai_auto_apply_threshold",
    "ai_model_selection_mode", "ai_model_default", "ai_model_fallback",
    "ai_model_metadata", "ai_model_verification", "ai_model_scene",
    "ai_auto_tier_preference",
    "ai_enrichable_fields", "ai_rename_on_update", "ai_scene_mode",
    "acoustid_api_key",
]


def _load_ai_settings(db: Session) -> AISettingsOut:
    """Load AI settings from the database."""
    settings_map = {}
    rows = db.query(AppSetting).filter(
        AppSetting.key.in_(_ALL_AI_SETTING_KEYS),
        AppSetting.user_id.is_(None),
    ).all()

    for s in rows:
        settings_map[s.key] = s.value

    # Parse enrichable_fields from JSON
    enrichable_fields = None
    ef_raw = settings_map.get("ai_enrichable_fields")
    if ef_raw:
        try:
            enrichable_fields = json.loads(ef_raw)
        except (json.JSONDecodeError, TypeError):
            enrichable_fields = None

    # Fall back to all fields if not set
    if enrichable_fields is None:
        from app.ai.schemas import ALL_ENRICHABLE_FIELDS
        enrichable_fields = ALL_ENRICHABLE_FIELDS.copy()

    return AISettingsOut(
        provider=settings_map.get("ai_provider", "none"),
        openai_api_key_set=bool(settings_map.get("openai_api_key")),
        gemini_api_key_set=bool(settings_map.get("gemini_api_key")),
        claude_api_key_set=bool(settings_map.get("claude_api_key")),
        local_llm_base_url=settings_map.get("local_llm_base_url", "http://localhost:11434/v1"),
        local_llm_model=settings_map.get("local_llm_model", "llama3"),
        auto_enrich_on_import=settings_map.get("ai_auto_enrich", "false").lower() == "true",
        auto_scene_analysis=settings_map.get("ai_auto_scenes", "false").lower() == "true",
        auto_apply_threshold=float(settings_map.get("ai_auto_apply_threshold", "0.85")),
        model_selection_mode=settings_map.get("ai_model_selection_mode", "auto"),
        model_default=settings_map.get("ai_model_default"),
        model_fallback=settings_map.get("ai_model_fallback"),
        model_metadata=settings_map.get("ai_model_metadata"),
        model_verification=settings_map.get("ai_model_verification"),
        model_scene=settings_map.get("ai_model_scene"),
        auto_tier_preference=settings_map.get("ai_auto_tier_preference", "balanced"),
        enrichable_fields=enrichable_fields,
        rename_on_metadata_update=settings_map.get("ai_rename_on_update", "false").lower() == "true",
        scene_analysis_mode=settings_map.get("ai_scene_mode", "heuristic"),
        acoustid_api_key_set=bool(settings_map.get("acoustid_api_key")),
    )


def _upsert_setting(db: Session, key: str, value: str, value_type: str = "string"):
    """Insert or update a global setting."""
    setting = db.query(AppSetting).filter(
        AppSetting.key == key,
        AppSetting.user_id.is_(None),
    ).first()

    if setting:
        setting.value = value
        setting.value_type = value_type
    else:
        setting = AppSetting(key=key, value=value, value_type=value_type)
        db.add(setting)


# ---------------------------------------------------------------------------
# AI Prompt Settings
# ---------------------------------------------------------------------------

_PROMPT_SETTING_KEYS = ["ai_system_prompt", "ai_enrichment_prompt", "ai_review_prompt"]


def _load_prompt_settings(db: Session) -> AIPromptSettingsOut:
    """Load AI prompt templates from the database, falling back to defaults."""
    from app.ai.prompt_builder import SYSTEM_PROMPT, SMART_ENRICHMENT_PROMPT, REVIEW_DESCRIPTION_PROMPT

    rows = db.query(AppSetting).filter(
        AppSetting.key.in_(_PROMPT_SETTING_KEYS),
        AppSetting.user_id.is_(None),
    ).all()
    m = {r.key: r.value for r in rows}

    system = m.get("ai_system_prompt") or ""
    enrichment = m.get("ai_enrichment_prompt") or ""
    review = m.get("ai_review_prompt") or ""

    return AIPromptSettingsOut(
        system_prompt=system or SYSTEM_PROMPT,
        enrichment_prompt=enrichment or SMART_ENRICHMENT_PROMPT,
        review_prompt=review or REVIEW_DESCRIPTION_PROMPT,
        is_default_system=not bool(system),
        is_default_enrichment=not bool(enrichment),
        is_default_review=not bool(review),
    )


def load_custom_prompts(db: Session) -> dict:
    """Load custom prompt overrides from DB. Returns dict with keys only for non-default prompts."""
    rows = db.query(AppSetting).filter(
        AppSetting.key.in_(_PROMPT_SETTING_KEYS),
        AppSetting.user_id.is_(None),
    ).all()
    m = {r.key: r.value for r in rows}
    result = {}
    if m.get("ai_system_prompt"):
        result["system_prompt"] = m["ai_system_prompt"]
    if m.get("ai_enrichment_prompt"):
        result["enrichment_prompt"] = m["ai_enrichment_prompt"]
    if m.get("ai_review_prompt"):
        result["review_prompt"] = m["ai_review_prompt"]
    return result


@router.get("/prompts", response_model=AIPromptSettingsOut)
def get_prompt_settings(db: Session = Depends(get_db)):
    """Get current AI prompt templates (with defaults if not customized)."""
    return _load_prompt_settings(db)


@router.put("/prompts", response_model=AIPromptSettingsOut)
def update_prompt_settings(update: AIPromptSettingsUpdate, db: Session = Depends(get_db)):
    """Update AI prompt templates. Send empty string to reset a prompt to default."""
    if update.system_prompt is not None:
        if update.system_prompt.strip():
            _upsert_setting(db, "ai_system_prompt", update.system_prompt, "string")
        else:
            # Reset to default: delete the setting
            db.query(AppSetting).filter(
                AppSetting.key == "ai_system_prompt", AppSetting.user_id.is_(None)
            ).delete()

    if update.enrichment_prompt is not None:
        if update.enrichment_prompt.strip():
            _upsert_setting(db, "ai_enrichment_prompt", update.enrichment_prompt, "string")
        else:
            db.query(AppSetting).filter(
                AppSetting.key == "ai_enrichment_prompt", AppSetting.user_id.is_(None)
            ).delete()

    if update.review_prompt is not None:
        if update.review_prompt.strip():
            _upsert_setting(db, "ai_review_prompt", update.review_prompt, "string")
        else:
            db.query(AppSetting).filter(
                AppSetting.key == "ai_review_prompt", AppSetting.user_id.is_(None)
            ).delete()

    db.commit()
    return _load_prompt_settings(db)


def _batch_enrich_task(
    video_ids: List[int],
    provider: Optional[str],
    auto_apply: bool,
    force: bool,
):
    """Background task: batch AI enrichment."""
    db = SessionLocal()
    try:
        for vid in video_ids:
            try:
                enrich_video_metadata(
                    db, vid,
                    provider_name=provider,
                    auto_apply=auto_apply,
                    force=force,
                )
            except Exception as e:
                logger.error(f"Batch enrich failed for video {vid}: {e}")
                db.rollback()
    finally:
        db.close()


def _batch_scenes_task(video_ids: List[int], force: bool):
    """Background task: batch scene analysis."""
    db = SessionLocal()
    try:
        for vid in video_ids:
            try:
                analyze_scenes(db, vid, force=force)

                # Persist to disk: update XML + copy thumbs to video folder
                try:
                    video = db.query(VideoItem).get(vid)
                    if video and video.folder_path and os.path.isdir(video.folder_path):
                        from app.services.playarr_xml import write_playarr_xml
                        write_playarr_xml(video, db)

                        import shutil as _shutil
                        thumbs = db.query(AIThumbnail).filter(AIThumbnail.video_id == vid).all()
                        for t in thumbs:
                            if t.file_path and os.path.isfile(t.file_path):
                                dest = os.path.join(video.folder_path, os.path.basename(t.file_path))
                                if not os.path.isfile(dest):
                                    _shutil.copy2(t.file_path, dest)
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Batch scene analysis failed for video {vid}: {e}")
                db.rollback()
    finally:
        db.close()
