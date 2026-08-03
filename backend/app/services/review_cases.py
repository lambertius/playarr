"""Generate durable, evidence-hashed review cases from transition-era flags."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from itertools import combinations
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy.orm import Session

from app.models import (
    QualitySignature,
    ReviewCase,
    ReviewCaseEdge,
    ReviewCaseItem,
    Source,
    SourceProvider,
    VideoItem,
)
from app.services.content_id import compute_ids_for_video


def _normalise(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


_VERSION_QUALIFIERS = re.compile(
    r"\b(?:alternate|live|acoustic|clean|explicit|remix|remaster(?:ed)?|"
    r"censored|uncensored|extended|radio|demo|instrumental|karaoke|version|"
    r"edit|official|lyrics?|video|audio|mix|dub|mono|stereo)\b",
    re.IGNORECASE,
)


def _normalise_artist(value: str | None) -> str:
    artist = re.sub(
        r"[\s\(\[]+(?:feat\.?|featuring|ft\.?)\s+.*$", "", value or "",
        flags=re.IGNORECASE,
    ).strip()
    return _normalise(artist or value)


def _normalise_title(value: str | None) -> str:
    """Match alternate/live/remix suffixes without erasing legitimate titles."""
    title = (value or "").strip()
    while title:
        match = re.search(r"\s*[\(\[]([^\(\)\[\]]*)[\)\]]\s*$", title)
        if match and _VERSION_QUALIFIERS.search(match.group(1)):
            title = title[:match.start()].strip()
            continue
        match = re.search(r"\s+[-–—]\s+([^-–—]+)$", title)
        if match and _VERSION_QUALIFIERS.search(match.group(1)):
            title = title[:match.start()].strip()
            continue
        break
    return _normalise(title)


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


def _item_evidence(
    video: VideoItem, quality: QualitySignature | None,
    missing_enrichment: list[str] | None = None,
) -> dict:
    _ensure_content_ids(video)
    from app.config import get_settings
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
        "loudness_lufs": quality.loudness_lufs if quality else None,
        "normalization_target_lufs": get_settings().normalization_target_lufs,
        "duration_seconds": quality.duration_seconds if quality else None,
        "file_size_bytes": video.file_size_bytes,
        "version_type": video.version_type or "normal",
        "source": video.import_method or "unknown",
        "audio_fingerprint": bool(video.audio_fingerprint),
        "video_phash": video.video_phash,
        "legacy_trigger_detail": video.review_reason,
        "missing_enrichment": missing_enrichment or [],
    }


def _edge_evidence(left: VideoItem, right: VideoItem) -> tuple[str, float, dict]:
    exact_title_match = (
        _normalise(left.artist) == _normalise(right.artist)
        and _normalise(left.title) == _normalise(right.title)
    )
    version_tolerant_title_match = (
        _normalise_artist(left.artist) == _normalise_artist(right.artist)
        and _normalise_title(left.title) == _normalise_title(right.title)
    )
    fingerprint_match = bool(
        left.audio_fingerprint
        and right.audio_fingerprint
        and left.audio_fingerprint == right.audio_fingerprint
    )
    phash_match = bool(
        left.video_phash and right.video_phash and left.video_phash == right.video_phash
    )
    recording_id_match = bool(
        (left.acoustid_id and left.acoustid_id == right.acoustid_id)
        or (
            getattr(left, "mb_recording_id", None)
            and left.mb_recording_id == getattr(right, "mb_recording_id", None)
        )
    )
    track_id_match = bool(
        left.playarr_track_id
        and left.playarr_track_id == right.playarr_track_id
    )
    video_id_match = bool(
        left.playarr_video_id
        and left.playarr_video_id == right.playarr_video_id
    )
    evidence = {
        "same_artist_title": exact_title_match,
        "similar_artist_title": version_tolerant_title_match,
        "same_audio_fingerprint": fingerprint_match,
        "same_perceptual_hash": phash_match,
        "same_recording_id": recording_id_match,
        "same_track_identity": track_id_match,
        "same_video_identity": video_id_match,
        "left_revision": left.revision,
        "right_revision": right.revision,
        "left_file_checksum": left.file_checksum,
        "right_file_checksum": right.file_checksum,
    }
    score = min(
        1.0,
        (0.35 if exact_title_match else 0.25 if version_tolerant_title_match else 0.0)
        + (0.35 if fingerprint_match or recording_id_match else 0.0)
        + (0.2 if phash_match or video_id_match else 0.0)
        + (0.1 if track_id_match else 0.0),
    )
    evidence_types = [
        label
        for matched, label in (
            (exact_title_match, "same_title"),
            (version_tolerant_title_match and not exact_title_match, "similar_title"),
            (fingerprint_match, "audio_fingerprint"),
            (recording_id_match, "recording_id"),
            (phash_match, "perceptual_hash"),
            (track_id_match, "track_identity"),
            (video_id_match, "video_identity"),
        )
        if matched
    ]
    return "+".join(evidence_types) or "legacy_duplicate_signal", score, evidence


def _case_spec(
    category: str, key: str, videos: list[VideoItem],
    quality_by_video: dict[int, QualitySignature],
    missing_by_video: dict[int, list[str]] | None = None,
):
    item_evidence = [
        _item_evidence(video, quality_by_video.get(video.id), (missing_by_video or {}).get(video.id))
        for video in videos
    ]
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
        "trigger_code": f"video_flag:{category}",
        "items": sorted(item_evidence, key=lambda item: item["video_stable_id"]),
        "edges": edges,
    }
    return {
        "stable_id": str(uuid5(NAMESPACE_URL, f"playarr:review:{category}:{key}")),
        "category": category,
        "trigger_code": f"video_flag:{category}",
        "videos": videos,
        "items": item_evidence,
        "edges": edges,
        "evidence": evidence,
        "evidence_hash": _canonical_hash(evidence),
    }


def sync_review_cases(db: Session, *, include_enrichment_completeness: bool = False) -> list[ReviewCase]:
    """Dual-read transition: materialize current flags as durable cases."""
    flagged = db.query(VideoItem).filter(
        VideoItem.review_status.notin_(("none", "reviewed")),
    ).all()
    all_videos = db.query(VideoItem).all()
    quality_by_video = {
        quality.video_id: quality for quality in db.query(QualitySignature).all()
    }
    from app.ai.models import AIThumbnail
    thumbnail_video_ids = {
        video_id for (video_id,) in db.query(AIThumbnail.video_id).distinct().all()
    }
    wikipedia_video_ids = {
        video_id for (video_id,) in db.query(Source.video_id)
        .filter(Source.provider == SourceProvider.wikipedia).distinct().all()
    }

    def missing_enrichment(video: VideoItem) -> list[str]:
        state = video.processing_state or {}
        completed = lambda key: bool((state.get(key) or {}).get("completed"))
        missing: list[str] = []
        if not completed("ai_enriched"):
            missing.append("no_ai")
        if video.id not in thumbnail_video_ids:
            missing.append("no_thumbnails")
        if not completed("scenes_analyzed"):
            missing.append("no_scene_analysis")
        if video.id not in wikipedia_video_ids:
            missing.append("no_wikipedia")
        if not any((
            video.mb_artist_id, video.mb_recording_id, video.mb_release_id,
            video.mb_release_group_id, video.mb_track_id,
        )):
            missing.append("no_mbid")
        return missing

    missing_by_video = {video.id: missing_enrichment(video) for video in all_videos}

    duplicate_groups: dict[str, list[VideoItem]] = {}
    individual: list[tuple[str, VideoItem]] = []
    for video in flagged:
        # Categories are explicit structured data. Uncategorised transition
        # rows are intentionally labelled legacy_manual_review; reason text is
        # evidence detail, never the classifier.
        category = video.review_category or (
            "version_detection" if (video.version_type or "normal") != "normal"
            else "legacy_manual_review"
        )
        if category == "duplicate":
            group_key = f"{_normalise_artist(video.artist)}||{_normalise_title(video.title)}"
            duplicate_groups.setdefault(group_key, [])
        else:
            individual.append((category, video))

    for group_key in duplicate_groups:
        artist_key, title_key = group_key.split("||", 1)
        duplicate_groups[group_key] = [
            video for video in all_videos
            if _normalise_artist(video.artist) == artist_key
            and _normalise_title(video.title) == title_key
        ]

    # A duplicate case is deliberately one A/B comparison. A cluster of three
    # videos therefore produces V1–V2, V1–V3 and V2–V3 as independently
    # resolvable cases rather than one ambiguous three-way decision.
    specs = []
    generated_pairs: set[tuple[str, str]] = set()
    def append_pair(left: VideoItem, right: VideoItem) -> None:
        if left.id == right.id:
            return
        pair = tuple(sorted((_stable_video_id(left), _stable_video_id(right))))
        if pair in generated_pairs:
            return
        generated_pairs.add(pair)
        specs.append(_case_spec("duplicate", ":".join(pair), [left, right], quality_by_video))

    for videos in duplicate_groups.values():
        ordered = sorted(videos, key=_stable_video_id)
        for left, right in combinations(ordered, 2):
            append_pair(left, right)

    # Recording/fingerprint scans may pair videos whose displayed titles are
    # genuinely different. Preserve their explicit partner IDs instead of
    # reconstructing every relationship from text labels.
    videos_by_id = {video.id: video for video in all_videos}
    for video in flagged:
        if video.review_category != "duplicate":
            continue
        for partner_id in re.findall(r"\d+", video.review_reason or ""):
            partner = videos_by_id.get(int(partner_id))
            if partner is not None:
                append_pair(video, partner)
    specs.extend(
        _case_spec(category, _stable_video_id(video), [video], quality_by_video)
        for category, video in individual
    )
    # Completeness is a first-class review rule, not a scan the user must
    # remember to run. Dismissal remains durable until the underlying evidence
    # changes, because the case hash includes the exact missing capabilities.
    if include_enrichment_completeness:
        specs.extend(
            _case_spec(
                "enrichment_incomplete", _stable_video_id(video), [video],
                quality_by_video, missing_by_video,
            )
            for video in all_videos if missing_by_video[video.id]
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
            if case.status == "obsolete":
                case.status = "open"
                case.resolved_at = None

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
        ReviewCase.trigger_code.like("video_flag:%"),
    )
    if not include_enrichment_completeness:
        stale_query = stale_query.filter(ReviewCase.category != "enrichment_incomplete")
    if generated_ids:
        stale_query = stale_query.filter(ReviewCase.stable_id.notin_(generated_ids))
    for stale in stale_query.all():
        stale.status = "obsolete"
        stale.resolved_at = now
    db.flush()
    return materialized


def sync_orphan_review_cases(db: Session, orphan_records: list[dict]) -> list[ReviewCase]:
    """Materialize filesystem-only media as review cases without inventing DB videos."""
    now = datetime.now(timezone.utc)
    active_ids: set[str] = set()
    materialized: list[ReviewCase] = []
    for record in orphan_records:
        path = str(record["folder_path"])
        stable_id = str(uuid5(NAMESPACE_URL, f"playarr:review:orphan:{_normalise(path)}"))
        active_ids.add(stable_id)
        evidence = {
            "schema": 1,
            "category": "orphan_file",
            "trigger_code": "filesystem_scan:unrepresented_media",
            "folder_path": path,
            "size_bytes": record.get("size_bytes"),
            "file_count": record.get("file_count"),
            "files": sorted(record.get("files") or []),
        }
        evidence_hash = _canonical_hash(evidence)
        case = db.query(ReviewCase).filter(ReviewCase.stable_id == stable_id).one_or_none()
        if case is None:
            case = ReviewCase(
                stable_id=stable_id, category="orphan_file",
                trigger_code="filesystem_scan:unrepresented_media",
                evidence_hash=evidence_hash, evidence_json=evidence,
            )
            db.add(case)
        elif case.evidence_hash != evidence_hash:
            case.revision += 1
            case.evidence_hash = evidence_hash
            case.evidence_json = evidence
            case.status = "open"
            case.resolved_at = None
        materialized.append(case)

    stale = db.query(ReviewCase).filter(
        ReviewCase.status == "open",
        ReviewCase.trigger_code == "filesystem_scan:unrepresented_media",
    )
    if active_ids:
        stale = stale.filter(ReviewCase.stable_id.notin_(active_ids))
    for case in stale.all():
        case.status = "obsolete"
        case.resolved_at = now
    db.flush()
    return materialized


def dismiss_case(case: ReviewCase) -> None:
    case.status = "dismissed"
    case.dismissed_evidence_hash = case.evidence_hash
    case.resolved_at = datetime.now(timezone.utc)
    for edge in case.edges:
        edge.status = "dismissed"


def clear_resolved_duplicate_flags(db: Session, stable_ids: set[str]) -> None:
    """Clear legacy flags only when a video has no unresolved pair left."""
    if not stable_ids:
        return
    for stable_id in stable_ids:
        has_open_pair = (
            db.query(ReviewCaseItem.id)
            .join(ReviewCase, ReviewCase.id == ReviewCaseItem.case_id)
            .filter(
                ReviewCase.category == "duplicate",
                ReviewCase.status == "open",
                ReviewCaseItem.video_stable_id == stable_id,
            )
            .first()
            is not None
        )
        if has_open_pair:
            continue
        video = db.query(VideoItem).filter(VideoItem.stable_id == stable_id).one_or_none()
        if video and video.review_category == "duplicate":
            video.review_status = "none"
            video.review_category = None
            video.review_reason = None


def record_duplicate_pair_resolution(db: Session, case: ReviewCase) -> None:
    """Persist a mutual not-duplicate decision for the legacy scanner."""
    stable_ids = [item.video_stable_id for item in case.items]
    videos = {
        video.stable_id: video
        for video in db.query(VideoItem).filter(VideoItem.stable_id.in_(stable_ids)).all()
    }
    if len(videos) != 2:
        return
    left, right = videos.values()
    left_ids = set(left.dismissed_duplicate_ids or [])
    right_ids = set(right.dismissed_duplicate_ids or [])
    left_ids.add(right.id)
    right_ids.add(left.id)
    left.dismissed_duplicate_ids = sorted(left_ids)
    right.dismissed_duplicate_ids = sorted(right_ids)
