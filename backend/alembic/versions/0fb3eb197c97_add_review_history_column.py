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
    sa.Column('provider_video_id', sa.VARCHAR(length=200), nullable=True),
    sa.Column('artist', sa.VARCHAR(length=500), nullable=True),
    sa.Column('category', sa.VARCHAR(length=50), nullable=True),
    sa.Column('created_at', sa.DATETIME(), nullable=True),
    sa.Column('context_json', sqlite.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['suggested_video_id'], ['suggested_videos.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recommendation_feedback_suggested_video_id'), 'recommendation_feedback', ['suggested_video_id'], unique=False)
    op.create_index(op.f('ix_recommendation_feedback_feedback_type'), 'recommendation_feedback', ['feedback_type'], unique=False)
    op.create_index(op.f('ix_feedback_type_created'), 'recommendation_feedback', ['feedback_type', 'created_at'], unique=False)
    op.create_table('suggested_videos',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('provider', sa.VARCHAR(length=50), nullable=False),
    sa.Column('provider_video_id', sa.VARCHAR(length=200), nullable=False),
    sa.Column('url', sa.VARCHAR(length=2000), nullable=False),
    sa.Column('title', sa.VARCHAR(length=1000), nullable=False),
    sa.Column('artist', sa.VARCHAR(length=500), nullable=True),
    sa.Column('album', sa.VARCHAR(length=500), nullable=True),
    sa.Column('channel', sa.VARCHAR(length=500), nullable=True),
    sa.Column('thumbnail_url', sa.VARCHAR(length=2000), nullable=True),
    sa.Column('duration_seconds', sa.INTEGER(), nullable=True),
    sa.Column('release_date', sa.VARCHAR(length=20), nullable=True),
    sa.Column('view_count', sa.INTEGER(), nullable=True),
    sa.Column('category', sa.VARCHAR(length=50), nullable=False),
    sa.Column('source_type', sa.VARCHAR(length=100), nullable=True),
    sa.Column('trust_score', sa.FLOAT(), nullable=True),
    sa.Column('popularity_score', sa.FLOAT(), nullable=True),
    sa.Column('trend_score', sa.FLOAT(), nullable=True),
    sa.Column('recommendation_score', sa.FLOAT(), nullable=True),
    sa.Column('recommendation_reason_json', sqlite.JSON(), nullable=True),
    sa.Column('trust_reasons_json', sqlite.JSON(), nullable=True),
    sa.Column('metadata_json', sqlite.JSON(), nullable=True),
    sa.Column('created_at', sa.DATETIME(), nullable=True),
    sa.Column('updated_at', sa.DATETIME(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_video_id', 'category', name=op.f('uq_suggested_provider_video_category'))
    )
    op.create_index(op.f('ix_suggested_videos_category'), 'suggested_videos', ['category'], unique=False)
    op.create_index(op.f('ix_suggested_category_score'), 'suggested_videos', ['category', 'recommendation_score'], unique=False)
    op.create_table('suggested_video_cart_items',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('suggested_video_id', sa.INTEGER(), nullable=False),
    sa.Column('url', sa.VARCHAR(length=2000), nullable=False),
    sa.Column('title', sa.VARCHAR(length=1000), nullable=True),
    sa.Column('artist', sa.VARCHAR(length=500), nullable=True),
    sa.Column('provider', sa.VARCHAR(length=50), nullable=True),
    sa.Column('provider_video_id', sa.VARCHAR(length=200), nullable=True),
    sa.Column('added_at', sa.DATETIME(), nullable=True),
    sa.ForeignKeyConstraint(['suggested_video_id'], ['suggested_videos.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('suggested_video_id')
    )
    op.create_table('recommendation_snapshots',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('category', sa.VARCHAR(length=50), nullable=False),
    sa.Column('generated_at', sa.DATETIME(), nullable=True),
    sa.Column('expires_at', sa.DATETIME(), nullable=True),
    sa.Column('payload_json', sqlite.JSON(), nullable=True),
    sa.Column('generator_version', sa.VARCHAR(length=50), nullable=True),
    sa.Column('source_summary_json', sqlite.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recommendation_snapshots_category'), 'recommendation_snapshots', ['category'], unique=1)
    op.create_table('suggested_video_dismissals',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('suggested_video_id', sa.INTEGER(), nullable=False),
    sa.Column('dismissal_type', sa.VARCHAR(length=20), nullable=False),
    sa.Column('dismissed_at', sa.DATETIME(), nullable=True),
    sa.Column('reason', sa.VARCHAR(length=500), nullable=True),
    sa.Column('provider', sa.VARCHAR(length=50), nullable=True),
    sa.Column('provider_video_id', sa.VARCHAR(length=200), nullable=True),
    sa.ForeignKeyConstraint(['suggested_video_id'], ['suggested_videos.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_suggested_video_dismissals_suggested_video_id'), 'suggested_video_dismissals', ['suggested_video_id'], unique=False)
    op.create_index(op.f('ix_suggested_video_dismissals_provider_video_id'), 'suggested_video_dismissals', ['provider_video_id'], unique=False)
    # ### end Alembic commands ###
