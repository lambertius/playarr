"""Add SQL-indexed archive catalogue.

Revision ID: 028_archive_catalog
Revises: 027_genre_aggregate
"""
from alembic import op
import sqlalchemy as sa

revision = "028_archive_catalog"
down_revision = "027_genre_aggregate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "archive_catalog_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("folder", sa.String(2000), nullable=False, unique=True),
        sa.Column("path", sa.String(2000), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("artist", sa.String(500), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("video_stable_id", sa.String(64), nullable=True),
        sa.Column("operation_id", sa.String(80), nullable=True),
        sa.Column("original_path", sa.String(2000), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum_md5", sa.String(32), nullable=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("manifest_schema_version", sa.Integer(), nullable=True),
        sa.Column("restore_eligible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("integrity_status", sa.String(40), nullable=False, server_default="unchecked"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
    )
    for column in ("reason", "artist", "title", "video_id", "video_stable_id", "operation_id", "archived_at", "integrity_status", "last_seen_at"):
        op.create_index(f"ix_archive_catalog_entries_{column}", "archive_catalog_entries", [column])


def downgrade() -> None:
    op.drop_table("archive_catalog_entries")
