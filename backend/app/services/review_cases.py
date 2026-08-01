"""Generate durable, evidence-hashed review cases from transition-era flags."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from itertools import combinations
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy.orm import Session

from app.models import (
    QualitySignature,
    ReviewCase,
    ReviewCaseEdge,
    ReviewCaseItem,
    VideoItem,
)
from app.services.content_id import compute_ids_for_video


def _normalise(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _stable_video_id(video: VideoItem) -> str:
    if not video.stable_id:
        video.stable_id = str(uuid4())
    return video.stable_id


def _ensure_content_ids(video: VideoItem) -> None:
    if not video.playarr_video_id or not video.playarr_track_id:
        ids = compute_ids_for_video(video)
        if not video.playarr_video_id:
            video.playarr_video_id = ids["playarr_video_id"]
        if not video.playarr_track_id:
            video.playarr_track_id = ids["playarr_track_id"]


def _item_evidence(video: VideoItem, quality: QualitySignature | None) -> dict:
    _ensure_content_ids(video)
    return {
        "video_id": video.id,
        "video_stable_id": _stable_video_id(video),
        "playarr_video_id": video.playarr_video_id,
        "playarr_track_id": video.playarr_track_id,
        "artist": video.artist or "",
        "title": video.title or "",
        "added_at": video.created_at.isoformat() if video.created_at else None,
        "resolution": video.resolution_label,
        "video_codec": quality.video_codec if quality else None,
        "video_bitrate": quality.video_bitrate if quality else None,
        "audio_codec": quality.audio_codec if quality else None,
        "audio_bitrate": quality.audio_bitrate if quality else None,
        "duration_seconds": quality.duration_seconds if quality else None,
        "file_size_bytes": video.file_size_bytes,
        "version_type": video.version_type or "normal",
        "source": video.import_method or "unknown",
        "audio_fingerprint": bool(video.audio_fingerprint),
        "video_phash": video.video_phash,
        "legacy_trigger_detail": video.review_reason,
    }


def _edge_evidence(left: VideoItem, right: VideoItem) -> tuple[str, float, dict]:
    title_match = (
        _normalise(left.artist) == _normalise(right.artist)
        and _normalise(left.title) == _normalise(right.title)
    )
    fingerprint_match = bool(
        left.audio_fingerprint
        and right.audio_fingerprint
        and left.audio_fingerprint == right.audio_fingerprint
    )
    phash_match = bool(
        left.video_phash and right.video_phash and left.video_phash == right.video_phash
    )
    evidence = {
        "same_normalized_artist_title": title_match,
        "same_audio_fingerprint": fingerprint_match,
        "same_perceptual_hash": phash_match,
        "left_revision": left.revision,
        "right_revision": right.revision,
        "left_file_checksum": left.file_checksum,
        "right_file_checksum": right.file_checksum,
    }
    score = min(
        1.0,
        (0.4 if title_match else 0.0)
        + (0.4 if fingerprint_match else 0.0)
        + (0.2 if phash_match else 0.0),
    )
    evidence_types = [
        label
        for matched, label in (
            (title_match, "artist_title"),
            (fingerprint_match, "audio_fingerprint"),
            (phash_match, "perceptual_hash"),
        )
        if matched
    ]
    return "+".join(evidence_types) or "legacy_duplicate_signal", score, evidence


def _case_spec(category: str, key: str, videos: list[VideoItem], quality_by_video: dict[int, QualitySignature]):
    item_evidence = [_item_evidence(video, quality_by_video.get(video.id)) for video in videos]
    edges = []
    if category == "duplicate":
        for left, right in combinations(sorted(videos, key=lambda item: _stable_video_id(item)), 2):
            evidence_type, score, evidence = _edge_evidence(left, right)
            left_id, right_id = sorted((_stable_video_id(left), _stable_video_id(right)))
            edges.append({
                "left_video_stable_id": left_id,
                "right_video_stable_id": right_id,
                "evidence_type": evidence_type,
                "score": score,
                "evidence": evidence,
                "evidence_hash": _canonical_hash(evidence),
            })
    evidence = {
        "schema": 1,
        "category": category,
        "trigger_code": f"legacy_flag:{category}",
        "items": sorted(item_evidence, key=lambda item: item["video_stable_id"]),
        "edges": edges,
    }
    return {
        "stable_id": str(uuid5(NAMESPACE_URL, f"playarr:review:{category}:{key}")),
        "category": category,
        "trigger_code": f"legacy_flag:{category}",
        "videos": videos,
        "items": item_evidence,
        "edges": edges,
        "evidence": evidence,
        "evidence_hash": _canonical_hash(evidence),
    }


def sync_review_cases(db: Session) -> list[ReviewCase]:
    """Dual-read transition: materialize current flags as durable cases."""
    flagged = db.query(VideoItem).filter(
        VideoItem.review_status.notin_(("none", "reviewed")),
    ).all()
    all_videos = db.query(VideoItem).all()
    quality_by_video = {
        quality.video_id: quality for quality in db.query(QualitySignature).all()
    }

    duplicate_groups: dict[str, list[VideoItem]] = {}
    individual: list[tuple[str, VideoItem]] = []
    for video in flagged:
        # Categories are explicit structured data. Uncategorised transition
        # rows are intentionally labelled legacy_manual_review; reason text is
        # evidence detail, never the classifier.
        category = video.review_category or "legacy_manual_review"
        if category == "duplicate":
            group_key = f"{_normalise(video.artist)}||{_normalise(video.title)}"
            duplicate_groups.setdefault(group_key, [])
        else:
            individual.append((category, video))

    for group_key in duplicate_groups:
        artist_key, title_key = group_key.split("||", 1)
        duplicate_groups[group_key] = [
            video for video in all_videos
            if _normalise(video.artist) == artist_key and _normalise(video.title) == title_key
        ]

    specs = [
        _case_spec("duplicate", key, videos, quality_by_video)
        for key, videos in duplicate_groups.items()
        if videos
    ]
    specs.extend(
        _case_spec(category, _stable_video_id(video), [video], quality_by_video)
        for category, video in individual
    )

    materialized: list[ReviewCase] = []
    generated_ids: set[str] = set()
    now = datetime.now(timezone.utc)
    for spec in specs:
        generated_ids.add(spec["stable_id"])
        case = db.query(ReviewCase).filter(
            ReviewCase.stable_id == spec["stable_id"],
        ).one_or_none()
        if case is None:
            case = ReviewCase(
                stable_id=spec["stable_id"],
                category=spec["category"],
                trigger_code=spec["trigger_code"],
                evidence_hash=spec["evidence_hash"],
                evidence_json=spec["evidence"],
            )
            db.add(case)
            db.flush()
        elif case.evidence_hash != spec["evidence_hash"]:
            case.revision += 1
            case.evidence_hash = spec["evidence_hash"]
            case.evidence_json = spec["evidence"]
            case.status = "open"
            case.resolved_at = None
        else:
            # Unchanged dismissed evidence stays dismissed across rescans.
            case.evidence_json = spec["evidence"]

        case.items.clear()
        case.edges.clear()
        db.flush()
        for video, evidence in zip(spec["videos"], spec["items"]):
            case.items.append(ReviewCaseItem(
                video_id=video.id,
                video_stable_id=_stable_video_id(video),
                role="candidate",
                evidence_summary_json=evidence,
            ))
        for edge in spec["edges"]:
            case.edges.append(ReviewCaseEdge(
                left_video_stable_id=edge["left_video_stable_id"],
                right_video_stable_id=edge["right_video_stable_id"],
                evidence_type=edge["evidence_type"],
                score=edge["score"],
                evidence_hash=edge["evidence_hash"],
                evidence_json=edge["evidence"],
            ))
        materialized.append(case)

    stale_query = db.query(ReviewCase).filter(
        ReviewCase.status == "open",
        ReviewCase.trigger_code.like("legacy_flag:%"),
    )
    if generated_ids:
        stale_query = stale_query.filter(ReviewCase.stable_id.notin_(generated_ids))
    for stale in stale_query.all():
        stale.status = "obsolete"
        stale.resolved_at = now
    db.flush()
    return materialized


def dismiss_case(case: ReviewCase) -> None:
    case.status = "dismissed"
    case.dismissed_evidence_hash = case.evidence_hash
    case.resolved_at = datetime.now(timezone.utc)
    for edge in case.edges:
        edge.status = "dismissed"
