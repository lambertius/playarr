"""Durable review cases, pair evidence and staged action plans.

Revision ID: 020_review_cases
Revises: 019_durable_operations
"""
from alembic import op
import sqlalchemy as sa


revision = "020_review_cases"
down_revision = "019_durable_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_items") as batch:
        batch.add_column(sa.Column("stable_id", sa.String(36), nullable=True))
    op.execute("UPDATE video_items SET stable_id = lower(hex(randomblob(16))) WHERE stable_id IS NULL")
    with op.batch_alter_table("video_items") as batch:
        batch.alter_column("stable_id", nullable=False)
        batch.create_index("ix_video_items_stable_id", ["stable_id"], unique=True)

    op.create_table(
        "review_cases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("stable_id", sa.String(36), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("trigger_code", sa.String(100), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("dismissed_evidence_hash", sa.String(64), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_review_cases_stable_id", "review_cases", ["stable_id"], unique=True)
    for column in ("category", "status", "evidence_hash", "created_at"):
        op.create_index(f"ix_review_cases_{column}", "review_cases", [column])

    op.create_table(
        "review_case_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("review_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("video_stable_id", sa.String(32), nullable=False),
        sa.Column("role", sa.String(30), nullable=False, server_default="candidate"),
        sa.Column("evidence_summary_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("case_id", "video_stable_id", name="uq_review_case_item_video"),
    )
    for column in ("case_id", "video_id", "video_stable_id"):
        op.create_index(f"ix_review_case_items_{column}", "review_case_items", [column])

    op.create_table(
        "review_case_edges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("review_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("left_video_stable_id", sa.String(32), nullable=False),
        sa.Column("right_video_stable_id", sa.String(32), nullable=False),
        sa.Column("evidence_type", sa.String(60), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.UniqueConstraint(
            "case_id", "left_video_stable_id", "right_video_stable_id",
            name="uq_review_case_edge_pair",
        ),
    )
    for column in ("case_id", "left_video_stable_id", "right_video_stable_id", "evidence_hash"):
        op.create_index(f"ix_review_case_edges_{column}", "review_case_edges", [column])

    op.create_table(
        "review_action_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("review_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("actions_json", sa.JSON(), nullable=False),
        sa.Column("consequence_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_review_action_plans_case_id", "review_action_plans", ["case_id"])
    op.create_index("ix_review_action_plans_status", "review_action_plans", ["status"])


def downgrade() -> None:
    op.drop_table("review_action_plans")
    op.drop_table("review_case_edges")
    op.drop_table("review_case_items")
    op.drop_table("review_cases")
    with op.batch_alter_table("video_items") as batch:
        batch.drop_index("ix_video_items_stable_id")
        batch.drop_column("stable_id")
