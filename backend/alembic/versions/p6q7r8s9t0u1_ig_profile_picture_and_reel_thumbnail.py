"""Built during the Meta Global AI Developer Hackathon (new work).

ig_account.profile_picture_url and ig_reel.thumbnail_url (links only; no image is stored)

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-11-04
"""
import sqlalchemy as sa
from alembic import op

revision = "p6q7r8s9t0u1"
down_revision = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ig_account", sa.Column("profile_picture_url", sa.Text(), nullable=True))
    op.add_column("ig_reel", sa.Column("thumbnail_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ig_reel", "thumbnail_url")
    op.drop_column("ig_account", "profile_picture_url")
