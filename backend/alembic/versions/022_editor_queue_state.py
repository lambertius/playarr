"""Durable cross-device video editor queue.

Revision ID: 022_editor_queue_state
Revises: 021_crop_evidence
"""
from alembic import op
import sqlalchemy as sa


revision = "022_editor_queue_state"
down_revision = "021_crop_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_editor_queue_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("occurrence_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("video_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("settings_json", sa.JSON(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("occurrence_id"),
        sa.UniqueConstraint("video_id"),
    )
    op.create_index("ix_video_editor_queue_entries_occurrence_id", "video_editor_queue_entries", ["occurrence_id"])
    op.create_index("ix_video_editor_queue_entries_video_id", "video_editor_queue_entries", ["video_id"])
    op.create_index("ix_video_editor_queue_entries_position", "video_editor_queue_entries", ["position"])


def downgrade() -> None:
    op.drop_table("video_editor_queue_entries")
