"""Durable operations, sidecar outbox and aggregate revisions.

Revision ID: 019_durable_operations
Revises: 018_provenance_robustness
"""
from alembic import op
import sqlalchemy as sa


revision = "019_durable_operations"
down_revision = "018_provenance_robustness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_items") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("sidecar_revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("settings") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("playlists") as batch:
        batch.add_column(sa.Column("stable_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("playlist_entries") as batch:
        batch.add_column(sa.Column("occurrence_id", sa.String(36), nullable=True))

    # SQLite generates collision-resistant IDs without relying on a Python
    # migration environment or existing row numbering.
    op.execute("UPDATE playlists SET stable_id = lower(hex(randomblob(16))) WHERE stable_id IS NULL")
    op.execute("UPDATE playlist_entries SET occurrence_id = lower(hex(randomblob(16))) WHERE occurrence_id IS NULL")
    with op.batch_alter_table("playlists") as batch:
        batch.alter_column("stable_id", nullable=False)
        batch.create_index("ix_playlists_stable_id", ["stable_id"], unique=True)
    with op.batch_alter_table("playlist_entries") as batch:
        batch.alter_column("occurrence_id", nullable=False)
        batch.create_index("ix_playlist_entries_occurrence_id", ["occurrence_id"], unique=True)

    op.create_table(
        "mutation_commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("command_type", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_stable_id", sa.String(200), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("actor_id", sa.String(200), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_mutation_commands_idempotency_key", "mutation_commands", ["idempotency_key"], unique=True)
    op.create_index("ix_mutation_commands_command_type", "mutation_commands", ["command_type"])
    op.create_index("ix_mutation_commands_entity_type", "mutation_commands", ["entity_type"])
    op.create_index("ix_mutation_commands_entity_stable_id", "mutation_commands", ["entity_stable_id"])
    op.create_index("ix_mutation_commands_priority", "mutation_commands", ["priority"])
    op.create_index("ix_mutation_commands_status", "mutation_commands", ["status"])
    op.create_index("ix_mutation_commands_created_at", "mutation_commands", ["created_at"])
    op.create_index("ix_mutation_pending_priority", "mutation_commands", ["status", "priority", "created_at"])

    op.create_table(
        "sidecar_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("operation_id", sa.String(36), sa.ForeignKey("mutation_commands.id", ondelete="SET NULL")),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("video_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_stable_id", sa.String(200), nullable=False),
        sa.Column("target_path", sa.String(1200), nullable=True),
        sa.Column("entity_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(80), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("video_id", "entity_revision", name="uq_sidecar_outbox_video_revision"),
    )
    for column in ("operation_id", "video_id", "entity_stable_id", "status", "created_at"):
        op.create_index(f"ix_sidecar_outbox_{column}", "sidecar_outbox", [column])

    op.create_table(
        "file_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("command_id", sa.String(36), sa.ForeignKey("mutation_commands.id", ondelete="SET NULL")),
        sa.Column("entity_stable_id", sa.String(200), nullable=False),
        sa.Column("operation_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="planned"),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("rollback_json", sa.JSON(), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    for column in ("command_id", "entity_stable_id", "operation_type", "status", "created_at"):
        op.create_index(f"ix_file_operations_{column}", "file_operations", [column])

    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("processing_jobs.id", ondelete="CASCADE")),
        sa.Column("operation_id", sa.String(36), nullable=True),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_hash", sa.String(80), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    for column in ("job_id", "operation_id", "stage", "state", "created_at"):
        op.create_index(f"ix_job_events_{column}", "job_events", [column])


def downgrade() -> None:
    op.drop_table("job_events")
    op.drop_table("file_operations")
    op.drop_table("sidecar_outbox")
    op.drop_table("mutation_commands")
    with op.batch_alter_table("playlist_entries") as batch:
        batch.drop_index("ix_playlist_entries_occurrence_id")
        batch.drop_column("occurrence_id")
    with op.batch_alter_table("playlists") as batch:
        batch.drop_index("ix_playlists_stable_id")
        batch.drop_column("revision")
        batch.drop_column("stable_id")
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("revision")
    with op.batch_alter_table("video_items") as batch:
        batch.drop_column("sidecar_revision")
        batch.drop_column("revision")
