"""Add editor_edit_type column to video_items.

Tracks what type of video editor edit has been applied:
'crop', 'trim', 'both', or NULL (not edited).

Revision ID: 015_add_editor_edit_type
Revises: 014_genre_consolidation
"""
from alembic import op
import sqlalchemy as sa

revision = '015_add_editor_edit_type'
down_revision = '014_genre_consolidation'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('video_items', sa.Column('editor_edit_type', sa.String(10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('video_items') as batch_op:
        batch_op.drop_column('editor_edit_type')
