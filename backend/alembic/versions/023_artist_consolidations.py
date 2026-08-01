"""Portable artist consolidation aggregates.

Revision ID: 023_artist_consolidations
Revises: 022_editor_queue_state
"""
from alembic import op
import sqlalchemy as sa


revision = "023_artist_consolidations"
down_revision = "022_editor_queue_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artist_consolidations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("stable_id", sa.String(36), nullable=False, unique=True),
        sa.Column("mask_name", sa.String(500), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_artist_consolidations_stable_id", "artist_consolidations", ["stable_id"], unique=True)
    op.create_index("ix_artist_consolidations_mask_name", "artist_consolidations", ["mask_name"])
    op.create_table(
        "artist_consolidation_targets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("consolidation_id", sa.Integer(), sa.ForeignKey("artist_consolidations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_name", sa.String(500), nullable=False),
        sa.Column("provenance", sa.String(100), nullable=True),
        sa.Column("mb_artist_id", sa.String(36), nullable=True),
        sa.UniqueConstraint("consolidation_id", "raw_name", name="uq_artist_consolidation_target"),
    )
    op.create_index("ix_artist_consolidation_targets_consolidation_id", "artist_consolidation_targets", ["consolidation_id"])
    op.create_index("ix_artist_consolidation_targets_raw_name", "artist_consolidation_targets", ["raw_name"])
    op.create_index("ix_artist_consolidation_targets_mb_artist_id", "artist_consolidation_targets", ["mb_artist_id"])
    op.create_table(
        "artist_consolidation_mbids",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("consolidation_id", sa.Integer(), sa.ForeignKey("artist_consolidations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mb_artist_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("consolidation_id", "mb_artist_id", name="uq_artist_consolidation_mbid"),
    )
    op.create_index("ix_artist_consolidation_mbids_consolidation_id", "artist_consolidation_mbids", ["consolidation_id"])
    op.create_index("ix_artist_consolidation_mbids_mb_artist_id", "artist_consolidation_mbids", ["mb_artist_id"])


def downgrade() -> None:
    op.drop_table("artist_consolidation_mbids")
    op.drop_table("artist_consolidation_targets")
    op.drop_table("artist_consolidations")
