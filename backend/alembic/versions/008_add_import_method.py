"""Add import_method column to video_items.

Tracks how a video was added to the library:
- 'url'     — imported via URL pipeline
- 'import'  — imported via library import pipeline
- 'scanned' — added via library directory scan
- NULL      — legacy items (pre-migration)

Revision ID: 008_add_import_method
"""
from alembic import op
import sqlalchemy as sa

revision = '008_add_import_method'
down_revision = '007_mb_release_group_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('video_items', sa.Column('import_method', sa.String(20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('video_items') as batch_op:
        batch_op.drop_column('import_method')
