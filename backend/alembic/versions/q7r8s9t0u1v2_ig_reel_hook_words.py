"""Built during the Meta Global AI Developer Hackathon (new work).

ig_reel.hook_words and ig_reel.hook_words_status (the first ~12 seconds of speech, as text only)

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-11-04
"""
import sqlalchemy as sa
from alembic import op

revision = "q7r8s9t0u1v2"
down_revision = "p6q7r8s9t0u1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ig_reel", sa.Column("hook_words", sa.Text(), nullable=True))
    op.add_column("ig_reel", sa.Column("hook_words_status", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("ig_reel", "hook_words_status")
    op.drop_column("ig_reel", "hook_words")
