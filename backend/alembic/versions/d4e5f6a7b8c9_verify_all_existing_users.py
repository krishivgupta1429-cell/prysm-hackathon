"""verify all existing users (registered during dev mode)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All existing users were registered during APP_ENV=development,
    # which should have auto-verified them. Mark them all as verified.
    op.execute("UPDATE users SET is_verified = true WHERE is_verified = false")


def downgrade() -> None:
    pass  # Can't reliably undo this
