"""
add source_type to sources

Revision ID: 003_add_source_type
Revises: c298cf6db234
Create Date: 2026-03-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '003_add_source_type'
down_revision: Union[str, None] = '002_asset_validity'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the source_type column
    with op.batch_alter_table("sources") as batch_op:
        batch_op.add_column(sa.Column("source_type", sa.String(20), nullable=True))

    # Backfill existing sources
    conn = op.get_bind()
    # YouTube/Vimeo → "video"
    conn.execute(sa.text(
        "UPDATE sources SET source_type = 'video' WHERE provider IN ('youtube', 'vimeo') AND source_type IS NULL"
    ))
    # MusicBrainz recording → "single"
    conn.execute(sa.text(
        "UPDATE sources SET source_type = 'single' WHERE provider = 'musicbrainz' AND source_type IS NULL"
    ))
    # Wikipedia → "single" (existing wiki sources are song pages)
    conn.execute(sa.text(
        "UPDATE sources SET source_type = 'single' WHERE provider = 'wikipedia' AND source_type IS NULL"
    ))
    # IMDB → "video"
    conn.execute(sa.text(
        "UPDATE sources SET source_type = 'video' WHERE provider = 'imdb' AND source_type IS NULL"
    ))


def downgrade() -> None:
    with op.batch_alter_table("sources") as batch_op:
        batch_op.drop_column("source_type")
