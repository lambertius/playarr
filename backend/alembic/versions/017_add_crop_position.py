"""Add crop_position to media_assets and cached_assets.

Stores CSS object-position value (e.g. "50% 30%") for artwork crop control.

Revision ID: 017_add_crop_position
Revises: 016_add_user_id_tracking
"""
from alembic import op
import sqlalchemy as sa

revision = '017_add_crop_position'
down_revision = '016_add_user_id_tracking'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('media_assets', sa.Column('crop_position', sa.String(50), nullable=True))
    op.add_column('cached_assets', sa.Column('crop_position', sa.String(50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('cached_assets') as batch_op:
        batch_op.drop_column('crop_position')
    with op.batch_alter_table('media_assets') as batch_op:
        batch_op.drop_column('crop_position')
