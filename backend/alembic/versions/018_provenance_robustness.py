"""Provenance robustness for TMVDB contribution + trust profiles.

Adds:
- video_items.field_provenance_at — per-field last-set timestamps
- video_items.field_verifications — per-field human confirmation of auto values
- video_items.song_rating_by / song_rating_at — rating provenance
- video_items.video_rating_by / video_rating_at — rating provenance
- video_items.file_checksum — fast content signature
- contribution_log — outbound contribution audit trail

Revision ID: 018_provenance_robustness
Revises: 017_add_crop_position
"""
from alembic import op
import sqlalchemy as sa

revision = '018_provenance_robustness'
down_revision = '017_add_crop_position'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('video_items', sa.Column('field_provenance_at', sa.JSON(), nullable=True))
    op.add_column('video_items', sa.Column('field_verifications', sa.JSON(), nullable=True))
    op.add_column('video_items', sa.Column('song_rating_by', sa.String(36), nullable=True))
    op.add_column('video_items', sa.Column('song_rating_at', sa.DateTime(), nullable=True))
    op.add_column('video_items', sa.Column('video_rating_by', sa.String(36), nullable=True))
    op.add_column('video_items', sa.Column('video_rating_at', sa.DateTime(), nullable=True))
    op.add_column('video_items', sa.Column('file_checksum', sa.String(64), nullable=True))
    op.create_index('ix_video_items_file_checksum', 'video_items', ['file_checksum'])

    op.create_table(
        'contribution_log',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('video_id', sa.Integer(),
                  sa.ForeignKey('video_items.id', ondelete='SET NULL'), nullable=True),
        sa.Column('instance_user_id', sa.String(36), nullable=True),
        sa.Column('target', sa.String(40), nullable=False, server_default='tmvdb'),
        sa.Column('operation', sa.String(20), nullable=False),
        sa.Column('playarr_track_id', sa.String(16), nullable=True),
        sa.Column('playarr_video_id', sa.String(16), nullable=True),
        sa.Column('payload_hash', sa.String(64), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('remote_id', sa.String(100), nullable=True),
        sa.Column('response', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_contribution_log_video_id', 'contribution_log', ['video_id'])
    op.create_index('ix_contribution_log_instance_user_id', 'contribution_log', ['instance_user_id'])
    op.create_index('ix_contribution_log_playarr_track_id', 'contribution_log', ['playarr_track_id'])
    op.create_index('ix_contribution_log_playarr_video_id', 'contribution_log', ['playarr_video_id'])
    op.create_index('ix_contribution_log_payload_hash', 'contribution_log', ['payload_hash'])
    op.create_index('ix_contribution_log_created_at', 'contribution_log', ['created_at'])


def downgrade() -> None:
    op.drop_table('contribution_log')
    with op.batch_alter_table('video_items') as batch_op:
        batch_op.drop_index('ix_video_items_file_checksum')
        batch_op.drop_column('file_checksum')
        batch_op.drop_column('video_rating_at')
        batch_op.drop_column('video_rating_by')
        batch_op.drop_column('song_rating_at')
        batch_op.drop_column('song_rating_by')
        batch_op.drop_column('field_verifications')
        batch_op.drop_column('field_provenance_at')
