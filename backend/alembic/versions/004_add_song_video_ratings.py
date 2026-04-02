"""Add song_rating, video_rating, song_rating_set, video_rating_set columns.

Revision ID: 004
"""
from alembic import op
import sqlalchemy as sa

revision = "004_add_song_video_ratings"
down_revision = "003_add_source_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_items", sa.Column("song_rating", sa.Integer(), nullable=True))
    op.add_column("video_items", sa.Column("video_rating", sa.Integer(), nullable=True))
    op.add_column("video_items", sa.Column("song_rating_set", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("video_items", sa.Column("video_rating_set", sa.Boolean(), nullable=False, server_default="0"))

    # Backfill existing rows with default rating of 3
    op.execute("UPDATE video_items SET song_rating = 3 WHERE song_rating IS NULL")
    op.execute("UPDATE video_items SET video_rating = 3 WHERE video_rating IS NULL")


def downgrade() -> None:
    op.drop_column("video_items", "video_rating_set")
    op.drop_column("video_items", "song_rating_set")
    op.drop_column("video_items", "video_rating")
    op.drop_column("video_items", "song_rating")
