"""Add exclude_from_editor_scan column to video_items.

Allows users to exclude specific videos from future letterbox scans
(e.g. false positives). Toggled via the Video Editor UI.

Revision ID: 009_add_exclude_from_editor_scan
"""
from alembic import op
import sqlalchemy as sa

revision = '009_add_exclude_from_editor_scan'
down_revision = '008_add_import_method'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('video_items', sa.Column('exclude_from_editor_scan', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('video_items') as batch_op:
        batch_op.drop_column('exclude_from_editor_scan')
