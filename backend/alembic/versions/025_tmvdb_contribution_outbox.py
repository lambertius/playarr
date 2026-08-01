"""Durable TMVDB contribution outbox.

Revision ID: 025_tmvdb_outbox
Revises: 024_operation_correlation
"""
from alembic import op
import sqlalchemy as sa


revision = "025_tmvdb_outbox"
down_revision = "024_operation_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contribution_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("operation_id", sa.String(80), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("envelope_json", sa.JSON(), nullable=False),
        sa.Column("eligibility_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("remote_id", sa.String(100), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_contribution_outbox_video_id", "contribution_outbox", ["video_id"])
    op.create_index("ix_contribution_outbox_operation_id", "contribution_outbox", ["operation_id"], unique=True)
    op.create_index("ix_contribution_outbox_request_id", "contribution_outbox", ["request_id"])
    op.create_index("ix_contribution_outbox_idempotency_key", "contribution_outbox", ["idempotency_key"], unique=True)
    op.create_index("ix_contribution_outbox_payload_hash", "contribution_outbox", ["payload_hash"])
    op.create_index("ix_contribution_outbox_status", "contribution_outbox", ["status"])
    op.create_index("ix_contribution_outbox_created_at", "contribution_outbox", ["created_at"])


def downgrade() -> None:
    op.drop_table("contribution_outbox")
