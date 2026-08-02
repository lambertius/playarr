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
from uuid import uuid4

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
    finalizing = "finalizing"
    cancelling = "cancelling"
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
    stable_id: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid4()), nullable=False, unique=True, index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    sidecar_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)

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
    mb_track_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Multi-artist support — JSON list of {name, mb_artist_id} for featured/collaborating artists
    artist_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

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

    # Version detection — cover / live / alternate / remix / acoustic / normal
    version_type: Mapped[str] = mapped_column(
        String(20), default="normal", server_default="normal", nullable=False, index=True,
    )
    alternate_version_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    original_artist: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    original_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    related_versions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # [{video_id, label}]

    # Hierarchical version relationship — link to parent video
    # e.g. a remix of a cover links to the cover, which links to the original
    parent_video_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    # Canonical track linking provenance + confidence
    canonical_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0.0–1.0
    canonical_provenance: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # musicbrainz/fingerprint/import/ai/user

    # Review routing
    review_status: Mapped[str] = mapped_column(
        String(30), default="none", server_default="none", nullable=False, index=True,
    )
    review_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Structured category: version_detection, duplicate, import_error, url_import_error, manual_review
    review_category: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)

    # Review history — tracks past review dismiss/clear actions so items don't get re-flagged.
    # JSON list: [{"action": "dismissed", "category": "scanned", "reason": "...", "timestamp": "..."}]
    review_history: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Dismissed duplicate video IDs — IDs explicitly confirmed as "not a duplicate".
    # JSON list: [93, 142]  Checked by duplicate_scan_task to skip known non-duplicates.
    dismissed_duplicate_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Rename dismissed — user has reviewed and accepted the current filename.
    # Prevents re-flagging on automatic rename scans.
    rename_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)

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

    # Playarr content IDs — deterministic hashes for cross-quality / cross-version matching
    # playarr_video_id: same visual content regardless of quality/resolution/crops.
    #   Alternate versions and 18+ edits get different video IDs.
    # playarr_track_id: same musical composition/performance regardless of video.
    #   Covers and live versions get different track IDs; alternate/18+ share the same one.
    playarr_video_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    playarr_track_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)

    # Perceptual hash of a representative video frame — used to distinguish
    # visually different music videos for the same song (e.g. two official videos).
    video_phash: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # Field-level provenance — tracks which provider sourced each metadata field
    # JSON dict: {"artist": "musicbrainz", "plot": "wikipedia", "album": "tmvdb", ...}
    field_provenance: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Field-level user attribution — tracks which user last set each field
    # JSON dict: {"artist": "abc123", "plot": "abc123", ...}
    # Only populated for fields set by a human editor (not automated sources)
    field_provenance_users: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Field-level timestamps — when each field was last set (ISO-8601 UTC).
    # JSON dict: {"artist": "2026-06-20T...", "plot": "2026-06-20T...", ...}
    # Populated for both automated and human edits so contributions carry recency.
    field_provenance_at: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Field-level human verification — a human *confirmed* an existing value
    # without changing it (a strong trust signal, distinct from editing).
    # JSON dict: {"artist": {"by": "abc123", "at": "2026-06-20T...", "from": "musicbrainz"}}
    field_verifications: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Last user who manually edited this video's metadata
    last_edited_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Rating provenance — who set each rating and when (for cross-instance weighting)
    song_rating_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    song_rating_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    video_rating_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    video_rating_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Fast content signature — SHA-256 of sampled file chunks + size.
    # Cheap, deterministic exact-content key for integrity / cross-instance dedup.
    file_checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # Video editor — exclude from future letterbox scans (false positive suppression)
    exclude_from_editor_scan: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False,
    )
    editor_crop_dismissed_evidence_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Video editor — what type of edit has been applied: 'crop', 'trim', 'both', or None
    editor_edit_type: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True, default=None,
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

    # Hierarchical parent/child version relationships
    parent_video = relationship(
        "VideoItem", remote_side="VideoItem.id",
        foreign_keys=[parent_video_id],
    )

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


def clear_stale_enrichment_review(video: "VideoItem", db=None) -> bool:
    """Clear an ai_partial/ai_pending/scanned/missing_artwork review flag if the underlying steps are now complete.

    Returns True if the flag was cleared.  Caller must commit.
    """
    if video.review_status != "needs_human_review":
        return False
    rc = video.review_category
    ps = video.processing_state or {}
    _ok = lambda s: ps.get(s, {}).get("completed", False)
    rr = video.review_reason or ""
    _clear = False

    if rc in ("ai_partial", "ai_pending"):
        need_ai = "AI metadata" in rr
        need_scenes = "scene analysis" in rr
        _clear = (not need_ai or _ok("ai_enriched")) and (not need_scenes or _ok("scenes_analyzed"))
    elif rc == "scanned":
        _clear = _ok("metadata_scraped") or _ok("metadata_resolved")
    elif rc == "import_error":
        # import_error review items are now redundant (shown in queue skipped tab)
        _clear = True
    elif rc in ("missing_artwork", "artwork_incomplete") and db is not None:
        from app.ai.models import AIThumbnail
        has_poster = db.query(MediaAsset.id).filter(
            MediaAsset.video_id == video.id,
            MediaAsset.asset_type == "poster",
            MediaAsset.status == "valid",
        ).first() is not None
        has_thumb = db.query(AIThumbnail.id).filter(
            AIThumbnail.video_id == video.id,
            AIThumbnail.is_selected == True,  # noqa: E712
        ).first() is not None
        # Auto-select best thumbnail if thumbnails exist but none is selected
        if not has_thumb:
            best = (
                db.query(AIThumbnail)
                .filter(AIThumbnail.video_id == video.id)
                .order_by(AIThumbnail.score_overall.desc())
                .first()
            )
            if best:
                best.is_selected = True
                has_thumb = True
        # Check only what was actually flagged as missing
        needs_poster = "poster" in rr
        needs_thumb = "thumbnail" in rr
        if needs_poster and needs_thumb:
            _clear = has_poster and has_thumb
        elif needs_poster:
            _clear = has_poster
        elif needs_thumb:
            _clear = has_thumb
        else:
            # Fallback: require both
            _clear = has_poster and has_thumb

    if _clear:
        video.review_status = "none"
        video.review_reason = None
        video.review_category = None
        return True
    return False


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

    # Letterbox scan results — persisted so rescans don't re-analyze every file
    letterbox_scanned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    letterbox_detected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    letterbox_crop_w: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    letterbox_crop_h: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    letterbox_crop_x: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    letterbox_crop_y: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    letterbox_bar_top: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    letterbox_bar_bottom: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    letterbox_bar_left: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    letterbox_bar_right: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    letterbox_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    letterbox_sample_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    letterbox_samples_expected: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    letterbox_review_suggested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    letterbox_instability_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    letterbox_evidence_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    letterbox_evidence_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    letterbox_source_checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

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

    # Anonymous user ID that triggered this snapshot (NULL for system/automated actions)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    video_item: Mapped["VideoItem"] = relationship(back_populates="metadata_snapshots")


# ---------------------------------------------------------------------------
# ContributionLog — outbound record of metadata shared with external DBs (TMVDB)
# ---------------------------------------------------------------------------

class ContributionLog(Base):
    """Audit trail of metadata contributions pushed to an external database.

    Enables idempotency (skip re-pushing unchanged data), a trust feedback
    loop (record what the remote accepted / what id it assigned), and dedup.
    """
    __tablename__ = "contribution_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Nullable + SET NULL so the log survives deletion of the source video.
    video_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    # Anonymous instance identity that made the contribution (the trust anchor).
    instance_user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    target: Mapped[str] = mapped_column(String(40), default="tmvdb", nullable=False)  # external DB name
    operation: Mapped[str] = mapped_column(String(20), nullable=False)  # push | push_bulk

    # Stable identity keys captured at push time (survive video deletion).
    playarr_track_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    playarr_video_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)

    # SHA-256 of the canonical contribution payload — used for idempotency.
    payload_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False)  # submitted | failed | skipped
    remote_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # id assigned by remote
    response: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # raw remote response / error

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True,
    )


class ContributionOutbox(Base):
    """Durable, idempotent TMVDB submission snapshot."""
    __tablename__ = "contribution_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    video_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    operation_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    envelope_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    eligibility_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    remote_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    response_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


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
    crop_position: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # CSS object-position e.g. "50% 30%"

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
    master_genre_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("genres.id", ondelete="SET NULL"), nullable=True
    )

    master_genre: Mapped[Optional["Genre"]] = relationship(
        "Genre", remote_side="Genre.id", foreign_keys=[master_genre_id],
    )

    video_items: Mapped[List["VideoItem"]] = relationship(
        secondary=video_genres, back_populates="genres"
    )


# ---------------------------------------------------------------------------
# ProcessingJob — background job tracking
# ---------------------------------------------------------------------------

class VideoEditorQueueEntry(Base):
    """Durable editor draft entry shared by every browser/device."""
    __tablename__ = "video_editor_queue_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    occurrence_id: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid4()), unique=True, nullable=False, index=True,
    )
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video_items.id", ondelete="CASCADE"), unique=True, nullable=False, index=True,
    )
    source: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    settings_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc), nullable=False,
    )


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Job identity
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True, index=True,
        default=lambda: __import__(
            "app.services.request_context", fromlist=["current_request_id"]
        ).current_request_id(),
    )
    operation_id: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True, index=True,
        default=lambda: __import__(
            "app.services.request_context", fromlist=["new_operation_id"]
        ).new_operation_id(),
    )
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

    @property
    def status_group(self) -> str:
        from app.services.job_registry import status_group
        return status_group(self.status)

    @property
    def job_category(self) -> str:
        from app.services.job_registry import job_category
        return job_category(self.job_type)

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
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)

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
    stable_id: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid4()), nullable=False, unique=True, index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
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
    occurrence_id: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid4()), nullable=False, unique=True, index=True,
    )
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
# Durable mutation, sidecar and filesystem operation boundary
# ---------------------------------------------------------------------------

class MutationCommand(Base):
    """Idempotent command accepted by an HTTP endpoint or background worker."""

    __tablename__ = "mutation_commands"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()),
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    command_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_stable_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    expected_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True, index=True,
        default=lambda: __import__(
            "app.services.request_context", fromlist=["current_request_id"]
        ).current_request_id(),
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50, server_default="50", index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    result_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_mutation_pending_priority", "status", "priority", "created_at"),
    )


class SidecarOutbox(Base):
    """Durable request to reconcile one database entity to its sidecar."""

    __tablename__ = "sidecar_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    operation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("mutation_commands.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video_items.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    entity_stable_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    target_path: Mapped[Optional[str]] = mapped_column(String(1200), nullable=True)
    entity_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    content_hash: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("video_id", "entity_revision", name="uq_sidecar_outbox_video_revision"),
    )


class FileOperation(Base):
    """Recoverable journal for media and companion-file transitions."""

    __tablename__ = "file_operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    command_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("mutation_commands.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    entity_stable_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned", server_default="planned", index=True)
    expected_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rollback_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class JobEvent(Base):
    """Structured stage event used for diagnostics and resumability."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    operation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    input_hash: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    output_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


# ---------------------------------------------------------------------------
# Durable review cases and staged decisions
# ---------------------------------------------------------------------------

class ReviewCase(Base):
    __tablename__ = "review_cases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stable_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open", index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    trigger_code: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dismissed_evidence_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    items: Mapped[List["ReviewCaseItem"]] = relationship(
        back_populates="review_case", cascade="all, delete-orphan",
    )
    edges: Mapped[List["ReviewCaseEdge"]] = relationship(
        back_populates="review_case", cascade="all, delete-orphan",
    )
    plans: Mapped[List["ReviewActionPlan"]] = relationship(
        back_populates="review_case", cascade="all, delete-orphan",
    )


class ReviewCaseItem(Base):
    __tablename__ = "review_case_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("review_cases.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    video_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    video_stable_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="candidate")
    evidence_summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    review_case: Mapped["ReviewCase"] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("case_id", "video_stable_id", name="uq_review_case_item_video"),
    )


class ReviewCaseEdge(Base):
    __tablename__ = "review_case_edges"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("review_cases.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    left_video_stable_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    right_video_stable_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(60), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open")

    review_case: Mapped["ReviewCase"] = relationship(back_populates="edges")

    __table_args__ = (
        UniqueConstraint(
            "case_id", "left_video_stable_id", "right_video_stable_id",
            name="uq_review_case_edge_pair",
        ),
    )


class ReviewActionPlan(Base):
    __tablename__ = "review_action_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[int] = mapped_column(
        ForeignKey("review_cases.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actions_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    consequence_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    committed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    review_case: Mapped["ReviewCase"] = relationship(back_populates="plans")


class ArtistConsolidation(Base):
    """Display mask over raw artist target names and zero-or-more MBIDs."""
    __tablename__ = "artist_consolidations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stable_id: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()), unique=True, nullable=False, index=True)
    mask_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc), nullable=False,
    )
    targets: Mapped[List["ArtistConsolidationTarget"]] = relationship(
        back_populates="consolidation", cascade="all, delete-orphan",
    )
    mbids: Mapped[List["ArtistConsolidationMbid"]] = relationship(
        back_populates="consolidation", cascade="all, delete-orphan",
    )


class ArtistConsolidationTarget(Base):
    __tablename__ = "artist_consolidation_targets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    consolidation_id: Mapped[int] = mapped_column(
        ForeignKey("artist_consolidations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    raw_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    provenance: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    provenance_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    mb_artist_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    consolidation: Mapped["ArtistConsolidation"] = relationship(back_populates="targets")

    __table_args__ = (UniqueConstraint("consolidation_id", "raw_name", name="uq_artist_consolidation_target"),)


class ArtistConsolidationMbid(Base):
    __tablename__ = "artist_consolidation_mbids"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    consolidation_id: Mapped[int] = mapped_column(
        ForeignKey("artist_consolidations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    mb_artist_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    consolidation: Mapped["ArtistConsolidation"] = relationship(back_populates="mbids")

    __table_args__ = (UniqueConstraint("consolidation_id", "mb_artist_id", name="uq_artist_consolidation_mbid"),)


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

from app.durability_models import (  # noqa: E402, F401
    FieldProvenanceEvent, GenreConsolidation, GenreConsolidationMember, ArchiveCatalogEntry,
)
