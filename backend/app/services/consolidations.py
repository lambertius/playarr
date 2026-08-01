"""Artist/genre consolidation aggregates, diagnostics and portable manifest."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models import (
    ArtistConsolidation,
    ArtistConsolidationMbid,
    ArtistConsolidationTarget,
    Genre,
    VideoItem,
)
from app.services.sidecar_store import atomic_write_sidecar


def serialize_artist_consolidation(item: ArtistConsolidation) -> dict[str, Any]:
    return {
        "id": item.id,
        "stable_id": item.stable_id,
        "mask_name": item.mask_name,
        "revision": item.revision,
        "targets": [
            {"id": target.id, "raw_name": target.raw_name, "provenance": target.provenance, "mb_artist_id": target.mb_artist_id}
            for target in sorted(item.targets, key=lambda value: value.raw_name.casefold())
        ],
        "mbids": sorted(member.mb_artist_id for member in item.mbids),
    }


def list_artist_consolidations(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(ArtistConsolidation)
        .options(selectinload(ArtistConsolidation.targets), selectinload(ArtistConsolidation.mbids))
        .order_by(ArtistConsolidation.mask_name)
        .all()
    )
    return [serialize_artist_consolidation(row) for row in rows]


def matching_artist_consolidation(db: Session, raw_name: str, mbid: str | None) -> dict[str, Any] | None:
    query = db.query(ArtistConsolidation).options(
        selectinload(ArtistConsolidation.targets), selectinload(ArtistConsolidation.mbids),
    )
    if mbid:
        query = query.join(ArtistConsolidationMbid).filter(ArtistConsolidationMbid.mb_artist_id == mbid)
    else:
        query = query.join(ArtistConsolidationTarget).filter(
            ArtistConsolidationTarget.raw_name == raw_name,
        )
    item = query.first()
    return serialize_artist_consolidation(item) if item else None


def consolidation_conflicts(db: Session) -> list[dict[str, Any]]:
    """One diagnostics source used by both counts and list UI."""
    conflicts: list[dict[str, Any]] = []
    rows = db.query(VideoItem.artist, VideoItem.mb_artist_id).filter(VideoItem.artist.isnot(None)).all()
    names_by_mbid: dict[str, set[str]] = defaultdict(set)
    mbids_by_name: dict[str, set[str]] = defaultdict(set)
    no_mbid_names: set[str] = set()
    for name, mbid in rows:
        primary = (name or "").split(";")[0].strip()
        normalized = re.sub(r"[^a-z0-9]", "", primary.casefold())
        if mbid:
            names_by_mbid[mbid].add(primary)
            mbids_by_name[normalized].add(mbid)
        elif primary:
            no_mbid_names.add(primary)
    for mbid, names in names_by_mbid.items():
        if len({name.casefold() for name in names}) > 1:
            conflicts.append({"type": "same_mbid_different_name", "mbids": [mbid], "names": sorted(names), "confidence": 1.0})
    for normalized, mbids in mbids_by_name.items():
        if normalized and len(mbids) > 1:
            conflicts.append({"type": "multiple_mbid_identity", "mbids": sorted(mbids), "names": [normalized], "confidence": 0.98})
    names = sorted(no_mbid_names)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            score = SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
            if score >= 0.88:
                conflicts.append({"type": "similar_name_no_mbid", "mbids": [], "names": [left, right], "confidence": round(score, 3)})
    target_membership: dict[str, list[str]] = defaultdict(list)
    for item in list_artist_consolidations(db):
        for target in item["targets"]:
            target_membership[target["raw_name"].casefold()].append(item["stable_id"])
    for name, stable_ids in target_membership.items():
        if len(stable_ids) > 1:
            conflicts.append({"type": "target_multiple_consolidations", "mbids": [], "names": [name], "consolidation_ids": stable_ids, "confidence": 1.0})
    return conflicts


def library_consolidation_manifest(db: Session) -> dict[str, Any]:
    genre_groups: dict[int, dict[str, Any]] = {}
    aliases = db.query(Genre).filter(Genre.master_genre_id.isnot(None)).all()
    for alias in aliases:
        master = db.get(Genre, alias.master_genre_id)
        if not master:
            continue
        group = genre_groups.setdefault(master.id, {"mask_name": master.name, "target_genres": [master.name]})
        group["target_genres"].append(alias.name)
    return {
        "schema_version": 1,
        "artist_consolidations": list_artist_consolidations(db),
        "genre_consolidations": list(genre_groups.values()),
    }


def write_library_consolidation_manifest(db: Session) -> Path:
    path = Path(get_settings().library_dir) / ".playarr-library-manifest.json"
    payload = json.dumps(library_consolidation_manifest(db), indent=2, sort_keys=True).encode("utf-8")

    def validate_json(candidate: Path) -> None:
        parsed = json.loads(candidate.read_text(encoding="utf-8"))
        if parsed.get("schema_version") != 1 or "artist_consolidations" not in parsed:
            raise ValueError("invalid Playarr library manifest")

    return atomic_write_sidecar(path, payload, validator=validate_json)
