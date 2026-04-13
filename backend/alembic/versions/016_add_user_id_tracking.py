"""Add user ID tracking for edit provenance.

Adds columns for tracking which user made each edit:
- metadata_snapshots.user_id — who triggered the snapshot
- video_items.field_provenance_users — per-field user attribution
- video_items.last_edited_by — last manual editor
- artists.field_provenance_users — per-field user attribution
- albums.field_provenance_users — per-field user attribution
- tracks.field_provenance_users — per-field user attribution

Revision ID: 016_add_user_id_tracking
Revises: 013_playarr_content_ids, 015_add_editor_edit_type
"""
from alembic import op
import sqlalchemy as sa

revision = '016_add_user_id_tracking'
down_revision = ('013_playarr_content_ids', '015_add_editor_edit_type')
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MetadataSnapshot — who triggered the snapshot
    op.add_column('metadata_snapshots', sa.Column('user_id', sa.String(36), nullable=True))

    # VideoItem — per-field user attribution and last editor
    op.add_column('video_items', sa.Column('field_provenance_users', sa.JSON(), nullable=True))
    op.add_column('video_items', sa.Column('last_edited_by', sa.String(36), nullable=True))

    # Canonical entity models — per-field user attribution
    op.add_column('artists', sa.Column('field_provenance_users', sa.JSON(), nullable=True))
    op.add_column('albums', sa.Column('field_provenance_users', sa.JSON(), nullable=True))
    op.add_column('tracks', sa.Column('field_provenance_users', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tracks') as batch_op:
        batch_op.drop_column('field_provenance_users')
    with op.batch_alter_table('albums') as batch_op:
        batch_op.drop_column('field_provenance_users')
    with op.batch_alter_table('artists') as batch_op:
        batch_op.drop_column('field_provenance_users')
    with op.batch_alter_table('video_items') as batch_op:
        batch_op.drop_column('last_edited_by')
        batch_op.drop_column('field_provenance_users')
    with op.batch_alter_table('metadata_snapshots') as batch_op:
        batch_op.drop_column('user_id')
