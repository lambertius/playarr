"""Canonical track identity system.

Extends TrackEntity to serve as the canonical track, adds AI verification
flags, artwork caching, cover track metadata, and genre associations.
Adds fingerprint/acoustid fields and new processing steps to VideoItem.
Adds origin and artist_image to ArtistEntity.

Revision ID: 001_canonical_track
Revises: None
Create Date: 2026-03-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

# revision identifiers
revision = "001_canonical_track"
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return name in sa_inspect(bind).get_table_names()


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = {c["name"] for c in sa_inspect(bind).get_columns(table)}
    return column in cols


def upgrade() -> None:
    # --- track_genres association table ---
    if not _table_exists("track_genres"):
        op.create_table(
            "track_genres",
            sa.Column("track_id", sa.Integer, sa.ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("genre_id", sa.Integer, sa.ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
        )

    # --- ArtistEntity new columns ---
    with op.batch_alter_table("artists") as batch_op:
        if not _column_exists("artists", "origin"):
            batch_op.add_column(sa.Column("origin", sa.String(500), nullable=True))
        if not _column_exists("artists", "artist_image"):
            batch_op.add_column(sa.Column("artist_image", sa.String(1000), nullable=True))

    # --- TrackEntity (canonical track) new columns ---
    with op.batch_alter_table("tracks") as batch_op:
        if not _column_exists("tracks", "year"):
            batch_op.add_column(sa.Column("year", sa.Integer, nullable=True))
        if not _column_exists("tracks", "mb_release_id"):
            batch_op.add_column(sa.Column("mb_release_id", sa.String(36), nullable=True))
        if not _column_exists("tracks", "mb_artist_id"):
            batch_op.add_column(sa.Column("mb_artist_id", sa.String(36), nullable=True))
        if not _column_exists("tracks", "artwork_album"):
            batch_op.add_column(sa.Column("artwork_album", sa.String(1000), nullable=True))
        if not _column_exists("tracks", "artwork_single"):
            batch_op.add_column(sa.Column("artwork_single", sa.String(1000), nullable=True))
        if not _column_exists("tracks", "canonical_verified"):
            batch_op.add_column(sa.Column("canonical_verified", sa.Boolean, server_default="0", nullable=False))
        if not _column_exists("tracks", "metadata_source"):
            batch_op.add_column(sa.Column("metadata_source", sa.String(100), nullable=True))
        if not _column_exists("tracks", "ai_verified"):
            batch_op.add_column(sa.Column("ai_verified", sa.Boolean, server_default="0", nullable=False))
        if not _column_exists("tracks", "ai_verified_at"):
            batch_op.add_column(sa.Column("ai_verified_at", sa.DateTime, nullable=True))
        if not _column_exists("tracks", "original_artist"):
            batch_op.add_column(sa.Column("original_artist", sa.String(500), nullable=True))
        if not _column_exists("tracks", "original_title"):
            batch_op.add_column(sa.Column("original_title", sa.String(500), nullable=True))
        if not _column_exists("tracks", "is_cover"):
            batch_op.add_column(sa.Column("is_cover", sa.Boolean, server_default="0", nullable=False))
        batch_op.create_index("ix_tracks_mb_release_id", ["mb_release_id"])
        batch_op.create_index("ix_tracks_mb_artist_id", ["mb_artist_id"])

    # --- VideoItem new columns ---
    with op.batch_alter_table("video_items") as batch_op:
        if not _column_exists("video_items", "audio_fingerprint"):
            batch_op.add_column(sa.Column("audio_fingerprint", sa.Text, nullable=True))
        if not _column_exists("video_items", "acoustid_id"):
            batch_op.add_column(sa.Column("acoustid_id", sa.String(36), nullable=True))
        batch_op.create_index("ix_video_items_acoustid_id", ["acoustid_id"])


def downgrade() -> None:
    with op.batch_alter_table("video_items") as batch_op:
        batch_op.drop_index("ix_video_items_acoustid_id")
        batch_op.drop_column("acoustid_id")
        batch_op.drop_column("audio_fingerprint")

    with op.batch_alter_table("tracks") as batch_op:
        batch_op.drop_index("ix_tracks_mb_artist_id")
        batch_op.drop_index("ix_tracks_mb_release_id")
        batch_op.drop_column("is_cover")
        batch_op.drop_column("original_title")
        batch_op.drop_column("original_artist")
        batch_op.drop_column("ai_verified_at")
        batch_op.drop_column("ai_verified")
        batch_op.drop_column("metadata_source")
        batch_op.drop_column("canonical_verified")
        batch_op.drop_column("artwork_single")
        batch_op.drop_column("artwork_album")
        batch_op.drop_column("mb_artist_id")
        batch_op.drop_column("mb_release_id")
        batch_op.drop_column("year")

    with op.batch_alter_table("artists") as batch_op:
        batch_op.drop_column("artist_image")
        batch_op.drop_column("origin")

    op.drop_table("track_genres")
