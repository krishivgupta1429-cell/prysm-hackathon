"""add embedding columns to prysm_analyses

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-09-23

Adds four JSONB columns to store dense embeddings per analysis:
  visual_embedding  — DINOv2-base CLS token            (768-dim float list)
  motion_embedding  — V-JEPA 2 ViT-L CLS token         (1024-dim float list)
  audio_embedding   — CLAP htsat-fused audio projection (512-dim float list)
  joint_embedding   — CLIP-ViT-L/14 (LanguageBind core) (768-dim float list)

JSONB is used instead of pgvector VECTOR(n) because the pgvector extension is not
yet installed on all environments. Migrate to proper VECTOR columns (with HNSW/IVFFlat
indexes) once pgvector is confirmed installed in production:
  ALTER TABLE prysm_analyses
    ALTER COLUMN visual_embedding TYPE vector(768) USING visual_embedding::text::vector,
    ...
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'l2m3n4o5p6q7'
down_revision = 'k1l2m3n4o5p6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('prysm_analyses', sa.Column('visual_embedding', JSONB, nullable=True))
    op.add_column('prysm_analyses', sa.Column('motion_embedding', JSONB, nullable=True))
    op.add_column('prysm_analyses', sa.Column('audio_embedding',  JSONB, nullable=True))
    op.add_column('prysm_analyses', sa.Column('joint_embedding',  JSONB, nullable=True))


def downgrade():
    op.drop_column('prysm_analyses', 'joint_embedding')
    op.drop_column('prysm_analyses', 'audio_embedding')
    op.drop_column('prysm_analyses', 'motion_embedding')
    op.drop_column('prysm_analyses', 'visual_embedding')
