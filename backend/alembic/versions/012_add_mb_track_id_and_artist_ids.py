"""
Add mb_track_id to video_items and tracks, artist_ids JSON to video_items,
acoustid_id and audio_fingerprint to tracks.

Revision ID: 012_mb_track_artist_ids
Revises: af2bfb4db65d
Create Date: 2025-07-14
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '012_mb_track_artist_ids'
down_revision: Union[str, None] = 'af2bfb4db65d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # VideoItem additions
    with op.batch_alter_table('video_items') as batch_op:
        batch_op.add_column(sa.Column('mb_track_id', sa.String(36), nullable=True))
        batch_op.add_column(sa.Column('artist_ids', sa.JSON(), nullable=True))

    # TrackEntity additions
    with op.batch_alter_table('tracks') as batch_op:
        batch_op.add_column(sa.Column('mb_track_id', sa.String(36), nullable=True))
        batch_op.add_column(sa.Column('acoustid_id', sa.String(36), nullable=True))
        batch_op.add_column(sa.Column('audio_fingerprint', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tracks') as batch_op:
        batch_op.drop_column('audio_fingerprint')
        batch_op.drop_column('acoustid_id')
        batch_op.drop_column('mb_track_id')

    with op.batch_alter_table('video_items') as batch_op:
        batch_op.drop_column('artist_ids')
        batch_op.drop_column('mb_track_id')
