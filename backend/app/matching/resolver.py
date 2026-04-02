"""
Resolver Orchestration — The main entry point for matching.

Workflow for a single video
---------------------------
1. **Normalise** raw artist / title / album strings.
2. **Fetch candidates** from MusicBrainz (+ optionally Wikipedia).
3. **Score** each candidate with the feature-based scoring engine.
4. **Rank** candidates deterministically (score → MBID → name).
5. **Apply hysteresis** — only update if Δ ≥ threshold or first resolve.
6. **Persist** MatchResult, MatchCandidate, NormalizationResult.
7. **Return** ResolveOutput with full breakdown.

Public API
----------
    resolve_video(db, video_id, *, force=False) -> ResolveOutput
    resolve_batch(db, video_ids, *, force=False) -> list[ResolveOutput]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.matching.normalization import (
    normalize_artist_name, extract_featured_artists,
    normalize_title, extract_title_qualifiers,
    normalize_album, make_comparison_key,
)
from app.matching.candidates import (
    ArtistCandidate, RecordingCandidate, ReleaseCandidate,
    fetch_artist_candidates, fetch_recording_candidates,
    fetch_release_candidates,
)
from app.matching.scoring import (
    MatchStatus, ScoreBreakdown, ScoredCandidate,
    score_artist_candidate, score_recording_candidate,
    score_release_candidate, compute_overall_score,
    classify_score,
)
from app.matching.hysteresis import should_update_match, is_pinned

logger = logging.getLogger(__name__)

__all__ = ["resolve_video", "resolve_batch", "ResolveOutput"]


# ── Output container ──────────────────────────────────────────────────────

@dataclass
class ResolveOutput:
    """Result returned by ``resolve_video``."""
    video_id: int
    resolved_artist: str = ""
    artist_mbid: Optional[str] = None
    resolved_recording: str = ""
    recording_mbid: Optional[str] = None
    resolved_release: Optional[str] = None
    release_mbid: Optional[str] = None
    confidence_overall: float = 0.0
    confidence_breakdown: Optional[Dict[str, Any]] = None
    status: MatchStatus = MatchStatus.UNMATCHED
    candidate_list: List[Dict[str, Any]] = field(default_factory=list)
    normalization_notes: Optional[Dict[str, Any]] = None
    changed: bool = False  # True if match was updated

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "resolved_artist": self.resolved_artist,
            "artist_mbid": self.artist_mbid,
            "resolved_recording": self.resolved_recording,
            "recording_mbid": self.recording_mbid,
            "resolved_release": self.resolved_release,
            "release_mbid": self.release_mbid,
            "confidence_overall": self.confidence_overall,
            "confidence_breakdown": self.confidence_breakdown,
            "status": self.status.value,
            "candidate_list": self.candidate_list,
            "normalization_notes": self.normalization_notes,
            "changed": self.changed,
        }


# ── Internal helpers ──────────────────────────────────────────────────────

def _build_normalization_notes(
    raw_artist: str, raw_title: str, raw_album: Optional[str],
    artist_display: str, title_display: str, album_display: Optional[str],
    primary: str, featured: List[str], qualifiers: Set[str],
) -> Dict[str, Any]:
    """Build a human-readable normalization change log."""
    notes: Dict[str, Any] = {}
    if raw_artist != artist_display:
        notes["artist_cleaned"] = {"from": raw_artist, "to": artist_display}
    if raw_title != title_display:
        notes["title_cleaned"] = {"from": raw_title, "to": title_display}
    if featured:
        notes["featured_artists_extracted"] = featured
    if qualifiers:
        notes["qualifiers_detected"] = sorted(qualifiers)
    if raw_album and album_display and raw_album != album_display:
        notes["album_cleaned"] = {"from": raw_album, "to": album_display}
    return notes


def _score_and_rank_artists(
    artist_candidates: List[ArtistCandidate],
    query_artist_key: str,
    query_artist_display: str,
) -> List[ScoredCandidate]:
    """Score and sort artist candidates."""
    scored: List[ScoredCandidate] = []
    for c in artist_candidates:
        features = score_artist_candidate(
            c,
            query_artist_key=query_artist_key,
            query_artist_display=query_artist_display,
        )
        # Simple category score for ranking artists individually
        from app.matching.scoring import _weighted_category_score, _ARTIST_FEATURE_WEIGHTS
        cat_score = _weighted_category_score(features, _ARTIST_FEATURE_WEIGHTS) * 100
        scored.append(ScoredCandidate(
            entity_type="artist",
            candidate_id=c.mbid,
            canonical_name=c.canonical_name,
            provider=c.provider,
            score=cat_score,
            breakdown=ScoreBreakdown(
                features=features,
                category_scores={"artist": round(cat_score / 100, 4)},
                overall_score=cat_score,
            ),
            raw=c.raw,
        ))
    scored.sort(key=lambda x: x.sort_key())
    return scored


def _score_and_rank_recordings(
    recording_candidates: List[RecordingCandidate],
    query_title_key: str,
    query_title_display: str,
    query_qualifiers: Set[str],
    local_duration: Optional[float],
    resolved_artist_mbid: Optional[str],
    resolved_artist_key: str,
    # Artist features from top artist candidate (for overall score)
    top_artist_features: Dict[str, float],
) -> List[ScoredCandidate]:
    """Score and sort recording candidates with full overall breakdown."""
    scored: List[ScoredCandidate] = []
    for c in recording_candidates:
        rec_features = score_recording_candidate(
            c,
            query_title_key=query_title_key,
            query_title_display=query_title_display,
            query_qualifiers=query_qualifiers,
            local_duration=local_duration,
            resolved_artist_mbid=resolved_artist_mbid,
            resolved_artist_key=resolved_artist_key,
        )
        # Compute full overall score (artist + recording, no release yet)
        breakdown = compute_overall_score(
            artist_features=top_artist_features,
            recording_features=rec_features,
            has_album_data=False,
        )
        scored.append(ScoredCandidate(
            entity_type="recording",
            candidate_id=c.mbid,
            canonical_name=c.title,
            provider=c.provider,
            score=breakdown.overall_score,
            breakdown=breakdown,
            raw=c.raw,
        ))
    scored.sort(key=lambda x: x.sort_key())
    return scored


def _score_and_rank_releases(
    release_candidates: List[ReleaseCandidate],
    query_album_key: str,
    query_album_display: str,
    query_year: Optional[int],
    resolved_artist_mbid: Optional[str],
    resolved_artist_key: str,
) -> List[ScoredCandidate]:
    """Score and sort release candidates."""
    scored: List[ScoredCandidate] = []
    for c in release_candidates:
        features = score_release_candidate(
            c,
            query_album_key=query_album_key,
            query_album_display=query_album_display,
            query_year=query_year,
            resolved_artist_mbid=resolved_artist_mbid,
            resolved_artist_key=resolved_artist_key,
        )
        from app.matching.scoring import _weighted_category_score, _RELEASE_FEATURE_WEIGHTS
        cat_score = _weighted_category_score(features, _RELEASE_FEATURE_WEIGHTS) * 100
        scored.append(ScoredCandidate(
            entity_type="release",
            candidate_id=c.mbid,
            canonical_name=c.title,
            provider=c.provider,
            score=cat_score,
            breakdown=ScoreBreakdown(
                features=features,
                category_scores={"release": round(cat_score / 100, 4)},
                overall_score=cat_score,
            ),
            raw=c.raw,
        ))
    scored.sort(key=lambda x: x.sort_key())
    return scored


# ── Main resolve function ─────────────────────────────────────────────────

def resolve_video(
    db: Session,
    video_id: int,
    *,
    force: bool = False,
) -> ResolveOutput:
    """
    Full resolve pipeline for a single video.

    Parameters
    ----------
    db : Session
        SQLAlchemy session.
    video_id : int
        VideoItem.id to resolve.
    force : bool
        If True, ignore hysteresis and re-resolve regardless.

    Returns
    -------
    ResolveOutput
        Full result with breakdown and candidate list.
    """
    from app.models import VideoItem
    from app.matching.models import (
        MatchResult, MatchCandidate, NormalizationResult,
        MatchStatusEnum, UserPinnedMatch,
    )

    video = db.query(VideoItem).get(video_id)
    if not video:
        return ResolveOutput(video_id=video_id, status=MatchStatus.UNMATCHED)

    # ── 0. Check pin ────────────────────────────────────────────────
    pinned = db.query(UserPinnedMatch).filter(
        UserPinnedMatch.video_id == video_id
    ).first()
    if pinned and not force:
        # Return existing result without changes
        existing = db.query(MatchResult).filter(
            MatchResult.video_id == video_id
        ).first()
        if existing:
            return _match_result_to_output(existing, changed=False)

    # ── 1. Normalise ────────────────────────────────────────────────
    raw_artist = video.artist or ""
    raw_title = video.title or ""
    raw_album = video.album
    raw_year = video.year

    artist_display = normalize_artist_name(raw_artist)
    primary_artist, featured = extract_featured_artists(raw_artist)
    artist_key = make_comparison_key(primary_artist)

    title_info = extract_title_qualifiers(raw_title)
    title_display = title_info["title_base"]
    title_key = make_comparison_key(title_display)
    qualifiers: Set[str] = title_info["qualifiers"]

    album_display = normalize_album(raw_album) if raw_album else None
    album_key = make_comparison_key(album_display) if album_display else None

    norm_notes = _build_normalization_notes(
        raw_artist, raw_title, raw_album,
        artist_display, title_display, album_display,
        primary_artist, featured, qualifiers,
    )

    # Get duration from quality signature
    local_duration: Optional[float] = None
    if video.quality_signature:
        local_duration = video.quality_signature.duration_seconds

    # ── 2. Fetch candidates ─────────────────────────────────────────
    artist_candidates = fetch_artist_candidates(
        primary_artist,
        mb_artist_id=video.mb_artist_id,
        limit=5,
    )

    # ── 3. Score artists ────────────────────────────────────────────
    scored_artists = _score_and_rank_artists(
        artist_candidates, artist_key, artist_display,
    )

    # Pick top artist for recording scoring
    top_artist = scored_artists[0] if scored_artists else None
    top_artist_mbid = top_artist.candidate_id if top_artist else None
    top_artist_name = top_artist.canonical_name if top_artist else primary_artist
    top_artist_features = top_artist.breakdown.features if top_artist else {}

    # ── 4. Fetch & score recordings ─────────────────────────────────
    recording_candidates = fetch_recording_candidates(
        top_artist_name, title_display,
        mb_recording_id=video.mb_recording_id,
        limit=8,
    )

    scored_recordings = _score_and_rank_recordings(
        recording_candidates,
        title_key, title_display, qualifiers, local_duration,
        top_artist_mbid, artist_key,
        top_artist_features,
    )

    # ── 5. Fetch & score releases (if album data exists) ────────────
    scored_releases: List[ScoredCandidate] = []
    has_album = bool(album_display and album_key)
    if has_album:
        release_candidates = fetch_release_candidates(
            top_artist_name, album_display or "",
            mb_release_id=video.mb_release_id,
            limit=5,
        )
        scored_releases = _score_and_rank_releases(
            release_candidates,
            album_key or "", album_display or "", raw_year,
            top_artist_mbid, artist_key,
        )

    # ── 6. Compute final overall score ──────────────────────────────
    top_recording = scored_recordings[0] if scored_recordings else None
    top_release = scored_releases[0] if scored_releases else None

    # Recompute with release data included
    rec_features = top_recording.breakdown.features if top_recording else {}
    rel_features = top_release.breakdown.features if top_release else None

    # Cross-source agreement: check if MusicBrainz and Wikipedia agree
    cross_agree = 0.0
    if top_recording and has_album:
        # If recording candidate reports same album as our album input, bonus
        if top_recording.raw and top_recording.raw.get("album"):
            mb_album_key = make_comparison_key(top_recording.raw["album"])
            if album_key and mb_album_key == album_key:
                cross_agree = 1.0
            elif album_key:
                from app.matching.scoring import string_similarity
                cross_agree = string_similarity(album_display or "", top_recording.raw["album"])

    final_breakdown = compute_overall_score(
        artist_features=top_artist_features,
        recording_features=rec_features,
        release_features=rel_features,
        cross_source_agreement=cross_agree,
        has_album_data=has_album and rel_features is not None,
    )

    # ── 7. Apply hysteresis ─────────────────────────────────────────
    existing_match = db.query(MatchResult).filter(
        MatchResult.video_id == video_id
    ).first()

    old_score = existing_match.confidence_overall if existing_match else 0.0
    old_rec_mbid = existing_match.recording_mbid if existing_match else None

    # Check if old MBID is still present in new candidates
    new_rec_mbids = {sc.candidate_id for sc in scored_recordings if sc.candidate_id}
    old_still_present = old_rec_mbid in new_rec_mbids if old_rec_mbid else True

    update = force or should_update_match(
        old_score=old_score,
        new_score=final_breakdown.overall_score,
        old_mbid=old_rec_mbid,
        new_mbid=top_recording.candidate_id if top_recording else None,
        old_mbid_still_present=old_still_present,
        is_user_pinned=bool(pinned),
    )

    if not update and existing_match:
        return _match_result_to_output(existing_match, changed=False)

    # ── 8. Persist ──────────────────────────────────────────────────
    # Save previous state as snapshot
    previous_snapshot = None
    if existing_match:
        previous_snapshot = {
            "resolved_artist": existing_match.resolved_artist,
            "artist_mbid": existing_match.artist_mbid,
            "resolved_recording": existing_match.resolved_recording,
            "recording_mbid": existing_match.recording_mbid,
            "resolved_release": existing_match.resolved_release,
            "release_mbid": existing_match.release_mbid,
            "confidence_overall": existing_match.confidence_overall,
            "status": existing_match.status.value if existing_match.status else None,
        }
        # Delete old candidates
        db.query(MatchCandidate).filter(
            MatchCandidate.match_result_id == existing_match.id
        ).delete()
        match_result = existing_match
    else:
        match_result = MatchResult(video_id=video_id)
        db.add(match_result)
        db.flush()

    # Update match result
    match_result.resolved_artist = top_artist_name
    match_result.artist_mbid = top_artist_mbid
    match_result.resolved_recording = top_recording.canonical_name if top_recording else title_display
    match_result.recording_mbid = top_recording.candidate_id if top_recording else None
    match_result.resolved_release = top_release.canonical_name if top_release else album_display
    match_result.release_mbid = top_release.candidate_id if top_release else None
    match_result.confidence_overall = final_breakdown.overall_score
    match_result.confidence_breakdown = final_breakdown.to_dict()
    match_result.status = MatchStatusEnum(final_breakdown.status.value)
    match_result.previous_snapshot = previous_snapshot
    match_result.normalization_notes = norm_notes
    match_result.is_user_pinned = bool(pinned)

    # Save top-N candidates (artists, recordings, releases combined)
    rank = 0
    for scored_list in [scored_artists[:5], scored_recordings[:5], scored_releases[:3]]:
        for sc in scored_list:
            rank += 1
            db.add(MatchCandidate(
                match_result_id=match_result.id,
                entity_type=sc.entity_type,
                candidate_mbid=sc.candidate_id,
                canonical_name=sc.canonical_name,
                provider=sc.provider,
                score=sc.score,
                score_breakdown=sc.breakdown.to_dict(),
                rank=rank,
                is_selected=(
                    (sc.entity_type == "artist" and sc.candidate_id == top_artist_mbid)
                    or (sc.entity_type == "recording" and sc == top_recording)
                    or (sc.entity_type == "release" and sc == top_release)
                ),
            ))

    # Save normalization result
    existing_norm = db.query(NormalizationResult).filter(
        NormalizationResult.video_id == video_id
    ).first()
    if existing_norm:
        norm = existing_norm
    else:
        norm = NormalizationResult(video_id=video_id)
        db.add(norm)

    norm.raw_artist = raw_artist
    norm.raw_title = raw_title
    norm.raw_album = raw_album
    norm.artist_display = artist_display
    norm.artist_key = artist_key
    norm.primary_artist = primary_artist
    norm.featured_artists = featured
    norm.title_display = title_display
    norm.title_key = title_key
    norm.title_base = title_info["title_base"]
    norm.qualifiers = sorted(qualifiers)
    norm.album_display = album_display
    norm.album_key = album_key
    norm.normalization_notes = norm_notes

    db.flush()

    # ── 9. Build output ─────────────────────────────────────────────
    candidate_list: List[Dict[str, Any]] = []
    for sc in scored_recordings[:5]:
        candidate_list.append({
            "entity_type": sc.entity_type,
            "mbid": sc.candidate_id,
            "canonical_name": sc.canonical_name,
            "provider": sc.provider,
            "score": sc.score,
            "breakdown": sc.breakdown.to_dict(),
            "raw": sc.raw,
        })

    output = ResolveOutput(
        video_id=video_id,
        resolved_artist=top_artist_name,
        artist_mbid=top_artist_mbid,
        resolved_recording=top_recording.canonical_name if top_recording else title_display,
        recording_mbid=top_recording.candidate_id if top_recording else None,
        resolved_release=top_release.canonical_name if top_release else album_display,
        release_mbid=top_release.candidate_id if top_release else None,
        confidence_overall=final_breakdown.overall_score,
        confidence_breakdown=final_breakdown.to_dict(),
        status=final_breakdown.status,
        candidate_list=candidate_list,
        normalization_notes=norm_notes,
        changed=True,
    )

    logger.info(
        f"Resolved video {video_id}: {output.resolved_artist} - "
        f"{output.resolved_recording} → {output.status.value} "
        f"(score={output.confidence_overall:.1f})"
    )

    return output


def resolve_batch(
    db: Session,
    video_ids: List[int],
    *,
    force: bool = False,
) -> List[ResolveOutput]:
    """Resolve multiple videos sequentially."""
    results: List[ResolveOutput] = []
    for vid in video_ids:
        try:
            r = resolve_video(db, vid, force=force)
            results.append(r)
        except Exception as e:
            logger.error(f"Resolve failed for video {vid}: {e}")
            results.append(ResolveOutput(
                video_id=vid,
                status=MatchStatus.UNMATCHED,
            ))
    return results


# ── Helpers ───────────────────────────────────────────────────────────────

def _match_result_to_output(mr, *, changed: bool) -> ResolveOutput:
    """Convert a MatchResult DB row to a ResolveOutput."""
    from app.matching.models import MatchCandidate

    candidates = []
    for mc in mr.candidates:
        if mc.entity_type == "recording":
            candidates.append({
                "entity_type": mc.entity_type,
                "mbid": mc.candidate_mbid,
                "canonical_name": mc.canonical_name,
                "provider": mc.provider,
                "score": mc.score,
                "breakdown": mc.score_breakdown,
            })

    return ResolveOutput(
        video_id=mr.video_id,
        resolved_artist=mr.resolved_artist or "",
        artist_mbid=mr.artist_mbid,
        resolved_recording=mr.resolved_recording or "",
        recording_mbid=mr.recording_mbid,
        resolved_release=mr.resolved_release,
        release_mbid=mr.release_mbid,
        confidence_overall=mr.confidence_overall,
        confidence_breakdown=mr.confidence_breakdown,
        status=MatchStatus(mr.status.value) if mr.status else MatchStatus.UNMATCHED,
        candidate_list=candidates,
        normalization_notes=mr.normalization_notes,
        changed=changed,
    )
