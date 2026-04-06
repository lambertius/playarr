"""Add rename_dismissed column to video_items.

Allows users to dismiss rename flags so previously accepted filenames
are not re-flagged on future automatic rename scans.

Revision ID: 010_add_rename_dismissed
"""
from alembic import op
import sqlalchemy as sa

revision = '010_add_rename_dismissed'
down_revision = '009_add_exclude_from_editor_scan'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('video_items', sa.Column('rename_dismissed', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('video_items') as batch_op:
        batch_op.drop_column('rename_dismissed')
