"""add intent column to prysm_analyses

Revision ID: a1b2c3d4e5f6
Revises: 09f6a0a77b8b
Create Date: 2026-03-28 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '09f6a0a77b8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('prysm_analyses', sa.Column('intent', sa.String(20), server_default='my_video', nullable=True))


def downgrade() -> None:
    op.drop_column('prysm_analyses', 'intent')
