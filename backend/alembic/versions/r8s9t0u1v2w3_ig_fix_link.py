"""Built during the Meta Global AI Developer Hackathon (new work).

ig_fix_link: a Prysm analysis linked to the reel posted after it ("Did my fix work?")

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-11-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r8s9t0u1v2w3"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "ig_fix_link",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("analysis_id", uuid, nullable=False),
        sa.Column("media_id", sa.String(60), nullable=False),
        sa.Column("applied", sa.JSON()),
        sa.Column("used_auto_edit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("analysis_snapshot", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "analysis_id", name="uq_ig_fix_link_user_analysis"),
    )


def downgrade() -> None:
    op.drop_table("ig_fix_link")
