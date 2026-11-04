"""merge hackathon branches (instagram, dinov2)

Revision ID: t0u1v2w3x4y5
Revises: r8s9t0u1v2w3, s9t0u1v2w3x4
Create Date: 2026-11-04 08:02:40.178339

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 't0u1v2w3x4y5'
down_revision: Union[str, None] = ('r8s9t0u1v2w3', 's9t0u1v2w3x4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
