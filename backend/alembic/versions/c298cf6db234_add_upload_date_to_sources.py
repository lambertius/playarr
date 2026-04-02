"""
add upload_date to sources

Revision ID: c298cf6db234
Revises: deb7429cbaaa
Create Date: 2026-03-07 10:29:14.282613
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c298cf6db234'
down_revision: Union[str, None] = 'deb7429cbaaa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sources', sa.Column('upload_date', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('sources', 'upload_date')
    # ### end Alembic commands ###
