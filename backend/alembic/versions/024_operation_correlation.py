"""End-to-end request and operation correlation.

Revision ID: 024_operation_correlation
Revises: 023_artist_consolidations
"""
from alembic import op
import sqlalchemy as sa


revision = "024_operation_correlation"
down_revision = "023_artist_consolidations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(sa.Column("request_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("operation_id", sa.String(80), nullable=True))
        batch.create_index("ix_processing_jobs_request_id", ["request_id"])
        batch.create_index("ix_processing_jobs_operation_id", ["operation_id"], unique=True)
    # Stable operation identifiers for legacy jobs keep historical rows
    # inspectable without pretending they came from an HTTP request.
    op.execute(
        "UPDATE processing_jobs SET operation_id = 'legacy_job_' || id "
        "WHERE operation_id IS NULL"
    )
    with op.batch_alter_table("processing_jobs") as batch:
        batch.alter_column("operation_id", nullable=False)

    with op.batch_alter_table("mutation_commands") as batch:
        batch.add_column(sa.Column("request_id", sa.String(80), nullable=True))
        batch.create_index("ix_mutation_commands_request_id", ["request_id"])


def downgrade() -> None:
    with op.batch_alter_table("mutation_commands") as batch:
        batch.drop_index("ix_mutation_commands_request_id")
        batch.drop_column("request_id")
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_index("ix_processing_jobs_operation_id")
        batch.drop_index("ix_processing_jobs_request_id")
        batch.drop_column("operation_id")
        batch.drop_column("request_id")
