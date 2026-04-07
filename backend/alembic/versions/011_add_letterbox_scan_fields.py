"""Add letterbox scan result fields to quality_signatures.

Persists letterbox detection results so rescans can skip previously-analyzed
files, dramatically speeding up repeated scans on large libraries.

Revision ID: 011_add_letterbox_scan_fields
"""
from alembic import op
import sqlalchemy as sa

revision = '011_add_letterbox_scan_fields'
down_revision = '010_add_rename_dismissed'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('quality_signatures') as batch_op:
        batch_op.add_column(sa.Column('letterbox_scanned', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('letterbox_detected', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('letterbox_crop_w', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('letterbox_crop_h', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('letterbox_crop_x', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('letterbox_crop_y', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('letterbox_bar_top', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('letterbox_bar_bottom', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('letterbox_bar_left', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('letterbox_bar_right', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('quality_signatures') as batch_op:
        batch_op.drop_column('letterbox_bar_right')
        batch_op.drop_column('letterbox_bar_left')
        batch_op.drop_column('letterbox_bar_bottom')
        batch_op.drop_column('letterbox_bar_top')
        batch_op.drop_column('letterbox_crop_y')
        batch_op.drop_column('letterbox_crop_x')
        batch_op.drop_column('letterbox_crop_h')
        batch_op.drop_column('letterbox_crop_w')
        batch_op.drop_column('letterbox_detected')
        batch_op.drop_column('letterbox_scanned')
