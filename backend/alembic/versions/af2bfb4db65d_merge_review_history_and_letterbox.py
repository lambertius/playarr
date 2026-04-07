"""
merge_review_history_and_letterbox

Revision ID: af2bfb4db65d
Revises: 0fb3eb197c97, 011_add_letterbox_scan_fields
Create Date: 2026-04-07 13:40:14.039629
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'af2bfb4db65d'
down_revision: Union[str, None] = ('0fb3eb197c97', '011_add_letterbox_scan_fields')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
