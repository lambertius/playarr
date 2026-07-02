"""
Provenance envelope — single source of truth for what Playarr contributes
to an external database (The Music Video DB).

Builds a rich, per-field "contribution envelope" carrying the full trust
signal so the remote can weight and score each value:

- the value itself
- its source (which provider, or "manual")
- who set / verified it (anonymous instance user id)
- when it was set / verified
- a derived trust level ("human_edited" > "human_verified" > "automated")
- whether the field is locked locally

It also carries every identity key (Playarr IDs, MusicBrainz IDs, AcoustID,
audio fingerprint, perceptual hash, file checksum) so the remote can dedup
and merge contributions across instances.

The format is "push all, tagged": every field is included with its trust
level; the remote decides how to weight it.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Current envelope schema version — bump on breaking format changes.
SCHEMA_VERSION = "1.0"

# Metadata fields contributed with full per-field provenance.
_METADATA_FIELDS = ("artist", "title", "album", "year", "plot")

# Trust levels, strongest first — the remote weights contributions by these.
TRUST_HUMAN_EDITED = "human_edited"      # a human typed/chose this value
TRUST_HUMAN_VERIFIED = "human_verified"  # a human confirmed an auto value unchanged
TRUST_AUTOMATED = "automated"            # sourced from a provider, untouched by a human
TRUST_UNKNOWN = "unknown"                # legacy data with no recorded source


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _field_trust(source: Optional[str], edited_by: Optional[str],
                 verified: Optional[dict]) -> str:
    """Derive a trust level for a single field from its provenance."""
    if edited_by or source == "manual":
        return TRUST_HUMAN_EDITED
    if verified:
        return TRUST_HUMAN_VERIFIED
    if source:
        return TRUST_AUTOMATED
    return TRUST_UNKNOWN


def _field_envelope(video, field: str) -> Dict[str, Any]:
    prov = video.field_provenance or {}
    users = video.field_provenance_users or {}
    set_at = video.field_provenance_at or {}
    verifs = video.field_verifications or {}
    locked = set(video.locked_fields or [])

    source = prov.get(field)
    edited_by = users.get(field)
    verified = verifs.get(field)

    return {
        "value": getattr(video, field, None),
        "source": source,
        "edited_by": edited_by,
        "verified_by": (verified or {}).get("by") if verified else None,
        "verified_at": (verified or {}).get("at") if verified else None,
        "set_at": set_at.get(field),
        "trust": _field_trust(source, edited_by, verified),
        "locked": field in locked,
    }


def mark_fields_verified(video, user_id: str,
                         fields: Optional[List[str]] = None) -> List[str]:
    """Record that a human confirmed automated field values without changing them.

    This is the "human_verified" trust signal — distinct from editing. Only
    applies to populated fields whose current value came from an automated
    source (not already human-edited). Returns the list of fields newly marked.

    Caller is responsible for flag_modified('field_verifications') and commit.
    """
    if fields is None:
        fields = list(_METADATA_FIELDS)

    prov = video.field_provenance or {}
    users = video.field_provenance_users or {}
    now = datetime.now(timezone.utc).isoformat()
    verifs = dict(video.field_verifications or {})
    marked: List[str] = []

    for f in fields:
        value = getattr(video, f, None)
        if value is None or value == "":
            continue
        # Skip fields a human already authored — editing already outranks verifying.
        if users.get(f) or prov.get(f) == "manual":
            continue
        verifs[f] = {"by": user_id, "at": now, "from": prov.get(f)}
        marked.append(f)

    if marked:
        video.field_verifications = verifs
    return marked


def build_contribution_envelope(video, instance_user_id: str) -> Dict[str, Any]:
    """Build the full tagged contribution envelope for a VideoItem.

    `instance_user_id` is this install's stable anonymous id — the trust anchor.
    """
    duration = None
    qs = getattr(video, "quality_signature", None)
    if qs is not None:
        duration = getattr(qs, "duration_seconds", None)

    identity = {
        "playarr_track_id": video.playarr_track_id,
        "playarr_video_id": video.playarr_video_id,
        "mb_recording_id": video.mb_recording_id,
        "mb_artist_id": video.mb_artist_id,
        "mb_release_id": video.mb_release_id,
        "mb_release_group_id": video.mb_release_group_id,
        "mb_track_id": video.mb_track_id,
        "acoustid_id": video.acoustid_id,
        "audio_fingerprint": video.audio_fingerprint,
        "video_phash": video.video_phash,
        "file_checksum": getattr(video, "file_checksum", None),
        "duration_seconds": duration,
    }

    fields = {f: _field_envelope(video, f) for f in _METADATA_FIELDS}

    ratings = {
        "song_rating": {
            "value": video.song_rating,
            "set": bool(video.song_rating_set),
            "by": getattr(video, "song_rating_by", None),
            "at": _iso(getattr(video, "song_rating_at", None)),
        },
        "video_rating": {
            "value": video.video_rating,
            "set": bool(video.video_rating_set),
            "by": getattr(video, "video_rating_by", None),
            "at": _iso(getattr(video, "video_rating_at", None)),
        },
    }

    genre_source = (video.field_provenance or {}).get("genres")
    genre_by = (video.field_provenance_users or {}).get("genres")
    genres = [
        {
            "name": g.name,
            "source": genre_source,
            "trust": _field_trust(genre_source, genre_by, None),
        }
        for g in getattr(video, "genres", [])
    ]

    assets = [
        {
            "type": a.asset_type,
            "provenance": a.provenance,
            "source_provider": a.source_provider,
            "source_url": a.source_url,
            "file_hash": a.file_hash,
            "width": a.width,
            "height": a.height,
        }
        for a in getattr(video, "media_assets", [])
        if getattr(a, "status", "valid") == "valid"
    ]

    sources = [
        {
            "provider": s.provider.value if hasattr(s.provider, "value") else s.provider,
            "url": s.canonical_url,
            "type": s.source_type,
            "provenance": s.provenance,
        }
        for s in getattr(video, "sources", [])
    ]

    envelope: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "instance_user_id": instance_user_id,
        "contributed_at": _iso(datetime.now(timezone.utc)),
        "identity": identity,
        "fields": fields,
        "ratings": ratings,
        "canonical": {
            "confidence": video.canonical_confidence,
            "provenance": video.canonical_provenance,
        },
        "version": {
            "version_type": video.version_type,
            "alternate_version_label": video.alternate_version_label,
            "original_artist": video.original_artist,
            "original_title": video.original_title,
        },
        "genres": genres,
        "assets": assets,
        "sources": sources,
    }
    envelope["payload_hash"] = compute_payload_hash(envelope)
    return envelope


def compute_payload_hash(envelope: Dict[str, Any]) -> str:
    """Stable SHA-256 over the *meaningful* content of an envelope.

    Excludes volatile fields (timestamps, the instance id) so re-pushing
    unchanged metadata yields the same hash — enabling idempotent skips.
    """
    material = {
        "identity": envelope.get("identity", {}),
        "fields": {
            k: {"value": v.get("value"), "source": v.get("source"), "trust": v.get("trust")}
            for k, v in envelope.get("fields", {}).items()
        },
        "ratings": {
            k: {"value": v.get("value"), "set": v.get("set")}
            for k, v in envelope.get("ratings", {}).items()
        },
        "version": envelope.get("version", {}),
        "genres": sorted(g.get("name", "") for g in envelope.get("genres", [])),
        "sources": sorted(
            (s.get("provider") or "") + "|" + (s.get("url") or "")
            for s in envelope.get("sources", [])
        ),
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
