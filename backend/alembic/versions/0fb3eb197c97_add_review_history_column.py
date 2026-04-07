"""
add review_history column

Revision ID: 0fb3eb197c97
Revises: 009_add_exclude_from_editor_scan
Create Date: 2026-04-04 13:53:49.635630
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0fb3eb197c97'
down_revision: Union[str, None] = '009_add_exclude_from_editor_scan'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('video_items', sa.Column('review_history', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('video_items', 'review_history')
    # ### end Alembic commands ###
