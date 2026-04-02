"""Add proposed_sources to ai_metadata_results

Revision ID: 005
Revises: 004
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004_add_song_video_ratings"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ai_metadata_results") as batch_op:
        batch_op.add_column(sa.Column("proposed_sources", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("ai_metadata_results") as batch_op:
        batch_op.drop_column("proposed_sources")
