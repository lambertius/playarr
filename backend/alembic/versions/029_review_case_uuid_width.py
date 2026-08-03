"""Align review evidence references with 36-character video UUIDs.

Revision ID: 029_review_uuid_width
Revises: 028_archive_catalog
"""
from alembic import op
import sqlalchemy as sa


revision = "029_review_uuid_width"
down_revision = "028_archive_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("review_case_items") as batch:
        batch.alter_column(
            "video_stable_id",
            existing_type=sa.String(32),
            type_=sa.String(36),
            existing_nullable=False,
        )
    with op.batch_alter_table("review_case_edges") as batch:
        batch.alter_column(
            "left_video_stable_id",
            existing_type=sa.String(32),
            type_=sa.String(36),
            existing_nullable=False,
        )
        batch.alter_column(
            "right_video_stable_id",
            existing_type=sa.String(32),
            type_=sa.String(36),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("review_case_edges") as batch:
        batch.alter_column(
            "right_video_stable_id",
            existing_type=sa.String(36),
            type_=sa.String(32),
            existing_nullable=False,
        )
        batch.alter_column(
            "left_video_stable_id",
            existing_type=sa.String(36),
            type_=sa.String(32),
            existing_nullable=False,
        )
    with op.batch_alter_table("review_case_items") as batch:
        batch.alter_column(
            "video_stable_id",
            existing_type=sa.String(36),
            type_=sa.String(32),
            existing_nullable=False,
        )
