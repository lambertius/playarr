"""Append-only field provenance events shared by edits, pulls and pushes."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import FieldProvenanceEvent, VideoItem

MANUAL_FIELDS = (
    "artist", "title", "album", "year", "plot", "mb_artist_id",
    "mb_recording_id", "mb_release_id", "mb_release_group_id", "mb_track_id",
    "artist_ids", "playarr_video_id", "playarr_track_id",
)


def value_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record_field_event(
    db: Session,
    video: VideoItem,
    field_name: str,
    *,
    event_type: str,
    actor_kind: str,
    prior_value: Any = None,
    resulting_value: Any = None,
    actor_id: str | None = None,
    model_id: str | None = None,
    provider: str | None = None,
    source_url: str | None = None,
    remote_id: str | None = None,
    transformation: str | None = None,
    verification: dict | None = None,
    operation_id: str | None = None,
    retrieved_at: datetime | None = None,
    submitted_at: datetime | None = None,
) -> FieldProvenanceEvent:
    event = FieldProvenanceEvent(
        video_id=video.id,
        video_stable_id=video.stable_id,
        field_name=field_name,
        event_type=event_type,
        actor_kind=actor_kind,
        actor_id=actor_id,
        model_id=model_id,
        provider=provider,
        source_url=source_url,
        remote_id=remote_id,
        transformation=transformation,
        prior_value_hash=value_hash(prior_value),
        resulting_value_hash=value_hash(resulting_value),
        verification_json=verification,
        operation_id=operation_id,
        retrieved_at=retrieved_at,
        submitted_at=submitted_at,
    )
    db.add(event)
    return event


def capture_manual_values(video: VideoItem) -> dict[str, Any]:
    values = {field: getattr(video, field, None) for field in MANUAL_FIELDS}
    values["genres"] = sorted(genre.name for genre in video.genres)
    return values


def record_manual_changes(
    db: Session, video: VideoItem, fields: list[str], prior: dict[str, Any], user_id: str,
) -> None:
    from app.services.request_context import new_operation_id
    operation_id = new_operation_id()
    for field in fields:
        resulting = sorted(genre.name for genre in video.genres) if field == "genres" else getattr(video, field, None)
        record_field_event(
            db, video, field, event_type="field_changed", actor_kind="user",
            actor_id=user_id, prior_value=prior.get(field), resulting_value=resulting,
            provider="manual", transformation="manual_edit", operation_id=operation_id,
        )
