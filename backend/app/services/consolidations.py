"""Artist/genre consolidation aggregates, diagnostics and portable manifest."""
from __future__ import annotations

import json
import hashlib
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models import (
    ArtistConsolidation,
    ArtistConsolidationMbid,
    ArtistConsolidationTarget,
    Genre,
    GenreConsolidation,
    GenreConsolidationMember,
    FileOperation,
    Playlist,
    ReviewCase,
    VideoItem,
)
from app.services.sidecar_store import atomic_write_sidecar


def library_manifest_hash(manifest: dict[str, Any]) -> str:
    canonical = dict(manifest)
    canonical.pop("content_hash", None)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def validate_library_manifest(manifest: dict[str, Any]) -> None:
    if int(manifest.get("schema_version") or 0) not in (1, 2):
        raise ValueError("invalid Playarr library manifest schema")
    if "artist_consolidations" not in manifest:
        raise ValueError("invalid Playarr library manifest")
    claimed = manifest.get("content_hash")
    if claimed and claimed != library_manifest_hash(manifest):
        raise ValueError("Playarr library manifest content hash mismatch")


def load_library_manifest(path: str | Path) -> tuple[dict[str, Any], Path]:
    """Load the primary manifest, falling back to its atomic-write backup."""
    primary = Path(path)
    errors: list[str] = []
    for candidate in (primary, primary.with_name(primary.name + ".bak")):
        if not candidate.is_file():
            continue
        try:
            manifest = json.loads(candidate.read_text(encoding="utf-8"))
            validate_library_manifest(manifest)
            return manifest, candidate
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    raise ValueError("; ".join(errors) or f"library manifest not found: {primary}")


def serialize_artist_consolidation(item: ArtistConsolidation) -> dict[str, Any]:
    return {
        "id": item.id,
        "stable_id": item.stable_id,
        "mask_name": item.mask_name,
        "revision": item.revision,
        "created_by": item.created_by,
        "targets": [
            {"id": target.id, "raw_name": target.raw_name, "provenance": target.provenance,
             "provenance_json": target.provenance_json, "mb_artist_id": target.mb_artist_id}
            for target in sorted(item.targets, key=lambda value: value.raw_name.casefold())
        ],
        "mbids": sorted(member.mb_artist_id for member in item.mbids),
    }


def list_artist_consolidations(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(ArtistConsolidation)
        .options(selectinload(ArtistConsolidation.targets), selectinload(ArtistConsolidation.mbids))
        .filter(ArtistConsolidation.deleted_at.is_(None))
        .order_by(ArtistConsolidation.mask_name)
        .all()
    )
    return [serialize_artist_consolidation(row) for row in rows]


def matching_artist_consolidation(db: Session, raw_name: str, mbid: str | None) -> dict[str, Any] | None:
    query = db.query(ArtistConsolidation).options(
        selectinload(ArtistConsolidation.targets), selectinload(ArtistConsolidation.mbids),
    ).filter(ArtistConsolidation.deleted_at.is_(None))
    if mbid:
        query = query.join(ArtistConsolidationMbid).filter(ArtistConsolidationMbid.mb_artist_id == mbid)
    else:
        query = query.join(ArtistConsolidationTarget).filter(
            ArtistConsolidationTarget.raw_name == raw_name,
        )
    item = query.first()
    return serialize_artist_consolidation(item) if item else None


def serialize_genre_consolidation(item: GenreConsolidation, db: Session | None = None) -> dict[str, Any]:
    counts: dict[str, int] = {}
    if db is not None and item.members:
        from app.models import video_genres
        rows = (
            db.query(Genre.name, func.count(video_genres.c.video_id))
            .outerjoin(video_genres, video_genres.c.genre_id == Genre.id)
            .filter(Genre.name.in_([member.raw_name for member in item.members]))
            .group_by(Genre.name).all()
        )
        counts = {name: count for name, count in rows}
    return {
        "id": item.id,
        "stable_id": item.stable_id,
        "mask_name": item.mask_name,
        "revision": item.revision,
        "created_by": item.created_by,
        "target_genres": [{
            "id": member.id,
            "raw_name": member.raw_name,
            "provenance_json": member.provenance_json,
            "linked_video_count": counts.get(member.raw_name, 0),
        } for member in sorted(item.members, key=lambda value: value.raw_name.casefold())],
    }


def list_genre_consolidations(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(GenreConsolidation)
        .options(selectinload(GenreConsolidation.members))
        .filter(GenreConsolidation.deleted_at.is_(None))
        .order_by(GenreConsolidation.mask_name).all()
    )
    return [serialize_genre_consolidation(row, db) for row in rows]


def migrate_legacy_genre_consolidations(db: Session) -> int:
    """Move legacy ``genres.master_genre_id`` masks into revisioned aggregates.

    Raw Genre rows and their video relationships are deliberately retained.  The
    legacy relationship is cleared only after its equivalent durable aggregate
    exists, making this safe to retry after an interrupted startup.
    """
    aliases = (
        db.query(Genre)
        .filter(Genre.master_genre_id.isnot(None))
        .order_by(Genre.master_genre_id, Genre.name)
        .all()
    )
    if not aliases:
        return 0

    by_master: dict[int, list[Genre]] = defaultdict(list)
    for alias in aliases:
        if alias.master_genre_id is not None:
            by_master[alias.master_genre_id].append(alias)
    masters = {
        row.id: row
        for row in db.query(Genre).filter(Genre.id.in_(list(by_master))).all()
    }
    active = (
        db.query(GenreConsolidation)
        .options(selectinload(GenreConsolidation.members))
        .filter(GenreConsolidation.deleted_at.is_(None))
        .all()
    )
    active_by_mask = {row.mask_name.casefold(): row for row in active}
    migrated = 0

    for master_id, grouped_aliases in by_master.items():
        master = masters.get(master_id)
        if master is None:
            continue
        aggregate = active_by_mask.get(master.name.casefold())
        if aggregate is None:
            aggregate = GenreConsolidation(mask_name=master.name, created_by="legacy_migration")
            db.add(aggregate)
            active_by_mask[master.name.casefold()] = aggregate
        existing = {member.raw_name.casefold() for member in aggregate.members}
        for genre in [master, *grouped_aliases]:
            if genre.name.casefold() not in existing:
                aggregate.members.append(GenreConsolidationMember(
                    raw_name=genre.name,
                    provenance_json={"source": "legacy_master_genre", "genre_id": genre.id},
                ))
                existing.add(genre.name.casefold())
        for alias in grouped_aliases:
            alias.master_genre_id = None
        migrated += 1

    db.commit()
    return migrated


def genre_display_map(db: Session) -> dict[str, str]:
    """Resolve raw tags through active stable aggregates, with legacy fallback."""
    mapping: dict[str, str] = {}
    rows = (
        db.query(GenreConsolidation)
        .options(selectinload(GenreConsolidation.members))
        .filter(GenreConsolidation.deleted_at.is_(None)).all()
    )
    for item in rows:
        for member in item.members:
            mapping[member.raw_name] = item.mask_name
    if db.query(GenreConsolidation.id).first() is not None:
        return mapping
    legacy_rows = db.query(Genre.name, Genre.master_genre_id).filter(Genre.master_genre_id.isnot(None)).all()
    if legacy_rows:
        master_ids = {row.master_genre_id for row in legacy_rows}
        masters = {row.id: row.name for row in db.query(Genre.id, Genre.name).filter(Genre.id.in_(master_ids)).all()}
        for row in legacy_rows:
            mapping.setdefault(row.name, masters.get(row.master_genre_id, row.name))
    return mapping


def consolidation_conflicts(db: Session) -> list[dict[str, Any]]:
    """One diagnostics source used by both counts and list UI."""
    conflicts: list[dict[str, Any]] = []
    rows = (
        db.query(VideoItem.artist, VideoItem.mb_artist_id, func.count(VideoItem.id))
        .filter(VideoItem.artist.isnot(None))
        .group_by(VideoItem.artist, VideoItem.mb_artist_id)
        .all()
    )
    names_by_mbid: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    identities: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    no_mbid_names: dict[str, int] = defaultdict(int)
    for name, mbid, count in rows:
        primary = (name or "").split(";")[0].strip()
        normalized = re.sub(r"[^a-z0-9]", "", primary.casefold())
        if mbid:
            names_by_mbid[mbid][primary] += count
            identities[normalized][mbid][primary] += count
        elif primary:
            no_mbid_names[primary] += count
    for mbid, names in names_by_mbid.items():
        if len({name.casefold() for name in names}) > 1:
            conflicts.append({
                "type": "same_mbid_different_name", "mbids": [mbid],
                "names": sorted(names), "confidence": 1.0,
                "targets": [
                    {"raw_name": name, "mb_artist_id": mbid, "video_count": count}
                    for name, count in sorted(names.items())
                ],
            })
    for normalized, by_mbid in identities.items():
        if normalized and len(by_mbid) > 1:
            targets = [
                {"raw_name": name, "mb_artist_id": mbid, "video_count": count}
                for mbid, names in sorted(by_mbid.items())
                for name, count in sorted(names.items())
            ]
            conflicts.append({
                "type": "multiple_mbid_identity", "mbids": sorted(by_mbid),
                "names": sorted({target["raw_name"] for target in targets}),
                "targets": targets, "confidence": 0.98,
            })
    names = sorted(no_mbid_names)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            score = SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
            if score >= 0.88:
                conflicts.append({
                    "type": "similar_name_no_mbid", "mbids": [], "names": [left, right],
                    "targets": [
                        {"raw_name": left, "mb_artist_id": None, "video_count": no_mbid_names[left]},
                        {"raw_name": right, "mb_artist_id": None, "video_count": no_mbid_names[right]},
                    ],
                    "confidence": round(score, 3),
                })
    target_membership: dict[str, list[str]] = defaultdict(list)
    for item in list_artist_consolidations(db):
        for target in item["targets"]:
            target_membership[target["raw_name"].casefold()].append(item["stable_id"])
    for name, stable_ids in target_membership.items():
        if len(stable_ids) > 1:
            conflicts.append({"type": "target_multiple_consolidations", "mbids": [], "names": [name], "consolidation_ids": stable_ids, "confidence": 1.0})

    # Raw provider values remain immutable, so a resolved consolidation would
    # otherwise be rediscovered forever. A conflict is resolved when one active
    # aggregate covers every implicated raw name and MBID.
    active = list_artist_consolidations(db)
    unresolved: list[dict[str, Any]] = []
    for conflict in conflicts:
        if conflict["type"] == "target_multiple_consolidations":
            unresolved.append(conflict)
            continue
        conflict_names = {name.casefold() for name in conflict.get("names") or []}
        conflict_mbids = set(conflict.get("mbids") or [])
        covered = any(
            conflict_names.issubset({target["raw_name"].casefold() for target in item["targets"]})
            and conflict_mbids.issubset(set(item["mbids"]))
            for item in active
        )
        if not covered:
            unresolved.append(conflict)
    return unresolved


def library_consolidation_manifest(db: Session) -> dict[str, Any]:
    playlists = []
    for playlist in db.query(Playlist).order_by(Playlist.stable_id).all():
        playlists.append({
            "stable_id": playlist.stable_id,
            "revision": playlist.revision,
            "name": playlist.name,
            "description": playlist.description,
            "entries": [{
                "occurrence_id": entry.occurrence_id,
                "video_ref": (
                    entry.video_item.playarr_video_id or entry.video_item.stable_id
                    if entry.video_item else None
                ),
                "position": entry.position,
            } for entry in playlist.entries],
        })
    review_cases = []
    for case in db.query(ReviewCase).order_by(ReviewCase.stable_id).all():
        review_cases.append({
            "stable_id": case.stable_id,
            "category": case.category,
            "status": case.status,
            "revision": case.revision,
            "trigger_code": case.trigger_code,
            "evidence_hash": case.evidence_hash,
            "dismissed_evidence_hash": case.dismissed_evidence_hash,
            "evidence": case.evidence_json,
            "items": [{
                "video_ref": item.video_stable_id,
                "role": item.role,
                "evidence_summary": item.evidence_summary_json,
            } for item in case.items],
            "edges": [{
                "left_video_ref": edge.left_video_stable_id,
                "right_video_ref": edge.right_video_stable_id,
                "evidence_type": edge.evidence_type,
                "score": edge.score,
                "evidence_hash": edge.evidence_hash,
                "evidence": edge.evidence_json,
                "status": edge.status,
            } for edge in case.edges],
            "plans": [{
                "id": plan.id,
                "expected_revision": plan.expected_revision,
                "actions": plan.actions_json,
                "consequences": plan.consequence_json,
                "status": plan.status,
                "created_at": plan.created_at.isoformat() if plan.created_at else None,
                "committed_at": plan.committed_at.isoformat() if plan.committed_at else None,
            } for plan in case.plans],
        })
    archive_operations = [{
        "operation_id": operation.id,
        "entity_id": operation.entity_stable_id,
        "playarr_video_id": (operation.plan_json or {}).get("playarr_video_id"),
        "operation_type": operation.operation_type,
        "status": operation.status,
        "archive_relative_path": (operation.plan_json or {}).get("archive_relative_path"),
        "checksum": (operation.plan_json or {}).get("checksum"),
        "reason": (operation.plan_json or {}).get("reason"),
    } for operation in db.query(FileOperation).filter(
        FileOperation.operation_type.in_(("archive", "restore", "replace")),
    ).order_by(FileOperation.created_at).all()]
    return {
        "schema_version": 2,
        "artist_consolidations": list_artist_consolidations(db),
        "genre_consolidations": list_genre_consolidations(db),
        "playlists": playlists,
        "review_cases": review_cases,
        "archive_operations": archive_operations,
    }


def write_library_consolidation_manifest(db: Session) -> Path:
    path = Path(get_settings().library_dir) / ".playarr-library-manifest.json"
    manifest = library_consolidation_manifest(db)
    manifest["content_hash"] = library_manifest_hash(manifest)
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

    def validate_json(candidate: Path) -> None:
        parsed = json.loads(candidate.read_text(encoding="utf-8"))
        validate_library_manifest(parsed)

    return atomic_write_sidecar(path, payload, validator=validate_json)
