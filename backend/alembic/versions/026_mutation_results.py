"""Store successful mutation results separately from errors.

Revision ID: 026_mutation_results
Revises: 025_tmvdb_contribution_outbox
"""
from alembic import op
import sqlalchemy as sa


revision = "026_mutation_results"
down_revision = "025_tmvdb_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mutation_commands") as batch:
        batch.add_column(sa.Column("result_json", sa.JSON(), nullable=True))
    op.execute(
        "UPDATE mutation_commands SET result_json = json_extract(error_json, '$.result'), "
        "error_json = NULL WHERE status = 'succeeded' AND json_type(error_json, '$.result') IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("mutation_commands") as batch:
        batch.drop_column("result_json")
