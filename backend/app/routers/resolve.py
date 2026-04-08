"""
API router — Matching & Confidence Scoring
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
POST /api/resolve/{video_id}    — trigger resolve for a single video
GET  /api/resolve/{video_id}    — return last resolve result + candidates
POST /api/resolve/{video_id}/pin — pin a specific candidate selection
POST /api/resolve/{video_id}/unpin — unpin
POST /api/resolve/{video_id}/apply — apply without pin
POST /api/resolve/batch          — batch resolve filtered videos
POST /api/resolve/{video_id}/undo — revert the last forced resolve
GET  /api/resolve/{video_id}/normalization — return normalization detail
GET  /api/review                 — review queue (paginated, filterable by category)
POST /api/review/{video_id}/approve  — approve review item
POST /api/review/{video_id}/dismiss  — dismiss/clear review item
POST /api/review/{video_id}/set-version — change version type
POST /api/review/batch/approve   — batch approve
POST /api/review/batch/dismiss   — batch dismiss
GET  /api/search/artist          — manual artist search
GET  /api/search/recording       — manual recording search
GET  /api/search/release         — manual release search
POST /api/export/kodi            — export trigger
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.config import get_settings
from app.matching.models import (
    MatchResult, MatchCandidate, NormalizationResult as NormalizationResultModel,
    MatchStatusEnum, UserPinnedMatch,
)
from app.matching.resolver import resolve_video, ResolveOutput
from app.matching.scoring import MatchStatus
from app.matching.schemas import (
    ResolveResultOut, CandidateOut, NormalizationResultOut,
    PinRequest, BatchResolveRequest, BatchResolveOut, UndoResultOut,
    ReviewItemOut, ReviewListOut, ApplyRequest, DuplicateVideoSummary,
    ManualSearchResultOut, ManualSearchResponse,
    ExportKodiRequest, ExportKodiResponse,
)
from app.models import VideoItem, ProcessingJob, JobStatus

logger = logging.getLogger(__name__)


def _record_review_history(vi: VideoItem, action: str) -> None:
    """Append a review history entry to the video item."""
    entry = {
        "action": action,
        "category": vi.review_category,
        "reason": vi.review_reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    history = vi.review_history or []
    history.append(entry)
    vi.review_history = history


def _persist_duplicate_dismissal(vi: VideoItem, db) -> None:
    """Parse partner IDs from review_reason and add reciprocal
    dismissed_duplicate_ids so the pair/group won't be re-flagged."""
    id_match = re.search(r'ID\(s\):\s*([\d,\s]+)', vi.review_reason or "")
    if not id_match:
        return
    partner_ids = [int(x.strip()) for x in id_match.group(1).split(',') if x.strip().isdigit()]
    existing_partners = [
        pid for pid in partner_ids
        if db.query(VideoItem.id).filter(VideoItem.id == pid).first()
    ]
    dismissed = vi.dismissed_duplicate_ids or []
    for pid in existing_partners:
        if pid not in dismissed:
            dismissed.append(pid)
    vi.dismissed_duplicate_ids = dismissed
    # Reciprocal: add vi.id to each partner's dismissed list
    for pid in existing_partners:
        partner = db.query(VideoItem).filter(VideoItem.id == pid).first()
        if partner:
            p_dismissed = partner.dismissed_duplicate_ids or []
            if vi.id not in p_dismissed:
                p_dismissed.append(vi.id)
                partner.dismissed_duplicate_ids = p_dismissed

resolve_router = APIRouter(prefix="/api/resolve", tags=["resolve"])
review_router = APIRouter(prefix="/api/review", tags=["review"])
search_router = APIRouter(prefix="/api/search", tags=["search"])
export_router = APIRouter(prefix="/api/export", tags=["export"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────────

def _mr_to_response(mr: MatchResult, db: Session) -> ResolveResultOut:
    """Convert a MatchResult row + its candidates into a response model."""
    cands: List[CandidateOut] = []
    for mc in db.query(MatchCandidate).filter(
        MatchCandidate.match_result_id == mr.id
    ).order_by(MatchCandidate.rank).all():
        cands.append(CandidateOut(
            entity_type=mc.entity_type,
            mbid=mc.candidate_mbid,
            canonical_name=mc.canonical_name,
            provider=mc.provider,
            score=mc.score,
            breakdown=mc.score_breakdown,
            is_selected=mc.is_selected,
        ))

    return ResolveResultOut(
        video_id=mr.video_id,
        resolved_artist=mr.resolved_artist or "",
        artist_mbid=mr.artist_mbid,
        resolved_recording=mr.resolved_recording or "",
        recording_mbid=mr.recording_mbid,
        resolved_release=mr.resolved_release,
        release_mbid=mr.release_mbid,
        confidence_overall=mr.confidence_overall or 0.0,
        confidence_breakdown=mr.confidence_breakdown,
        status=mr.status.value if mr.status else "unmatched",
        candidate_list=cands,
        normalization_notes=mr.normalization_notes,
        changed=False,
        is_user_pinned=mr.is_user_pinned or False,
    )


def _output_to_response(out: ResolveOutput) -> ResolveResultOut:
    """Convert ResolveOutput dataclass to a response model."""
    cands = [
        CandidateOut(
            entity_type=c.get("entity_type", ""),
            mbid=c.get("mbid"),
            canonical_name=c.get("canonical_name", ""),
            provider=c.get("provider", ""),
            score=c.get("score", 0.0),
            breakdown=c.get("breakdown"),
            is_selected=False,
        )
        for c in (out.candidate_list or [])
    ]
    return ResolveResultOut(
        video_id=out.video_id,
        resolved_artist=out.resolved_artist,
        artist_mbid=out.artist_mbid,
        resolved_recording=out.resolved_recording,
        recording_mbid=out.recording_mbid,
        resolved_release=out.resolved_release,
        release_mbid=out.release_mbid,
        confidence_overall=out.confidence_overall,
        confidence_breakdown=out.confidence_breakdown,
        status=out.status.value if isinstance(out.status, MatchStatus) else str(out.status),
        candidate_list=cands,
        normalization_notes=out.normalization_notes,
        changed=out.changed,
        is_user_pinned=False,
    )


# ── Routes ────────────────────────────────────────────────────────────────

@resolve_router.post("/{video_id}", response_model=ResolveResultOut)
def trigger_resolve(
    video_id: int,
    force: bool = Query(False, description="Ignore hysteresis"),
    db: Session = Depends(_get_db),
):
    """Trigger matching resolution for a single video."""
    video = db.query(VideoItem).get(video_id)
    if not video:
        raise HTTPException(404, "Video not found")

    try:
        result = resolve_video(db, video_id, force=force)
        db.commit()
        return _output_to_response(result)
    except Exception as e:
        db.rollback()
        logger.error(f"Resolve failed for video {video_id}: {e}", exc_info=True)
        raise HTTPException(500, f"Resolve failed: {e}")


@resolve_router.get("/{video_id}", response_model=ResolveResultOut)
def get_resolve_result(video_id: int, db: Session = Depends(_get_db)):
    """Return the last resolve result and all candidates for a video."""
    mr = db.query(MatchResult).filter(MatchResult.video_id == video_id).first()
    if not mr:
        raise HTTPException(404, "No resolve result for this video")
    return _mr_to_response(mr, db)


@resolve_router.get("/{video_id}/normalization", response_model=NormalizationResultOut)
def get_normalization(video_id: int, db: Session = Depends(_get_db)):
    """Return the normalization breakdown for a video."""
    nr = db.query(NormalizationResultModel).filter(
        NormalizationResultModel.video_id == video_id
    ).first()
    if not nr:
        raise HTTPException(404, "No normalization result for this video")
    return NormalizationResultOut(
        raw_artist=nr.raw_artist or "",
        raw_title=nr.raw_title or "",
        raw_album=nr.raw_album,
        artist_display=nr.artist_display or "",
        artist_key=nr.artist_key or "",
        primary_artist=nr.primary_artist or "",
        featured_artists=nr.featured_artists,
        title_display=nr.title_display or "",
        title_key=nr.title_key or "",
        title_base=nr.title_base or "",
        qualifiers=nr.qualifiers,
        album_display=nr.album_display,
        album_key=nr.album_key,
        normalization_notes=nr.normalization_notes,
    )


@resolve_router.post("/{video_id}/pin")
def pin_match(video_id: int, body: PinRequest, db: Session = Depends(_get_db)):
    """Pin a specific candidate as the selected match (Fix Match)."""
    # Validate candidate exists
    candidate = db.query(MatchCandidate).get(body.candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    mr = db.query(MatchResult).get(candidate.match_result_id)
    if not mr or mr.video_id != video_id:
        raise HTTPException(400, "Candidate does not belong to this video")

    # Update selected flags
    db.query(MatchCandidate).filter(
        MatchCandidate.match_result_id == mr.id
    ).update({"is_selected": False})
    candidate.is_selected = True

    # Update match result with pinned values
    mr.is_user_pinned = True
    if candidate.entity_type == "recording":
        mr.resolved_recording = candidate.canonical_name
        mr.recording_mbid = candidate.candidate_mbid
        mr.status = MatchStatusEnum.matched_high
    elif candidate.entity_type == "artist":
        mr.resolved_artist = candidate.canonical_name
        mr.artist_mbid = candidate.candidate_mbid
    elif candidate.entity_type == "release":
        mr.resolved_release = candidate.canonical_name
        mr.release_mbid = candidate.candidate_mbid

    # Upsert pin record
    pin = db.query(UserPinnedMatch).filter(
        UserPinnedMatch.video_id == video_id
    ).first()
    if not pin:
        pin = UserPinnedMatch(video_id=video_id)
        db.add(pin)

    pin.candidate_id = candidate.id
    pin.artist_mbid = mr.artist_mbid
    pin.recording_mbid = mr.recording_mbid
    pin.release_mbid = mr.release_mbid
    pin.pinned_at = datetime.now(timezone.utc)

    db.commit()

    return {"status": "pinned", "video_id": video_id, "candidate_id": candidate.id}


@resolve_router.post("/{video_id}/unpin")
def unpin_match(video_id: int, db: Session = Depends(_get_db)):
    """Remove user pin (reverts to automatic scoring)."""
    pin = db.query(UserPinnedMatch).filter(
        UserPinnedMatch.video_id == video_id
    ).first()
    if pin:
        db.delete(pin)

    mr = db.query(MatchResult).filter(MatchResult.video_id == video_id).first()
    if mr:
        mr.is_user_pinned = False

    db.commit()
    return {"status": "unpinned", "video_id": video_id}


@resolve_router.post("/batch", response_model=BatchResolveOut)
def batch_resolve(body: BatchResolveRequest, db: Session = Depends(_get_db)):
    """
    Batch resolve multiple videos.

    Runs synchronously for small batches, dispatches a job for large ones.
    Filters: 'missing' (no MatchResult), 'low_confidence' (<70),
             'needs_review' (50-69), 'all'.
    """
    # Determine video IDs from filter or explicit list
    if body.video_ids:
        video_ids = body.video_ids
    else:
        filt = body.filter or "missing"
        q = db.query(VideoItem.id)
        if filt == "missing":
            resolved_ids = db.query(MatchResult.video_id).subquery()
            q = q.filter(~VideoItem.id.in_(resolved_ids))
        elif filt == "low_confidence":
            low_ids = db.query(MatchResult.video_id).filter(
                MatchResult.confidence_overall < 70
            ).subquery()
            q = q.filter(VideoItem.id.in_(low_ids))
        elif filt == "needs_review":
            review_ids = db.query(MatchResult.video_id).filter(
                MatchResult.status == MatchStatusEnum.needs_review
            ).subquery()
            q = q.filter(VideoItem.id.in_(review_ids))
        # 'all' → no filter
        video_ids = [row[0] for row in q.all()]

    if not video_ids:
        return BatchResolveOut(job_id=0, message="No videos to resolve", video_count=0)

    # For small batches, run inline
    MAX_INLINE = 10
    if len(video_ids) <= MAX_INLINE:
        from app.matching.resolver import resolve_batch
        results = resolve_batch(db, video_ids, force=body.force)
        db.commit()
        changed = sum(1 for r in results if r.changed)
        return BatchResolveOut(
            job_id=0,
            message=f"Resolved {len(results)} videos ({changed} updated)",
            video_count=len(results),
        )

    # For larger batches, dispatch a background job
    from app.worker import celery_app as _app
    job = ProcessingJob(
        job_type="batch_resolve",
        status=JobStatus.queued,
        display_name=f"Batch resolve ({len(video_ids)} videos)",
        action_label="Batch Resolve",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    _app.send_task(
        "app.tasks.batch_resolve_task",
        args=[job.id, video_ids, body.force],
    )
    return BatchResolveOut(
        job_id=job.id,
        message=f"Queued batch resolve for {len(video_ids)} videos",
        video_count=len(video_ids),
    )


@resolve_router.post("/{video_id}/undo", response_model=UndoResultOut)
def undo_resolve(video_id: int, db: Session = Depends(_get_db)):
    """Revert the last resolve to its previous snapshot (one-level undo)."""
    mr = db.query(MatchResult).filter(MatchResult.video_id == video_id).first()
    if not mr:
        raise HTTPException(404, "No match result to undo")

    snap = mr.previous_snapshot
    if not snap:
        raise HTTPException(400, "No previous snapshot available for undo")

    prev_artist = snap.get("resolved_artist")
    prev_recording = snap.get("resolved_recording")

    mr.resolved_artist = prev_artist
    mr.artist_mbid = snap.get("artist_mbid")
    mr.resolved_recording = prev_recording
    mr.recording_mbid = snap.get("recording_mbid")
    mr.resolved_release = snap.get("resolved_release")
    mr.release_mbid = snap.get("release_mbid")
    mr.confidence_overall = snap.get("confidence_overall", 0.0)
    mr.status = MatchStatusEnum(snap["status"]) if snap.get("status") else MatchStatusEnum.unmatched
    mr.previous_snapshot = None  # only one-level undo

    db.commit()

    return UndoResultOut(
        video_id=video_id,
        previous_artist=prev_artist,
        previous_recording=prev_recording,
        message="Reverted to previous match",
    )


# ── Apply without pin ────────────────────────────────────────────────────

@resolve_router.post("/{video_id}/apply")
def apply_match(video_id: int, body: ApplyRequest, db: Session = Depends(_get_db)):
    """Apply a candidate selection WITHOUT pinning (future auto-resolve may override)."""
    candidate = db.query(MatchCandidate).get(body.candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    mr = db.query(MatchResult).get(candidate.match_result_id)
    if not mr or mr.video_id != video_id:
        raise HTTPException(400, "Candidate does not belong to this video")

    # Save snapshot for undo
    mr.previous_snapshot = {
        "resolved_artist": mr.resolved_artist,
        "artist_mbid": mr.artist_mbid,
        "resolved_recording": mr.resolved_recording,
        "recording_mbid": mr.recording_mbid,
        "resolved_release": mr.resolved_release,
        "release_mbid": mr.release_mbid,
        "confidence_overall": mr.confidence_overall,
        "status": mr.status.value if mr.status else "unmatched",
    }

    # Update selected flags
    db.query(MatchCandidate).filter(
        MatchCandidate.match_result_id == mr.id
    ).update({"is_selected": False})
    candidate.is_selected = True

    # Apply candidate values (NOT pinned)
    if candidate.entity_type == "recording":
        mr.resolved_recording = candidate.canonical_name
        mr.recording_mbid = candidate.candidate_mbid
    elif candidate.entity_type == "artist":
        mr.resolved_artist = candidate.canonical_name
        mr.artist_mbid = candidate.candidate_mbid
    elif candidate.entity_type == "release":
        mr.resolved_release = candidate.canonical_name
        mr.release_mbid = candidate.candidate_mbid

    # Update confidence to match the candidate score
    mr.confidence_overall = candidate.score
    if candidate.score >= 80:
        mr.status = MatchStatusEnum.matched_high
    elif candidate.score >= 50:
        mr.status = MatchStatusEnum.matched_medium
    else:
        mr.status = MatchStatusEnum.needs_review

    db.commit()
    return {"status": "applied", "video_id": video_id, "candidate_id": candidate.id}


# ── Review queue ──────────────────────────────────────────────────────────

def _infer_review_category(vi: VideoItem) -> str:
    """Infer a review category from review_reason if review_category is not set."""
    reason = (vi.review_reason or "").lower()
    if "normalization failed" in reason or "audio normalization" in reason:
        return "normalization"
    if "naming convention" in reason or "rename" in reason:
        return "rename"
    if "duplicate import skipped" in reason:
        return "import_error"
    if "duplicate" in reason:
        return "duplicate"
    if "untracked file" in reason or "imported via scan" in reason:
        return "scanned"
    # Canonical track issues
    if "canonical" in reason and "missing" in reason:
        return "canonical_missing"
    if "canonical" in reason and "conflict" in reason:
        return "canonical_conflict"
    if "canonical" in reason and ("low confidence" in reason or "uncertain" in reason):
        return "canonical_low_confidence"
    if any(kw in reason for kw in ("cover", "live", "alternate", "version", "classification", "ambiguous")):
        return "version_detection"
    method = vi.import_method or ""
    if method == "url":
        return "url_import_error"
    if method in ("import", "scanned"):
        return "import_error"
    return "version_detection"


def _build_review_item(vi: VideoItem, db: Session) -> ReviewItemOut:
    """Build a ReviewItemOut from a VideoItem row, enriching with match data."""
    import os
    import re
    from app.models import QualitySignature

    mr = db.query(MatchResult).filter(MatchResult.video_id == vi.id).first()
    top_cand_out = None
    cand_count = 0
    if mr:
        top_cand = db.query(MatchCandidate).filter(
            MatchCandidate.match_result_id == mr.id
        ).order_by(MatchCandidate.rank).first()
        if top_cand:
            top_cand_out = CandidateOut(
                entity_type=top_cand.entity_type,
                mbid=top_cand.candidate_mbid,
                canonical_name=top_cand.canonical_name,
                provider=top_cand.provider,
                score=top_cand.score,
                breakdown=top_cand.score_breakdown,
                is_selected=top_cand.is_selected,
            )
        cand_count = db.query(MatchCandidate).filter(
            MatchCandidate.match_result_id == mr.id
        ).count()

    _fname = os.path.basename(vi.file_path) if vi.file_path else None
    cat = vi.review_category or _infer_review_category(vi)

    # Quality data
    qs = db.query(QualitySignature).filter(QualitySignature.video_id == vi.id).first()

    # Duplicate comparison: extract existing video id from review_reason
    dup_summary = None
    if cat == "duplicate" and vi.review_reason:
        id_match = re.search(r'ID\(s\):\s*([\d,\s]+)', vi.review_reason)
        if id_match:
            existing_id = int(id_match.group(1).split(',')[0].strip())
            # Skip self-reference: hard-duplicate flags are placed on the
            # existing video itself, so the id in the reason points at itself.
            if existing_id != vi.id:
                existing_vi = db.query(VideoItem).filter(VideoItem.id == existing_id).first()
                if existing_vi:
                    ex_qs = db.query(QualitySignature).filter(
                        QualitySignature.video_id == existing_id
                    ).first()
                    dup_summary = DuplicateVideoSummary(
                        video_id=existing_vi.id,
                        artist=existing_vi.artist or "",
                        title=existing_vi.title or "",
                        version_type=existing_vi.version_type or "normal",
                        thumbnail_url=f"/api/playback/poster/{existing_vi.id}",
                        resolution_label=existing_vi.resolution_label,
                        file_size_bytes=existing_vi.file_size_bytes,
                        duration_seconds=ex_qs.duration_seconds if ex_qs else None,
                        video_codec=ex_qs.video_codec if ex_qs else None,
                        audio_codec=ex_qs.audio_codec if ex_qs else None,
                        video_bitrate=ex_qs.video_bitrate if ex_qs else None,
                        audio_bitrate=ex_qs.audio_bitrate if ex_qs else None,
                        fps=ex_qs.fps if ex_qs else None,
                        hdr=ex_qs.hdr if ex_qs else False,
                        container=ex_qs.container if ex_qs else None,
                        import_method=existing_vi.import_method,
                        quality_score=ex_qs.quality_score() if ex_qs else 0,
                    )

    # Duplicate group key: normalized artist||title for visual grouping
    _dup_group_key = None
    if cat == "duplicate" and vi.artist and vi.title:
        from app.tasks import _normalize_for_dup, _normalize_title_for_dup
        _dup_group_key = f"{_normalize_for_dup(vi.artist)}||{_normalize_title_for_dup(vi.title)}"

    # Rename info: compute expected path for rename-category items
    _expected_path = None
    if cat == "rename" and vi.file_path and vi.folder_path:
        from app.services.file_organizer import compute_expected_paths
        from app.config import get_settings
        settings = get_settings()
        file_ext = os.path.splitext(vi.file_path)[1] or ".mkv"
        exp = compute_expected_paths(
            settings.library_dir,
            vi.artist or "", vi.title or "", vi.resolution_label or "1080p",
            album=vi.album or "",
            version_type=vi.version_type or "normal",
            alternate_version_label=vi.alternate_version_label or "",
            file_ext=file_ext,
        )
        expected_fname = f"{exp['file_base_name']}{file_ext}"
        _expected_path = f"{exp['subpath']}/{expected_fname}"

    return ReviewItemOut(
        video_id=vi.id,
        artist=vi.artist or "",
        title=vi.title or "",
        filename=_fname,
        thumbnail_url=f"/api/playback/poster/{vi.id}" if vi.id else None,
        review_status=vi.review_status or "none",
        review_category=cat,
        resolved_artist=mr.resolved_artist or "" if mr else "",
        resolved_recording=mr.resolved_recording or "" if mr else "",
        confidence_overall=mr.confidence_overall or 0.0 if mr else 0.0,
        status=mr.status.value if mr and mr.status else "unmatched",
        is_user_pinned=mr.is_user_pinned or False if mr else False,
        top_candidate=top_cand_out,
        candidate_count=cand_count,
        version_type=vi.version_type or "normal",
        review_reason=vi.review_reason,
        updated_at=vi.updated_at,
        resolution_label=vi.resolution_label,
        file_size_bytes=vi.file_size_bytes,
        import_method=vi.import_method,
        related_versions=vi.related_versions if vi.related_versions else None,
        duration_seconds=qs.duration_seconds if qs else None,
        video_codec=qs.video_codec if qs else None,
        audio_codec=qs.audio_codec if qs else None,
        video_bitrate=qs.video_bitrate if qs else None,
        audio_bitrate=qs.audio_bitrate if qs else None,
        fps=qs.fps if qs else None,
        hdr=qs.hdr if qs else False,
        container=qs.container if qs else None,
        quality_score=qs.quality_score() if qs else 0,
        duplicate_of=dup_summary,
        dup_group_key=_dup_group_key,
        expected_path=_expected_path,
    )


@review_router.get("", response_model=ReviewListOut)
def list_review_queue(
    status: Optional[str] = Query(None, description="Filter by review status: needs_human_review, needs_ai_review, reviewed"),
    category: Optional[str] = Query(None, description="Filter by review category: version_detection, duplicate, import_error, url_import_error, manual_review"),
    q: Optional[str] = Query(None, description="Search query"),
    sort: str = Query("updated_desc", description="Sort: updated_desc|title_asc|status_asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(_get_db),
):
    """Return a paginated list of videos that need review.

    Queries VideoItem.review_status — this covers both pipeline failures
    (scrape errors, AI timeout, entity failures) and version-detection
    ambiguity.  Optionally LEFT JOINs MatchResult for match enrichment.
    """
    from sqlalchemy import func, or_, case

    base_q = db.query(VideoItem).outerjoin(
        MatchResult, MatchResult.video_id == VideoItem.id
    )

    # By default, show all items that need review (exclude "none" and "reviewed")
    if status:
        base_q = base_q.filter(VideoItem.review_status == status)
    else:
        base_q = base_q.filter(VideoItem.review_status.notin_(["none", "reviewed"]))

    # Text search
    if q:
        search_term = f"%{q}%"
        base_q = base_q.filter(or_(
            VideoItem.artist.ilike(search_term),
            VideoItem.title.ilike(search_term),
            VideoItem.album.ilike(search_term),
        ))

    # Category counts (before category filter is applied)
    # Use a case expression to infer category for items that haven't been back-filled
    cat_expr = case(
        (VideoItem.review_category.isnot(None), VideoItem.review_category),
        (VideoItem.review_reason.like("%normalization failed%"), "normalization"),
        (VideoItem.review_reason.like("%Audio normalization%"), "normalization"),
        (VideoItem.review_reason.like("%naming convention%"), "rename"),
        (VideoItem.review_reason.like("%rename%"), "rename"),
        (VideoItem.review_reason.like("%Duplicate import skipped%"), "import_error"),
        (VideoItem.review_reason.like("%duplicate%"), "duplicate"),
        (VideoItem.review_reason.like("%untracked file%"), "scanned"),
        (VideoItem.review_reason.like("%imported via scan%"), "scanned"),
        (VideoItem.review_reason.like("%cover%"), "version_detection"),
        (VideoItem.review_reason.like("%live%"), "version_detection"),
        (VideoItem.review_reason.like("%alternate%"), "version_detection"),
        (VideoItem.review_reason.like("%version%"), "version_detection"),
        (VideoItem.review_reason.like("%classification%"), "version_detection"),
        (VideoItem.review_reason.like("%ambiguous%"), "version_detection"),
        (VideoItem.import_method == "url", "url_import_error"),
        (VideoItem.import_method.in_(["import", "scanned"]), "import_error"),
        else_="version_detection",
    )

    cat_counts_raw = base_q.with_entities(cat_expr.label("cat"), func.count()).group_by("cat").all()
    category_counts = {row[0]: row[1] for row in cat_counts_raw}

    # Category filter
    if category:
        # Filter on stored category OR inferred category
        base_q = base_q.filter(cat_expr == category)

    # Total before pagination
    total = base_q.count()

    # Sorting
    if sort == "title_asc":
        base_q = base_q.order_by(VideoItem.title.asc())
    elif sort == "status_asc":
        base_q = base_q.order_by(VideoItem.review_status.asc(), VideoItem.title.asc())
    else:  # updated_desc (default)
        base_q = base_q.order_by(VideoItem.updated_at.desc())

    # Pagination
    offset = (page - 1) * page_size
    video_items = base_q.offset(offset).limit(page_size).all()

    items = [_build_review_item(vi, db) for vi in video_items]

    return ReviewListOut(
        items=items, total=total, page=page, page_size=page_size,
        category_counts=category_counts,
    )


# ── Batch & non-parameterized routes (must come before {video_id} routes) ──

@review_router.post("/batch/approve")
def batch_approve(
    video_ids: List[int],
    db: Session = Depends(_get_db),
):
    """Approve multiple review items at once."""
    items = db.query(VideoItem).filter(VideoItem.id.in_(video_ids)).all()
    for vi in items:
        # For duplicates: persist dismissed_duplicate_ids so pairs aren't re-flagged
        if vi.review_category == "duplicate" and vi.review_reason:
            _persist_duplicate_dismissal(vi, db)
        _record_review_history(vi, "approved")
        vi.review_status = "reviewed"
        vi.review_reason = None
        vi.review_category = None
    db.commit()
    # Persist to XML sidecars
    try:
        from app.services.playarr_xml import write_playarr_xml
        for vi in items:
            write_playarr_xml(vi, db)
    except Exception:
        pass
    return {"status": "approved", "count": len(items)}


@review_router.post("/batch/dismiss")
def batch_dismiss(
    video_ids: List[int],
    db: Session = Depends(_get_db),
):
    """Dismiss multiple review items at once."""
    items = db.query(VideoItem).filter(VideoItem.id.in_(video_ids)).all()
    for vi in items:
        # For duplicates: persist dismissed_duplicate_ids so pairs aren't re-flagged
        if vi.review_category == "duplicate" and vi.review_reason:
            _persist_duplicate_dismissal(vi, db)
        # For renames: persist rename_dismissed so the item isn't re-flagged
        if vi.review_category == "rename":
            vi.rename_dismissed = True
        _record_review_history(vi, "dismissed")
        vi.review_status = "none"
        vi.review_reason = None
        vi.review_category = None
    db.commit()
    # Persist to XML sidecars
    try:
        from app.services.playarr_xml import write_playarr_xml
        for vi in items:
            write_playarr_xml(vi, db)
    except Exception:
        pass
    return {"status": "dismissed", "count": len(items)}


@review_router.post("/batch/apply-rename")
def batch_apply_rename(
    video_ids: List[int],
    db: Session = Depends(_get_db),
):
    """Apply naming convention rename for multiple videos."""
    from app.routers.library import rename_to_expected

    renamed = 0
    errors = []
    for vid in video_ids:
        try:
            rename_to_expected(vid, db)
            vi = db.query(VideoItem).filter(VideoItem.id == vid).first()
            if vi:
                _record_review_history(vi, "approved")
                vi.review_status = "reviewed"
                vi.review_reason = None
                vi.review_category = None
            renamed += 1
        except Exception as e:
            errors.append(f"Video {vid}: {str(e)}")
    db.commit()
    return {"status": "renamed", "renamed": renamed, "failed": len(errors), "errors": errors}


@review_router.post("/batch/delete")
def batch_delete_from_review(
    video_ids: List[int],
    db: Session = Depends(_get_db),
):
    """Delete multiple videos (and their files) from the review queue.

    Delegates to the library batch-delete logic so entity cleanup,
    cached-asset removal, and disk cleanup all happen correctly.
    """
    from app.routers.library import batch_delete_videos, BatchDeleteRequest

    req = BatchDeleteRequest(video_ids=video_ids)
    return batch_delete_videos(req, db)


class BatchScrapeRequest(BaseModel):
    video_ids: List[int]
    scrape_wikipedia: bool = True
    scrape_musicbrainz: bool = True
    ai_auto: bool = False
    ai_only: bool = False
    scene_analysis: bool = True
    normalize: bool = False


@review_router.post("/batch/scrape")
def batch_scrape_from_review(
    req: BatchScrapeRequest,
    db: Session = Depends(_get_db),
):
    """Queue a metadata scrape (rescan) for multiple review items.

    Creates one sub-job per video and a parent batch job to track them.
    """
    from app.models import ProcessingJob, JobStatus
    from app.tasks import rescan_metadata_task, complete_batch_job_task
    from app.worker import dispatch_task
    from datetime import datetime, timezone

    items = db.query(VideoItem).filter(VideoItem.id.in_(req.video_ids)).all()
    if not items:
        raise HTTPException(404, "No matching videos found")

    ids = [v.id for v in items]

    job = ProcessingJob(
        job_type="batch_rescan",
        status=JobStatus.queued,
        display_name=f"Batch Scrape ({len(ids)} videos)",
        action_label="Batch Scrape (Review)",
        input_params={"video_ids": ids, "count": len(ids)},
    )
    db.add(job)
    db.commit()

    # Pre-fetch display names for scrape children
    _scrape_names = {v.id: f"{v.artist} \u2013 {v.title}" for v in items if v.artist and v.title}
    sub_job_ids = []
    for vid in ids:
        _sn = _scrape_names.get(vid)
        sub_job = ProcessingJob(
            job_type="rescan", status=JobStatus.queued,
            video_id=vid, action_label="Scrape Metadata",
            display_name=f"{_sn} \u203a Scrape Metadata" if _sn else None,
        )
        db.add(sub_job)
        db.flush()
        sub_job_ids.append(sub_job.id)
        dispatch_task(rescan_metadata_task, job_id=sub_job.id, video_id=vid,
                      scrape_wikipedia=req.scrape_wikipedia,
                      scrape_musicbrainz=req.scrape_musicbrainz,
                      ai_auto=req.ai_auto, ai_only=req.ai_only,
                      scene_analysis=req.scene_analysis,
                      normalize=req.normalize)

    job.status = JobStatus.analyzing
    job.current_step = f"Scraping {len(ids)} videos"
    job.started_at = datetime.now(timezone.utc)
    job.input_params = {**(job.input_params or {}), "sub_job_ids": sub_job_ids}
    db.commit()

    dispatch_task(complete_batch_job_task, parent_job_id=job.id, sub_job_ids=sub_job_ids)
    return {"job_id": job.id, "message": f"Queued metadata scrape for {len(ids)} item(s)"}


@review_router.post("/scan-enrichment")
def scan_enrichment(
    rescan_all: bool = Query(False, description="Re-scan ALL videos including previously dismissed items"),
    db: Session = Depends(_get_db),
):
    """Scan library for videos with incomplete AI enrichment and flag them for review."""
    query = db.query(VideoItem).filter(VideoItem.file_path.isnot(None))
    if not rescan_all:
        # Only flag items that haven't been explicitly reviewed/approved.
        # "reviewed" means a human already approved — don't re-flag.
        query = query.filter(VideoItem.review_status == "none")

    videos = query.all()
    flagged = 0
    for v in videos:
        ps = v.processing_state or {}
        _done = lambda step: ps.get(step, {}).get("completed", False)
        ai_done = _done("ai_enriched")
        scenes_done = _done("scenes_analyzed")
        if ai_done and scenes_done:
            continue  # fully enriched — skip
        category = "ai_partial" if (ai_done or scenes_done) else "ai_pending"
        missing = []
        if not ai_done:
            missing.append("AI metadata")
        if not scenes_done:
            missing.append("scene analysis")
        v.review_status = "needs_human_review"
        v.review_category = category
        v.review_reason = f"Missing {', '.join(missing)}"
        flagged += 1

    db.commit()
    return {"status": "scanned", "flagged": flagged}


@review_router.post("/scan-renames")
def scan_renames(
    rescan_all: bool = Query(False, description="Re-scan ALL files including previously dismissed items"),
    db: Session = Depends(_get_db),
):
    """Scan library for videos whose file/folder names don't match
    the current naming convention and flag them for review."""
    import os
    from app.services.file_organizer import compute_expected_paths
    from app.config import get_settings

    settings = get_settings()

    # If rescan_all, clear all rename_dismissed flags first
    if rescan_all:
        cleared = db.query(VideoItem).filter(VideoItem.rename_dismissed == True).update(
            {VideoItem.rename_dismissed: False}, synchronize_session="fetch"
        )
        if cleared:
            db.commit()

    query = db.query(VideoItem).filter(
        VideoItem.folder_path.isnot(None),
        VideoItem.file_path.isnot(None),
        VideoItem.review_status.in_(["none", "reviewed"]),
    )
    if not rescan_all:
        query = query.filter(VideoItem.rename_dismissed == False)
    videos = query.all()

    flagged = 0
    for v in videos:
        if not v.folder_path or not v.file_path:
            continue
        file_ext = os.path.splitext(v.file_path)[1] or ".mkv"
        expected = compute_expected_paths(
            settings.library_dir,
            v.artist or "", v.title or "", v.resolution_label or "1080p",
            album=v.album or "",
            version_type=v.version_type or "normal",
            alternate_version_label=v.alternate_version_label or "",
            file_ext=file_ext,
        )
        current_folder_norm = os.path.normpath(v.folder_path).lower()
        expected_folder_norm = os.path.normpath(expected["folder_path"]).lower()
        rename_needed = current_folder_norm != expected_folder_norm

        current_fname = os.path.basename(v.file_path)
        expected_fname = f"{expected['file_base_name']}{file_ext}"
        if current_fname.lower() != expected_fname.lower():
            rename_needed = True

        if rename_needed:
            current_rel = os.path.relpath(v.folder_path, settings.library_dir) if v.folder_path.startswith(settings.library_dir) else os.path.basename(v.folder_path)
            expected_rel = expected["subpath"]
            v.review_status = "needs_human_review"
            v.review_category = "rename"
            v.review_reason = f"Naming convention mismatch: {current_rel}/{current_fname} → {expected_rel}/{expected_fname}"
            flagged += 1

    db.commit()
    return {"status": "scanned", "flagged": flagged}


# ── Parameterized routes ({video_id}) ──

@review_router.post("/{video_id}/approve")
def approve_review_item(
    video_id: int,
    db: Session = Depends(_get_db),
):
    """Approve a review item — accepts the current category/version and removes it from review."""
    vi = db.query(VideoItem).filter(VideoItem.id == video_id).first()
    if not vi:
        raise HTTPException(404, "Video not found")

    # For duplicates: persist dismissed_duplicate_ids so the pair is never re-flagged
    if vi.review_category == "duplicate" and vi.review_reason:
        _persist_duplicate_dismissal(vi, db)

    _record_review_history(vi, "approved")
    vi.review_status = "reviewed"
    vi.review_reason = None
    vi.review_category = None
    db.commit()

    # Persist to XML sidecar
    try:
        from app.services.playarr_xml import write_playarr_xml
        write_playarr_xml(vi, db)
    except Exception:
        pass

    return {"status": "approved", "video_id": video_id}


@review_router.post("/{video_id}/dismiss")
def dismiss_review_item(
    video_id: int,
    db: Session = Depends(_get_db),
):
    """Dismiss/clear a review item — clears the review flag and reason."""
    vi = db.query(VideoItem).filter(VideoItem.id == video_id).first()
    if not vi:
        raise HTTPException(404, "Video not found")

    # For duplicates: persist dismissed_duplicate_ids so the pair isn't re-flagged
    if vi.review_category == "duplicate" and vi.review_reason:
        _persist_duplicate_dismissal(vi, db)

    # For renames: persist rename_dismissed so the item isn't re-flagged
    if vi.review_category == "rename":
        vi.rename_dismissed = True

    _record_review_history(vi, "dismissed")
    vi.review_status = "none"
    vi.review_reason = None
    vi.review_category = None
    db.commit()

    # Update XML sidecar to persist the dismissed_duplicate_ids
    try:
        from app.services.playarr_xml import write_playarr_xml
        write_playarr_xml(vi, db)
    except Exception:
        pass

    return {"status": "dismissed", "video_id": video_id}


@review_router.post("/{video_id}/set-version")
def set_review_version_type(
    video_id: int,
    version_type: str = Query(..., description="New version type: normal, cover, live, alternate, uncensored"),
    approve: bool = Query(True, description="Also approve/remove from review"),
    db: Session = Depends(_get_db),
):
    """Change the version type of a review item and optionally approve it."""
    valid_types = {"normal", "cover", "live", "alternate", "uncensored", "18+"}
    if version_type not in valid_types:
        raise HTTPException(400, f"Invalid version_type. Must be one of: {valid_types}")
    vi = db.query(VideoItem).filter(VideoItem.id == video_id).first()
    if not vi:
        raise HTTPException(404, "Video not found")
    vi.version_type = version_type
    if approve:
        vi.review_status = "reviewed"
    db.commit()
    return {"status": "updated", "video_id": video_id, "version_type": version_type}


@review_router.post("/{video_id}/apply-rename")
def apply_rename(video_id: int, db: Session = Depends(_get_db)):
    """Apply the naming convention rename for a single video and clear its review flag."""
    from app.routers.library import rename_to_expected

    vi = db.query(VideoItem).filter(VideoItem.id == video_id).first()
    if not vi:
        raise HTTPException(404, "Video not found")

    # Delegate to the existing library rename endpoint logic
    rename_to_expected(video_id, db)

    # Clear review flag
    vi = db.query(VideoItem).filter(VideoItem.id == video_id).first()
    if vi:
        vi.review_status = "reviewed"
        vi.review_reason = None
        vi.review_category = None
        db.commit()

    return {"status": "renamed", "video_id": video_id}


# ── Manual search (MusicBrainz) ──────────────────────────────────────────

@search_router.get("/artist", response_model=ManualSearchResponse)
def search_artist(
    q: str = Query(..., min_length=1, description="Artist name query"),
    limit: int = Query(10, ge=1, le=25),
    db: Session = Depends(_get_db),
):
    """Search MusicBrainz for artists."""
    import musicbrainzngs as mb
    try:
        mb.set_useragent("Playarr", "0.1", "https://github.com/playarr")
        result = mb.search_artists(query=q, limit=limit)
        artists = result.get("artist-list", [])
        results = [
            ManualSearchResultOut(
                mbid=a["id"],
                name=a.get("name", ""),
                disambiguation=a.get("disambiguation"),
                score=int(a.get("ext:score", 0)),
                extra={
                    "type": a.get("type"),
                    "country": a.get("country"),
                    "sort_name": a.get("sort-name"),
                },
            )
            for a in artists
        ]
        return ManualSearchResponse(query=q, entity_type="artist", results=results)
    except Exception as e:
        logger.error(f"MusicBrainz artist search failed: {e}")
        raise HTTPException(502, f"MusicBrainz search failed: {e}")


@search_router.get("/recording", response_model=ManualSearchResponse)
def search_recording(
    q: str = Query(..., min_length=1, description="Recording/track name query"),
    artist: Optional[str] = Query(None, description="Artist name filter"),
    limit: int = Query(10, ge=1, le=25),
    db: Session = Depends(_get_db),
):
    """Search MusicBrainz for recordings."""
    import musicbrainzngs as mb
    try:
        mb.set_useragent("Playarr", "0.1", "https://github.com/playarr")
        search_q = q
        if artist:
            search_q = f'"{q}" AND artist:"{artist}"'
        result = mb.search_recordings(query=search_q, limit=limit)
        recordings = result.get("recording-list", [])
        results = [
            ManualSearchResultOut(
                mbid=r["id"],
                name=r.get("title", ""),
                disambiguation=r.get("disambiguation"),
                score=int(r.get("ext:score", 0)),
                extra={
                    "artist": r.get("artist-credit-phrase"),
                    "length": r.get("length"),
                    "releases": [
                        {"title": rel.get("title"), "mbid": rel.get("id")}
                        for rel in r.get("release-list", [])[:3]
                    ] if r.get("release-list") else [],
                },
            )
            for r in recordings
        ]
        return ManualSearchResponse(query=q, entity_type="recording", results=results)
    except Exception as e:
        logger.error(f"MusicBrainz recording search failed: {e}")
        raise HTTPException(502, f"MusicBrainz search failed: {e}")


@search_router.get("/release", response_model=ManualSearchResponse)
def search_release(
    q: str = Query(..., min_length=1, description="Release/album name query"),
    artist: Optional[str] = Query(None, description="Artist name filter"),
    limit: int = Query(10, ge=1, le=25),
    db: Session = Depends(_get_db),
):
    """Search MusicBrainz for releases."""
    import musicbrainzngs as mb
    try:
        mb.set_useragent("Playarr", "0.1", "https://github.com/playarr")
        search_q = q
        if artist:
            search_q = f'"{q}" AND artist:"{artist}"'
        result = mb.search_releases(query=search_q, limit=limit)
        releases = result.get("release-list", [])
        results = [
            ManualSearchResultOut(
                mbid=r["id"],
                name=r.get("title", ""),
                disambiguation=r.get("disambiguation"),
                score=int(r.get("ext:score", 0)),
                extra={
                    "artist": r.get("artist-credit-phrase"),
                    "date": r.get("date"),
                    "country": r.get("country"),
                    "release_group_type": r.get("release-group", {}).get("type"),
                },
            )
            for r in releases
        ]
        return ManualSearchResponse(query=q, entity_type="release", results=results)
    except Exception as e:
        logger.error(f"MusicBrainz release search failed: {e}")
        raise HTTPException(502, f"MusicBrainz search failed: {e}")


# ── Kodi export ───────────────────────────────────────────────────────────

@export_router.post("/kodi", response_model=ExportKodiResponse)
def export_kodi(body: ExportKodiRequest, db: Session = Depends(_get_db)):
    """Export matched metadata to Kodi-compatible NFO files."""
    import xml.etree.ElementTree as ET
    from pathlib import Path

    settings = get_settings()

    # Determine which videos to export
    if body.video_ids:
        videos = db.query(VideoItem).filter(VideoItem.id.in_(body.video_ids)).all()
    else:
        # All videos with high/medium matches
        matched_ids = db.query(MatchResult.video_id).filter(
            MatchResult.status.in_([MatchStatusEnum.matched_high, MatchStatusEnum.matched_medium])
        ).subquery()
        videos = db.query(VideoItem).filter(VideoItem.id.in_(matched_ids)).all()

    exported = 0
    skipped = 0
    errors = 0

    for video in videos:
        try:
            mr = db.query(MatchResult).filter(MatchResult.video_id == video.id).first()
            if not mr:
                skipped += 1
                continue

            # Build NFO path
            video_dir = Path(video.file_path).parent if video.file_path else None
            if not video_dir or not video_dir.exists():
                skipped += 1
                continue

            nfo_path = video_dir / f"{Path(video.file_path).stem}.nfo"

            if nfo_path.exists() and not body.overwrite_existing:
                skipped += 1
                continue

            # Build Kodi-compatible musicvideo NFO
            root = ET.Element("musicvideo")
            ET.SubElement(root, "title").text = mr.resolved_recording or video.title or ""
            ET.SubElement(root, "artist").text = mr.resolved_artist or ""
            if mr.resolved_release:
                ET.SubElement(root, "album").text = mr.resolved_release
            if mr.artist_mbid:
                ET.SubElement(root, "musicbrainzartistid").text = mr.artist_mbid
            if mr.recording_mbid:
                ET.SubElement(root, "musicbrainztrackid").text = mr.recording_mbid
            if mr.release_mbid:
                ET.SubElement(root, "musicbrainzreleaseid").text = mr.release_mbid

            # Write NFO
            tree = ET.ElementTree(root)
            ET.indent(tree, space="  ")
            tree.write(str(nfo_path), encoding="unicode", xml_declaration=True)
            exported += 1

        except Exception as e:
            logger.error(f"NFO export failed for video {video.id}: {e}")
            errors += 1

    return ExportKodiResponse(
        exported=exported,
        skipped=skipped,
        errors=errors,
        message=f"Exported {exported} NFO files ({skipped} skipped, {errors} errors)",
    )
