"""add reel_visual_embedding and analysis_visual_embedding (DINOv2, vector(768))

Revision ID: s9t0u1v2w3x4
Revises: m3n4o5p6q7r8
Create Date: 2026-11-04

New tables only (hackathon, DINOv2 integration). No foreign keys into the ig_* tables: reel_visual_embedding joins them
by the media id string at query time. No ANN index: the nearest-reel query filters by user_id first (tens to hundreds of
rows), where an exact scan is faster and exact; add HNSW if a user ever has tens of thousands of reels.

RENUMBERED when merging: this file was n4o5p6q7r8s9, the same id the Instagram migration uses, so it was given a new id (never applied
anywhere before the merge). MERGING BRANCHES: this migration branches from m3n4o5p6q7r8 like the other hackathon branches do, so after merging
them run `alembic merge heads -m "merge hackathon branches"` and then `alembic upgrade head`.
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = 's9t0u1v2w3x4'
down_revision = 'm3n4o5p6q7r8'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "reel_visual_embedding",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.String(60), nullable=False),
        sa.Column("mean_vector", Vector(768), nullable=False),
        sa.Column("frame_count", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "media_id", name="uq_reel_visual_embedding_user_media"),
    )
    op.create_index("ix_reel_visual_embedding_user_id", "reel_visual_embedding", ["user_id"])
    op.create_table(
        "analysis_visual_embedding",
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prysm_analyses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("mean_vector", Vector(768), nullable=False),
        sa.Column("frame_count", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("analysis_visual_embedding")
    op.drop_index("ix_reel_visual_embedding_user_id", table_name="reel_visual_embedding")
    op.drop_table("reel_visual_embedding")
