"""
Artwork API — Validation, repair, and management of artwork assets.

Endpoints:
- POST /api/artwork/validate/{video_id}  — Validate artwork for a single video
- POST /api/artwork/repair               — Repair all artwork in the library
- POST /api/artwork/repair/cached        — Repair cached entity assets only
- POST /api/artwork/repair/media         — Repair media (video-level) assets only
- GET  /api/artwork/status               — Get overall artwork health summary
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import VideoItem, MediaAsset
from app.metadata.models import CachedAsset
from app.services.artwork_service import (
    validate_existing_cached_asset,
    validate_existing_media_asset,
    repair_cached_assets,
    repair_media_assets,
    RepairReport,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/artwork", tags=["Artwork"])


class RepairRequest(BaseModel):
    refetch: bool = False  # Whether to attempt re-downloading invalid/missing assets


class RepairResponse(BaseModel):
    total_scanned: int
    valid: int
    invalid: int
    missing: int
    deleted: int
    refetched: int
    errors: list[str]


class ArtworkHealthResponse(BaseModel):
    cached_total: int
    cached_valid: int
    cached_invalid: int
    cached_missing: int
    media_total: int
    media_valid: int
    media_invalid: int
    media_missing: int


class VideoValidateResponse(BaseModel):
    video_id: int
    assets_checked: int
    valid: int
    invalid: int
    details: list[dict]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
def artwork_status(db: Session = Depends(get_db)) -> ArtworkHealthResponse:
    """Get overall artwork health summary."""
    cached_total = db.query(CachedAsset).count()
    cached_valid = db.query(CachedAsset).filter(CachedAsset.status == "valid").count()
    cached_invalid = db.query(CachedAsset).filter(CachedAsset.status == "invalid").count()
    cached_missing = db.query(CachedAsset).filter(CachedAsset.status == "missing").count()

    media_total = db.query(MediaAsset).count()
    media_valid = db.query(MediaAsset).filter(MediaAsset.status == "valid").count()
    media_invalid = db.query(MediaAsset).filter(MediaAsset.status == "invalid").count()
    media_missing = db.query(MediaAsset).filter(MediaAsset.status == "missing").count()

    return ArtworkHealthResponse(
        cached_total=cached_total,
        cached_valid=cached_valid,
        cached_invalid=cached_invalid,
        cached_missing=cached_missing,
        media_total=media_total,
        media_valid=media_valid,
        media_invalid=media_invalid,
        media_missing=media_missing,
    )


@router.post("/validate/{video_id}")
def validate_video_artwork(video_id: int, db: Session = Depends(get_db)) -> VideoValidateResponse:
    """
    Validate all artwork for a single video.

    Checks each MediaAsset's file on disk. Marks invalid ones, deletes
    corrupt files. Use POST /api/artwork/repair to attempt refetch.
    """
    video = db.query(VideoItem).get(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    assets = db.query(MediaAsset).filter(MediaAsset.video_id == video_id).all()
    details = []
    valid_count = 0
    invalid_count = 0

    for asset in assets:
        is_valid = validate_existing_media_asset(asset, db)
        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1
        details.append({
            "asset_id": asset.id,
            "asset_type": asset.asset_type,
            "status": asset.status,
            "error": asset.validation_error,
        })

    # Also validate entity-level cached assets if linked
    if video.artist_entity_id:
        cached = db.query(CachedAsset).filter(
            CachedAsset.entity_type == "artist",
            CachedAsset.entity_id == video.artist_entity_id,
        ).all()
        for ca in cached:
            is_valid = validate_existing_cached_asset(ca, db)
            if is_valid:
                valid_count += 1
            else:
                invalid_count += 1
            details.append({
                "asset_id": ca.id,
                "asset_type": f"cached_{ca.entity_type}_{ca.kind}",
                "status": ca.status,
                "error": ca.validation_error,
            })

    if video.album_entity_id:
        cached = db.query(CachedAsset).filter(
            CachedAsset.entity_type == "album",
            CachedAsset.entity_id == video.album_entity_id,
        ).all()
        for ca in cached:
            is_valid = validate_existing_cached_asset(ca, db)
            if is_valid:
                valid_count += 1
            else:
                invalid_count += 1
            details.append({
                "asset_id": ca.id,
                "asset_type": f"cached_{ca.entity_type}_{ca.kind}",
                "status": ca.status,
                "error": ca.validation_error,
            })

    db.commit()

    return VideoValidateResponse(
        video_id=video_id,
        assets_checked=len(details),
        valid=valid_count,
        invalid=invalid_count,
        details=details,
    )


@router.post("/repair")
def repair_all_artwork(
    body: RepairRequest = RepairRequest(),
    db: Session = Depends(get_db),
) -> dict:
    """
    Repair all artwork in the library.

    Scans both cached entity assets and video-level media assets.
    Validates files, marks/deletes invalid ones.
    If refetch=True, attempts to re-download from original source URLs.
    """
    cached_report = repair_cached_assets(db, refetch=body.refetch)
    media_report = repair_media_assets(db)
    db.commit()

    return {
        "cached": RepairResponse(
            total_scanned=cached_report.total_scanned,
            valid=cached_report.valid,
            invalid=cached_report.invalid,
            missing=cached_report.missing,
            deleted=cached_report.deleted,
            refetched=cached_report.refetched,
            errors=cached_report.errors,
        ).model_dump(),
        "media": RepairResponse(
            total_scanned=media_report.total_scanned,
            valid=media_report.valid,
            invalid=media_report.invalid,
            missing=media_report.missing,
            deleted=media_report.deleted,
            refetched=media_report.refetched,
            errors=media_report.errors,
        ).model_dump(),
    }


@router.post("/repair/cached")
def repair_cached_only(
    body: RepairRequest = RepairRequest(),
    db: Session = Depends(get_db),
) -> RepairResponse:
    """Repair cached entity assets only."""
    report = repair_cached_assets(db, refetch=body.refetch)
    db.commit()
    return RepairResponse(
        total_scanned=report.total_scanned,
        valid=report.valid,
        invalid=report.invalid,
        missing=report.missing,
        deleted=report.deleted,
        refetched=report.refetched,
        errors=report.errors,
    )


@router.post("/repair/media")
def repair_media_only(db: Session = Depends(get_db)) -> RepairResponse:
    """Repair video-level media assets only."""
    report = repair_media_assets(db)
    db.commit()
    return RepairResponse(
        total_scanned=report.total_scanned,
        valid=report.valid,
        invalid=report.invalid,
        missing=report.missing,
        deleted=report.deleted,
        refetched=report.refetched,
        errors=report.errors,
    )
