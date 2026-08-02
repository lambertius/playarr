"""Revisioned, non-destructive genre consolidation API."""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import GenreConsolidation, GenreConsolidationMember
from app.services.consolidations import (
    list_genre_consolidations,
    serialize_genre_consolidation,
    write_library_consolidation_manifest,
)

router = APIRouter(prefix="/api/metadata", tags=["Metadata"])


class GenreConsolidationMemberInput(BaseModel):
    raw_name: str
    provenance_json: Optional[dict] = None


class GenreConsolidationCreate(BaseModel):
    mask_name: str
    target_genres: List[GenreConsolidationMemberInput] = Field(default_factory=list)


class GenreConsolidationUpdate(GenreConsolidationCreate):
    expected_revision: int


def _replace_members(item: GenreConsolidation, members: List[GenreConsolidationMemberInput]) -> None:
    item.members.clear()
    seen: set[str] = set()
    for member in members:
        raw_name = member.raw_name.strip()
        if raw_name and raw_name.casefold() not in seen:
            seen.add(raw_name.casefold())
            item.members.append(GenreConsolidationMember(
                raw_name=raw_name, provenance_json=member.provenance_json,
            ))


def _active(db: Session, stable_id: str) -> GenreConsolidation:
    item = db.query(GenreConsolidation).filter(
        GenreConsolidation.stable_id == stable_id, GenreConsolidation.deleted_at.is_(None),
    ).one_or_none()
    if not item:
        raise HTTPException(404, "Genre consolidation not found")
    return item


@router.get("/genre-consolidations-v2")
def get_genre_consolidations_v2(db: Session = Depends(get_db)):
    return list_genre_consolidations(db)


@router.post("/genre-consolidations-v2", status_code=201)
def create_genre_consolidation_v2(body: GenreConsolidationCreate, db: Session = Depends(get_db)):
    mask_name = body.mask_name.strip()
    if not mask_name:
        raise HTTPException(422, "Mask name is required")
    item = GenreConsolidation(mask_name=mask_name)
    _replace_members(item, body.target_genres)
    db.add(item); db.commit(); db.refresh(item)
    write_library_consolidation_manifest(db)
    return serialize_genre_consolidation(item, db)


@router.put("/genre-consolidations-v2/{stable_id}")
def update_genre_consolidation_v2(stable_id: str, body: GenreConsolidationUpdate, db: Session = Depends(get_db)):
    item = _active(db, stable_id)
    if item.revision != body.expected_revision:
        raise HTTPException(409, {"code": "stale_revision", "current_revision": item.revision})
    mask_name = body.mask_name.strip()
    if not mask_name:
        raise HTTPException(422, "Mask name is required")
    item.mask_name = mask_name
    item.members.clear(); db.flush(); _replace_members(item, body.target_genres)
    item.revision += 1; db.commit()
    write_library_consolidation_manifest(db)
    return serialize_genre_consolidation(item, db)


@router.delete("/genre-consolidations-v2/{stable_id}")
def delete_genre_consolidation_v2(stable_id: str, expected_revision: int, db: Session = Depends(get_db)):
    item = _active(db, stable_id)
    if item.revision != expected_revision:
        raise HTTPException(409, {"code": "stale_revision", "current_revision": item.revision})
    item.deleted_at = datetime.now(timezone.utc); item.revision += 1; db.commit()
    write_library_consolidation_manifest(db)
    return {"deleted": True, "stable_id": stable_id}
