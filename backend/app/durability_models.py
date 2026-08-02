"""Focused durable provenance and consolidation aggregates."""
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FieldProvenanceEvent(Base):
    __tablename__ = "field_provenance_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    video_id: Mapped[Optional[int]] = mapped_column(ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True, index=True)
    video_stable_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    actor_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    model_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    remote_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    transformation: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    prior_value_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resulting_value_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    verification_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    operation_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)


class GenreConsolidation(Base):
    __tablename__ = "genre_consolidations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stable_id: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()), unique=True, nullable=False, index=True)
    mask_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    members: Mapped[List["GenreConsolidationMember"]] = relationship(back_populates="consolidation", cascade="all, delete-orphan")


class GenreConsolidationMember(Base):
    __tablename__ = "genre_consolidation_members"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    consolidation_id: Mapped[int] = mapped_column(ForeignKey("genre_consolidations.id", ondelete="CASCADE"), nullable=False, index=True)
    raw_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    provenance_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    consolidation: Mapped["GenreConsolidation"] = relationship(back_populates="members")
    __table_args__ = (UniqueConstraint("consolidation_id", "raw_name", name="uq_genre_consolidation_member"),)


class ArchiveCatalogEntry(Base):
    """SQL-indexed projection of portable archive manifests."""
    __tablename__ = "archive_catalog_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    folder: Mapped[str] = mapped_column(String(2000), nullable=False, unique=True)
    path: Mapped[str] = mapped_column(String(2000), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False, default="edit", index=True)
    artist: Mapped[str] = mapped_column(String(500), nullable=False, default="", index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="", index=True)
    video_id: Mapped[Optional[int]] = mapped_column(ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True, index=True)
    video_stable_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    operation_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    original_path: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checksum_md5: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    manifest_schema_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    restore_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    integrity_status: Mapped[str] = mapped_column(String(40), nullable=False, default="unchecked", index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
