"""Add asset validity and provenance metadata to CachedAsset and MediaAsset.

Adds status, content_type, source_provider, resolved_url, validation_error,
last_validated_at, and file_hash columns to both tables. Makes artwork
assets self-describing so validation, repair, and provenance tracking work.

Revision ID: 002_asset_validity
Revises: 001_canonical_track
Create Date: 2026-03-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "002_asset_validity"
down_revision = "c298cf6db234"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = {c["name"] for c in sa_inspect(bind).get_columns(table)}
    return column in cols


def upgrade() -> None:
    # ---- CachedAsset additions ----
    _add = [
        ("cached_assets", "status", sa.String(20), "valid"),
        ("cached_assets", "content_type", sa.String(100), None),
        ("cached_assets", "source_provider", sa.String(100), None),
        ("cached_assets", "resolved_url", sa.String(2000), None),
        ("cached_assets", "validation_error", sa.Text(), None),
        ("cached_assets", "last_validated_at", sa.DateTime(), None),
        ("cached_assets", "file_hash", sa.String(64), None),
    ]
    for table, col, col_type, default in _add:
        if not _column_exists(table, col):
            kw = {}
            if default is not None:
                kw["server_default"] = default
            op.add_column(table, sa.Column(col, col_type, nullable=True, **kw))

    # ---- MediaAsset additions ----
    _add_media = [
        ("media_assets", "status", sa.String(20), "valid"),
        ("media_assets", "content_type", sa.String(100), None),
        ("media_assets", "source_provider", sa.String(100), None),
        ("media_assets", "resolved_url", sa.String(2000), None),
        ("media_assets", "validation_error", sa.Text(), None),
        ("media_assets", "last_validated_at", sa.DateTime(), None),
        ("media_assets", "file_hash", sa.String(64), None),
        ("media_assets", "width", sa.Integer(), None),
        ("media_assets", "height", sa.Integer(), None),
        ("media_assets", "file_size_bytes", sa.Integer(), None),
    ]
    for table, col, col_type, default in _add_media:
        if not _column_exists(table, col):
            kw = {}
            if default is not None:
                kw["server_default"] = default
            op.add_column(table, sa.Column(col, col_type, nullable=True, **kw))


def downgrade() -> None:
    for col in ["status", "content_type", "source_provider", "resolved_url",
                "validation_error", "last_validated_at", "file_hash"]:
        try:
            op.drop_column("cached_assets", col)
        except Exception:
            pass

    for col in ["status", "content_type", "source_provider", "resolved_url",
                "validation_error", "last_validated_at", "file_hash",
                "width", "height", "file_size_bytes"]:
        try:
            op.drop_column("media_assets", col)
        except Exception:
            pass
