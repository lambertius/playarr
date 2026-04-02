"""
add_platform_metadata_to_sources

Revision ID: deb7429cbaaa
Revises: 001_canonical_track
Create Date: 2026-03-07 09:42:59.307495
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'deb7429cbaaa'
down_revision: Union[str, None] = '001_canonical_track'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sources', sa.Column('channel_name', sa.String(length=500), nullable=True))
    op.add_column('sources', sa.Column('platform_title', sa.String(length=1000), nullable=True))
    op.add_column('sources', sa.Column('platform_description', sa.Text(), nullable=True))
    op.add_column('sources', sa.Column('platform_tags', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('sources', 'platform_tags')
    op.drop_column('sources', 'platform_description')
    op.drop_column('sources', 'platform_title')
    op.drop_column('sources', 'channel_name')
