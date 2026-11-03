"""Built during the Meta Global AI Developer Hackathon (new work).

add ig_account, ig_reel, ig_reel_insight, creator_dna (Instagram connect + Creator DNA)

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-11-04

New tables only; no baseline table is altered. The access token stays in users.instagram_access_token (baseline).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "n4o5p6q7r8s9"
down_revision = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "ig_account",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ig_user_id", sa.String(50), nullable=False),
        sa.Column("username", sa.String(100)),
        sa.Column("account_type", sa.String(30)),
        sa.Column("is_fixture", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("scopes", sa.String(300)),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("token_refreshed_at", sa.DateTime(timezone=True)),
        sa.Column("connected_at", sa.DateTime(timezone=True)),
        sa.Column("last_import_at", sa.DateTime(timezone=True)),
        sa.Column("last_import_requested", sa.Integer),
        sa.Column("last_import_summary", sa.JSON),
    )
    op.create_index("ix_ig_account_user_id", "ig_account", ["user_id"], unique=True)
    op.create_table(
        "ig_reel",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("account_id", uuid, sa.ForeignKey("ig_account.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.String(60), nullable=False),
        sa.Column("permalink", sa.Text),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("caption", sa.Text),
        sa.Column("duration_s", sa.Float),
        sa.Column("like_count", sa.Integer),
        sa.Column("comments_count", sa.Integer),
        sa.Column("frames_used", sa.Integer),
        sa.Column("performance_index", sa.Float),
        sa.Column("performance_basis", sa.String(20)),
        sa.Column("watch_through", sa.Float),
        sa.Column("fingerprint", sa.JSON),
        sa.Column("note", sa.Text),
        sa.UniqueConstraint("account_id", "media_id", name="uq_ig_reel_account_media"),
    )
    op.create_index("ix_ig_reel_account_id", "ig_reel", ["account_id"])
    op.create_table(
        "ig_reel_insight",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("reel_id", uuid, sa.ForeignKey("ig_reel.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric", sa.String(60), nullable=False),
        sa.Column("value", sa.Float),
        sa.Column("available", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("reel_id", "metric", name="uq_ig_reel_insight_metric"),
    )
    op.create_index("ix_ig_reel_insight_reel_id", "ig_reel_insight", ["reel_id"])
    op.create_table(
        "creator_dna",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("account_id", uuid, sa.ForeignKey("ig_account.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dna", sa.JSON),
        sa.Column("summary", sa.Text),
        sa.Column("sample_size", sa.Integer),
        sa.Column("metrics_available", sa.JSON),
        sa.Column("medians", sa.JSON),
        sa.Column("model", sa.String(60)),
        sa.Column("cost_usd", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_creator_dna_account_id", "creator_dna", ["account_id"], unique=True)
    op.create_index("ix_creator_dna_user_id", "creator_dna", ["user_id"])


def downgrade() -> None:
    op.drop_table("creator_dna")
    op.drop_table("ig_reel_insight")
    op.drop_table("ig_reel")
    op.drop_table("ig_account")
