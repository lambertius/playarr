"""
Canonical Entity Graph — Database models for the metadata subsystem.

Defines: ArtistEntity, AlbumEntity, TrackEntity, CachedAsset,
MetadataRevision, ExportManifest, and genre association tables.

All models share the same ``Base`` as the rest of Playarr so they are
picked up by ``create_all`` and Alembic automatically.
"""
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime,
    ForeignKey, JSON, Table, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column, validates

from app.database import Base

# ---------------------------------------------------------------------------
# Genre association tables for entities
# ---------------------------------------------------------------------------

artist_genres = Table(
    "artist_genres",
    Base.metadata,
    Column("artist_id", Integer, ForeignKey("artists.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)

album_genres = Table(
    "album_genres",
    Base.metadata,
    Column("album_id", Integer, ForeignKey("albums.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)

track_genres = Table(
    "track_genres",
    Base.metadata,
    Column("track_id", Integer, ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)


# ---------------------------------------------------------------------------
# ArtistEntity
# ---------------------------------------------------------------------------

class ArtistEntity(Base):
    """Canonical artist record — the stable identity for an artist/band."""
    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    sort_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    mb_artist_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, unique=True, index=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    origin: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # city/region of origin
    disambiguation: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    biography: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aliases: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # list of strings
    artist_image: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)  # cached image path

    # Confidence & review
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    # Field-level provenance — tracks which provider sourced each field
    # JSON dict: {"canonical_name": "musicbrainz", "biography": "wikipedia", ...}
    field_provenance: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Field-level user attribution — which user last set each field
    field_provenance_users: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    albums: Mapped[List["AlbumEntity"]] = relationship(back_populates="artist", cascade="save-update, merge")
    tracks: Mapped[List["TrackEntity"]] = relationship(back_populates="artist", cascade="save-update, merge")
    genres: Mapped[List["Genre"]] = relationship(secondary=artist_genres, backref="artists")

    __table_args__ = (
        UniqueConstraint("canonical_name", name="uq_artist_canonical"),
    )

    def __repr__(self):
        return f"<ArtistEntity {self.id}: {self.canonical_name}>"


# ---------------------------------------------------------------------------
# AlbumEntity (Release)
# ---------------------------------------------------------------------------

class AlbumEntity(Base):
    """Canonical album / release record."""
    __tablename__ = "albums"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    artist_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("artists.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    release_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # ISO
    mb_release_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, unique=True, index=True)
    mb_release_group_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    album_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # album|ep|single|compilation
    cover_image: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)  # cached cover image path

    # Confidence & review
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    # Field-level provenance
    field_provenance: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Field-level user attribution — which user last set each field
    field_provenance_users: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    artist: Mapped[Optional["ArtistEntity"]] = relationship(back_populates="albums")
    tracks: Mapped[List["TrackEntity"]] = relationship(back_populates="album", cascade="save-update, merge")
    genres: Mapped[List["Genre"]] = relationship(secondary=album_genres, backref="albums")

    __table_args__ = (
        UniqueConstraint("title", "artist_id", name="uq_album_title_artist"),
    )

    def __repr__(self):
        return f"<AlbumEntity {self.id}: {self.title}>"


# ---------------------------------------------------------------------------
# TrackEntity (Canonical Track / Recording)
# ---------------------------------------------------------------------------

class TrackEntity(Base):
    """
    Canonical track — represents a song identity independent of any specific
    video.  This is the entity used to reduce AI token usage: AI metadata
    verification runs once per canonical track.

    Multiple VideoItems (official video, live video, alternate cut) can
    share the same canonical track and reuse its cached metadata while
    retaining their own video-specific descriptions.
    """
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    artist_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("artists.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    album_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("albums.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # MusicBrainz IDs
    mb_recording_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, unique=True, index=True)
    mb_release_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    mb_release_group_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    mb_artist_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    mb_track_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    # Audio fingerprinting
    acoustid_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    audio_fingerprint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    track_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Artwork paths (cached at canonical level)
    artwork_album: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    artwork_single: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    # Canonical verification flags — AI metadata runs once per canonical track
    canonical_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # scraper|musicbrainz|ai
    ai_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # For cover tracks: link to original song identity
    original_artist: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    original_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_cover: Mapped[bool] = mapped_column(Boolean, default=False)

    # Confidence & review
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    # Field-level provenance
    field_provenance: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Field-level user attribution — which user last set each field
    field_provenance_users: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    artist: Mapped[Optional["ArtistEntity"]] = relationship(back_populates="tracks")
    album: Mapped[Optional["AlbumEntity"]] = relationship(back_populates="tracks")
    videos: Mapped[List["VideoItem"]] = relationship(
        "VideoItem", back_populates="track_entity",
        foreign_keys="VideoItem.track_id",
    )
    genres: Mapped[List["Genre"]] = relationship(secondary=track_genres, backref="tracks")

    def __repr__(self):
        return f"<TrackEntity {self.id}: {self.title}>"


# ---------------------------------------------------------------------------
# CachedAsset — central asset cache (entity-polymorphic)
# ---------------------------------------------------------------------------

class CachedAsset(Base):
    """
    Centrally cached artwork file.

    One image = one record regardless of how many export targets use it.
    The ``entity_type`` + ``entity_id`` pair links to artists/albums/videos.
    """
    __tablename__ = "cached_assets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # artist|album|video
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    kind: Mapped[str] = mapped_column(String(50), nullable=False)  # poster|thumb|fanart|logo|banner
    source_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    resolved_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)  # final URL after redirects
    local_cache_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # SHA-256
    file_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # SHA-256 of original download
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    format: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # jpeg|png|webp
    content_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # HTTP Content-Type
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    provenance: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source_provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # musicbrainz|wikipedia|coverartarchive|youtube
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    crop_position: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # CSS object-position e.g. "50% 30%"

    # Validity tracking
    status: Mapped[str] = mapped_column(String(20), default="valid", server_default="valid")  # valid|invalid|missing|pending
    validation_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_cached_asset_entity", "entity_type", "entity_id"),
        UniqueConstraint("entity_type", "entity_id", "kind", name="uq_cached_asset_entity_kind"),
    )

    @validates("status")
    def _require_status(self, key, value):
        """Soft guard: warn if status is not set to a known value."""
        import logging
        _VALID_STATUSES = {"valid", "invalid", "missing", "pending", "unavailable"}
        if value and value not in _VALID_STATUSES:
            logging.getLogger(__name__).warning(
                f"CachedAsset status '{value}' is not a recognized value "
                f"(expected one of {_VALID_STATUSES})"
            )
        return value


# ---------------------------------------------------------------------------
# MetadataRevision — entity-level snapshots for rollback
# ---------------------------------------------------------------------------

class MetadataRevision(Base):
    """
    Entity-level metadata snapshot.

    Supports any entity type (artist / album / track / video).  Each
    revision stores the full field state and the provider that produced it,
    enabling deterministic rollback.
    """
    __tablename__ = "metadata_revisions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    fields: Mapped[dict] = mapped_column(JSON, nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    # auto_import | forced_refresh | manual_edit

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_meta_rev_entity", "entity_type", "entity_id"),
    )


# ---------------------------------------------------------------------------
# ExportManifest — track what was exported (for incremental + cleanup)
# ---------------------------------------------------------------------------

class ExportManifest(Base):
    """
    Per-entity record of what was written by an exporter.

    Enables incremental export (only rewrite changed items) and
    stale-output cleanup (delete paths no longer referenced).
    """
    __tablename__ = "export_manifests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    export_target: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # "kodi"
    export_root: Mapped[str] = mapped_column(String(1000), nullable=False)

    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)

    exported_paths: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # list of paths
    exported_version: Mapped[int] = mapped_column(Integer, default=1)

    last_exported_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("export_target", "entity_type", "entity_id", name="uq_manifest_entity"),
        Index("ix_manifest_entity", "export_target", "entity_type", "entity_id"),
    )


# ---------------------------------------------------------------------------
# Import Genre from main models so relationships resolve
# ---------------------------------------------------------------------------
from app.models import Genre  # noqa: E402  — must import after Base is defined
