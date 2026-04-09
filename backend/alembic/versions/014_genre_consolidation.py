"""
add_genre_consolidation_and_artist_canonical

Revision ID: 014_genre_consolidation
Revises: deb7429cbaaa
Create Date: 2026-04-09
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '014_genre_consolidation'
down_revision: Union[str, None] = 'deb7429cbaaa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Genre consolidation: self-referencing master_genre_id
    op.add_column('genres', sa.Column('master_genre_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_genres_master_genre_id', 'genres', 'genres',
        ['master_genre_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_genres_master_genre_id', 'genres', type_='foreignkey')
    op.drop_column('genres', 'master_genre_id')
