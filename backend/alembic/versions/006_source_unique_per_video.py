"""Fix sources unique constraint: per-video instead of global.

The original UNIQUE(provider, source_video_id) prevents two videos from
referencing the same artist or album source (e.g. two songs by the same
artist both need a musicbrainz artist source with the same MB artist ID).

Changed to UNIQUE(video_id, provider, source_video_id) so each video can
have its own copy of shared artist/album sources.

Revision ID: 003_source_unique_per_video
Revises: 002_asset_validity
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa

revision = "006_source_unique_per_video"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite doesn't support ALTER TABLE DROP CONSTRAINT, so we need to
    # recreate the table with the new constraint.
    with op.batch_alter_table("sources", schema=None) as batch_op:
        batch_op.drop_constraint("uq_source_provider_vid", type_="unique")
        batch_op.create_unique_constraint(
            "uq_source_video_provider_vid",
            ["video_id", "provider", "source_video_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("sources", schema=None) as batch_op:
        batch_op.drop_constraint("uq_source_video_provider_vid", type_="unique")
        batch_op.create_unique_constraint(
            "uq_source_provider_vid",
            ["provider", "source_video_id"],
        )
