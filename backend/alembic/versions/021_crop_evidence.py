"""Confidence-bearing crop evidence and false-positive memory.

Revision ID: 021_crop_evidence
Revises: 020_review_cases
"""
from alembic import op
import sqlalchemy as sa


revision = "021_crop_evidence"
down_revision = "020_review_cases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_items") as batch:
        batch.add_column(sa.Column("editor_crop_dismissed_evidence_hash", sa.String(64), nullable=True))
    with op.batch_alter_table("quality_signatures") as batch:
        batch.add_column(sa.Column("letterbox_confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("letterbox_sample_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("letterbox_samples_expected", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("letterbox_review_suggested", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("letterbox_instability_reason", sa.String(100), nullable=True))
        batch.add_column(sa.Column("letterbox_evidence_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("letterbox_evidence_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("letterbox_source_checksum", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quality_signatures") as batch:
        for column in (
            "letterbox_source_checksum", "letterbox_evidence_hash", "letterbox_evidence_json",
            "letterbox_instability_reason", "letterbox_review_suggested",
            "letterbox_samples_expected", "letterbox_sample_count", "letterbox_confidence",
        ):
            batch.drop_column(column)
    with op.batch_alter_table("video_items") as batch:
        batch.drop_column("editor_crop_dismissed_evidence_hash")
