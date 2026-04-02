"""Add mb_release_group_id columns to tracks, video_items, and albums.

Revision ID: 007_mb_release_group_id
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '007_mb_release_group_id'
down_revision = '006_source_unique_per_video'  # depends on last migration in the chain
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add mb_release_group_id to tracks table
    op.add_column('tracks', sa.Column('mb_release_group_id', sa.String(36), nullable=True))
    
    # Add mb_release_group_id to video_items table
    op.add_column('video_items', sa.Column('mb_release_group_id', sa.String(36), nullable=True))
    
    # Add mb_release_group_id to albums table
    op.add_column('albums', sa.Column('mb_release_group_id', sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column('albums', 'mb_release_group_id')
    op.drop_column('video_items', 'mb_release_group_id')
    op.drop_column('tracks', 'mb_release_group_id')
