"""
Add playarr_video_id, playarr_track_id, and video_phash to video_items.

These are deterministic content-identity hashes:
  - playarr_track_id: same musical composition regardless of video
  - playarr_video_id: same visual content regardless of quality/resolution
  - video_phash: perceptual hash of representative frame

Revision ID: 013_playarr_content_ids
Revises: 012_mb_track_artist_ids
Create Date: 2026-04-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '013_playarr_content_ids'
down_revision: Union[str, None] = '012_mb_track_artist_ids'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("video_items") as batch_op:
        batch_op.add_column(sa.Column("playarr_video_id", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("playarr_track_id", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("video_phash", sa.String(16), nullable=True))
        batch_op.create_index("ix_video_items_playarr_video_id", ["playarr_video_id"])
        batch_op.create_index("ix_video_items_playarr_track_id", ["playarr_track_id"])


def downgrade() -> None:
    with op.batch_alter_table("video_items") as batch_op:
        batch_op.drop_index("ix_video_items_playarr_track_id")
        batch_op.drop_index("ix_video_items_playarr_video_id")
        batch_op.drop_column("video_phash")
        batch_op.drop_column("playarr_track_id")
        batch_op.drop_column("playarr_video_id")
