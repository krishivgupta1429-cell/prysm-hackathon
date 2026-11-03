"""add share fields to prysm_analyses

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('prysm_analyses', sa.Column('share_id', sa.String(12), nullable=True))
    op.add_column('prysm_analyses', sa.Column('is_public', sa.Boolean(), server_default='false', nullable=False))
    op.create_index('ix_prysm_analyses_share_id', 'prysm_analyses', ['share_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_prysm_analyses_share_id', table_name='prysm_analyses')
    op.drop_column('prysm_analyses', 'is_public')
    op.drop_column('prysm_analyses', 'share_id')
