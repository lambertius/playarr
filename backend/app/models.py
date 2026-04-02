"""
Playarr Database Models
=======================
Complete schema for music video management.

Tables:
- video_items: Core identity of each music video in the library
- sources: Provider URLs (YouTube, Vimeo) linked to video items
- quality_signatures: Media analysis results (resolution, codecs, bitrate)
- metadata_snapshots: Versioned metadata for undo/rollback
- media_assets: Poster/thumb images with provenance
- processing_jobs: Background job tracking with logs
- genres: Normalized genre table
- video_genres: M2M join for video <-> genre
- settings: Global and per-user settings KV store
- playback_history: Track what was played and when
- normalization_history: Audit trail for audio normalization runs
"""
import enum
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime,
    ForeignKey, Enum, JSON, UniqueConstraint, Index, Table,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column, validates

from app.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class VersionType(str, enum.Enum):
    normal = "normal"
    cover = "cover"
    live = "live"
    alternate = "alternate"
    uncensored = "uncensored"
    explicit = "18+"


class ReviewStatus(str, enum.Enum):
    none = "none"                    # No review needed
    needs_human_review = "needs_human_review"
    needs_ai_review = "needs_ai_review"
    reviewed = "reviewed"            # Review completed


class JobStatus(str, enum.Enum):
    queued = "queued"
    downloading = "downloading"
    downloaded = "downloaded"
    remuxing = "remuxing"
    analyzing = "analyzing"
    normalizing = "normalizing"
    tagging = "tagging"
    writing_nfo = "writing_nfo"
    asset_fetch = "asset_fetch"
    complete = "complete"
    failed = "failed"
    cancelled = "cancelled"
    skipped = "skipped"


class SourceProvider(str, enum.Enum):
    youtube = "youtube"
    vimeo = "vimeo"
    wikipedia = "wikipedia"
    imdb = "imdb"
    musicbrainz = "musicbrainz"
    tmvdb = "tmvdb"
    other = "other"


# ---------------------------------------------------------------------------
# Association table: video <-> genre (M2M)
# ---------------------------------------------------------------------------

video_genres = Table(
    "video_genres",
    Base.metadata,
    Column("video_id", Integer, ForeignKey("video_items.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)


# ---------------------------------------------------------------------------
# VideoItem — core identity
# ---------------------------------------------------------------------------

class VideoItem(Base):
    __tablename__ = "video_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Identity
    artist: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    album: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    plot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # MusicBrainz IDs
    mb_artist_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    mb_recording_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    mb_release_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    mb_release_group_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # FK links to canonical entity graph (nullable for backward compat)
    artist_entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("artists.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    album_entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("albums.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    track_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    # Version detection — cover / live / alternate / normal
    version_type: Mapped[str] = mapped_column(
        String(20), default="normal", server_default="normal", nullable=False, index=True,
    )
    alternate_version_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    original_artist: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    original_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    related_versions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # [{video_id, label}]

    # Review routing
    review_status: Mapped[str] = mapped_column(
        String(30), default="none", server_default="none", nullable=False, index=True,
    )
    review_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Structured category: version_detection, duplicate, import_error, url_import_error, manual_review
    review_category: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)

    # File system
    folder_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Computed display label, e.g. "1080p"
    resolution_label: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # User ratings (1-5 stars, default 3)
    song_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=3)
    video_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=3)
    song_rating_set: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    video_rating_set: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)

    # Locked fields — prevent overwrite on rescan
    locked_fields: Mapped[Optional[str]] = mapped_column(JSON, default=list)

    # Processing state — tracks which processing steps have been completed.
    # JSON dict keyed by step name, each value is:
    # {"completed": bool, "timestamp": str, "method": str, "version": str}
    # Step names: metadata_scraped, metadata_ai_analyzed, track_identified,
    # scenes_analyzed, audio_normalized, description_generated,
    # filename_checked, canonical_linked
    processing_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # How this video was added: "url", "import", "scanned", or NULL (legacy)
    import_method: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)

    # Audio fingerprint (Chromaprint) for canonical track identification
    audio_fingerprint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    acoustid_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    # Field-level provenance — tracks which provider sourced each metadata field
    # JSON dict: {"artist": "musicbrainz", "plot": "wikipedia", "album": "tmvdb", ...}
    field_provenance: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Video editor — exclude from future letterbox scans (false positive suppression)
    exclude_from_editor_scan: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    sources: Mapped[List["Source"]] = relationship(
        back_populates="video_item", cascade="all, delete-orphan"
    )
    quality_signature: Mapped[Optional["QualitySignature"]] = relationship(
        back_populates="video_item", uselist=False, cascade="all, delete-orphan"
    )
    metadata_snapshots: Mapped[List["MetadataSnapshot"]] = relationship(
        back_populates="video_item", cascade="all, delete-orphan",
        order_by="MetadataSnapshot.created_at.desc()"
    )
    media_assets: Mapped[List["MediaAsset"]] = relationship(
        back_populates="video_item", cascade="all, delete-orphan"
    )
    genres: Mapped[List["Genre"]] = relationship(
        secondary=video_genres, back_populates="video_items"
    )
    processing_jobs: Mapped[List["ProcessingJob"]] = relationship(
        back_populates="video_item", cascade="all, delete-orphan"
    )
    normalization_history: Mapped[List["NormalizationHistory"]] = relationship(
        back_populates="video_item", cascade="all, delete-orphan"
    )
    playback_history: Mapped[List["PlaybackHistory"]] = relationship(
        back_populates="video_item", cascade="all, delete-orphan"
    )

    # Relationships to entity graph
    artist_entity = relationship("ArtistEntity", foreign_keys=[artist_entity_id])
    album_entity = relationship("AlbumEntity", foreign_keys=[album_entity_id])
    track_entity = relationship("TrackEntity", back_populates="videos", foreign_keys=[track_id])

    def __repr__(self):
        return f"<VideoItem {self.id}: {self.artist} - {self.title}>"

    @validates("album")
    def _sanitize_album(self, _key, value):
        """Strip sentinel values like 'Unknown', 'N/A', etc."""
        if not value or not isinstance(value, str):
            return value
        _SENTINEL_VALUES = {
            "unknown", "unknown album", "n/a", "na", "none", "null",
            "nil", "no album", "untitled", "tbd", "not available",
            "not applicable", "-", "--", "\u2014", "?",
        }
        if value.strip().lower() in _SENTINEL_VALUES:
            return None
        return value


# ---------------------------------------------------------------------------
# Source — provider URLs
# ---------------------------------------------------------------------------

class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_items.id", ondelete="CASCADE"), index=True)

    provider: Mapped[SourceProvider] = mapped_column(Enum(SourceProvider), nullable=False)
    source_video_id: Mapped[str] = mapped_column(String(200), nullable=False)
    original_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2000), nullable=False)

    # Platform metadata (populated from yt-dlp at import time)
    channel_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    platform_title: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    platform_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    platform_tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    upload_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # YYYYMMDD from yt-dlp

    # Category — what this source relates to
    # Values: "video", "artist", "album", "single"
    source_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Provenance — how this source was discovered
    # Values: "ai" (AI source resolution), "scraped" (search-based scraping),
    #         "manual" (user-entered), "import" (from yt-dlp at import time)
    provenance: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    video_item: Mapped["VideoItem"] = relationship(back_populates="sources")

    __table_args__ = (
        UniqueConstraint("video_id", "provider", "source_video_id", name="uq_source_video_provider_vid"),
    )

    @validates("source_type")
    def _validate_source_type(self, _key, value):
        """Enforce source categorization rules.

        - Only platform providers (youtube, vimeo) may use source_type="video".
        - Wikipedia and MusicBrainz links must never be "video".
        - "recording" is a valid type for MB recordings without a single release.
        """
        if value == "video" and self.provider in (
            SourceProvider.wikipedia, SourceProvider.musicbrainz,
        ):
            import logging as _log
            _log.getLogger("playarr").warning(
                f"Source type 'video' rejected for provider {self.provider.value} "
                f"(url={getattr(self, 'original_url', '?')}). Coercing to 'single'."
            )
            value = "single"
        return value


# ---------------------------------------------------------------------------
# QualitySignature — media analysis result
# ---------------------------------------------------------------------------

class QualitySignature(Base):
    __tablename__ = "quality_signatures"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video_items.id", ondelete="CASCADE"), unique=True, index=True
    )

    # Video
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    video_codec: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    video_bitrate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # bps
    hdr: Mapped[bool] = mapped_column(Boolean, default=False)

    # Audio
    audio_codec: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    audio_bitrate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # bps
    audio_sample_rate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    audio_channels: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Container
    container: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Integrated loudness (LUFS)
    loudness_lufs: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    video_item: Mapped["VideoItem"] = relationship(back_populates="quality_signature")

    def quality_score(self) -> int:
        """Compute a comparable quality score (higher = better)."""
        score = 0
        if self.height:
            score += self.height * 1000
        if self.video_bitrate:
            score += self.video_bitrate // 1000
        if self.fps and self.fps > 30:
            score += 500
        if self.hdr:
            score += 2000
        return score


# ---------------------------------------------------------------------------
# MetadataSnapshot — versioned metadata for undo/rollback
# ---------------------------------------------------------------------------

class MetadataSnapshot(Base):
    __tablename__ = "metadata_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_items.id", ondelete="CASCADE"), index=True)

    # Snapshot of all metadata fields as JSON
    snapshot_data: Mapped[dict] = mapped_column(JSON, nullable=False)

    # What triggered this snapshot
    reason: Mapped[str] = mapped_column(String(200), nullable=False)  # e.g. "auto_import", "manual_rescan", "manual_edit"

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    video_item: Mapped["VideoItem"] = relationship(back_populates="metadata_snapshots")


# ---------------------------------------------------------------------------
# MediaAsset — poster/thumb images
# ---------------------------------------------------------------------------

class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_items.id", ondelete="CASCADE"), index=True)

    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "poster", "thumb", "fanart"
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    resolved_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)  # final URL after redirects
    provenance: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # "wikipedia", "musicbrainz", "youtube_thumb"
    source_provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # musicbrainz|wikipedia|coverartarchive|youtube
    content_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # HTTP Content-Type
    file_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # SHA-256
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Validity tracking
    status: Mapped[str] = mapped_column(String(20), default="valid", server_default="valid")  # valid|invalid|missing|pending
    validation_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    video_item: Mapped["VideoItem"] = relationship(back_populates="media_assets")

    @validates("provenance")
    def _require_provenance(self, key, value):
        """Warn if a MediaAsset is created without provenance.

        This is a soft guard — it logs a warning rather than raising,
        to avoid breaking existing code during the transition period.
        Once all callers are migrated, this can be made strict.
        """
        import logging
        if not value:
            logging.getLogger(__name__).warning(
                "MediaAsset created without provenance — set provenance "
                "to track the origin of this asset."
            )
        return value


# ---------------------------------------------------------------------------
# Genre — normalized
# ---------------------------------------------------------------------------

class Genre(Base):
    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    video_items: Mapped[List["VideoItem"]] = relationship(
        secondary=video_genres, back_populates="genres"
    )


# ---------------------------------------------------------------------------
# ProcessingJob — background job tracking
# ---------------------------------------------------------------------------

class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Job identity
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)  # "import_url", "rescan", "normalize", "library_scan", "playlist_import"
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, index=True)

    # Display
    display_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    action_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # human-readable action e.g. "URL Import (AI Auto)"

    # Input
    input_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    input_params: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Pipeline step tracking — JSON list of {"step": "...", "status": "success"|"failed"|"skipped"}
    pipeline_steps: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Progress
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Logs (append-only text)
    log_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Error info
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc), nullable=True
    )

    video_item: Mapped[Optional["VideoItem"]] = relationship(back_populates="processing_jobs")


# ---------------------------------------------------------------------------
# Settings — global & per-user KV
# ---------------------------------------------------------------------------

class AppSetting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)  # None = global
    key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(String(20), default="string")  # string, int, float, bool, json

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_setting_user_key"),
    )


# ---------------------------------------------------------------------------
# NormalizationHistory — audit trail
# ---------------------------------------------------------------------------

class NormalizationHistory(Base):
    __tablename__ = "normalization_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_items.id", ondelete="CASCADE"), index=True)

    target_lufs: Mapped[float] = mapped_column(Float, nullable=False)
    measured_lufs_before: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    measured_lufs_after: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gain_applied_db: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    video_item: Mapped["VideoItem"] = relationship(back_populates="normalization_history")


# ---------------------------------------------------------------------------
# PlaybackHistory
# ---------------------------------------------------------------------------

class PlaybackHistory(Base):
    __tablename__ = "playback_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_items.id", ondelete="CASCADE"), index=True)

    played_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    duration_watched_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    video_item: Mapped["VideoItem"] = relationship(back_populates="playback_history")


# ---------------------------------------------------------------------------
# Playlist
# ---------------------------------------------------------------------------

class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    entries: Mapped[List["PlaylistEntry"]] = relationship(
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistEntry.position",
    )


class PlaylistEntry(Base):
    __tablename__ = "playlist_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    playlist_id: Mapped[int] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"), index=True
    )
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video_items.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    playlist: Mapped["Playlist"] = relationship(back_populates="entries")
    video_item: Mapped["VideoItem"] = relationship()


# ---------------------------------------------------------------------------
# Import metadata models so they share the same Base and are auto-created
# ---------------------------------------------------------------------------
from app.metadata.models import (  # noqa: E402, F401
    ArtistEntity, AlbumEntity, TrackEntity,
    CachedAsset, MetadataRevision, ExportManifest,
    artist_genres, album_genres, track_genres,
)

# ---------------------------------------------------------------------------
# Import matching models so they share the same Base and are auto-created
# ---------------------------------------------------------------------------
from app.matching.models import (  # noqa: E402, F401
    MatchResult, MatchCandidate,
    NormalizationResult as MatchNormalizationResult,
    UserPinnedMatch,
)

# ---------------------------------------------------------------------------
# Import AI models so they share the same Base and are auto-created
# ---------------------------------------------------------------------------
from app.ai.models import (  # noqa: E402, F401
    AIMetadataResult, AISceneAnalysis, AIThumbnail,
)
