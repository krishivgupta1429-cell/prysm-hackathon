"""migrate embedding columns from JSONB to vector(n) with IVFFlat indexes

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-09-24

Converts the 4 dense-embedding columns on prysm_analyses from JSONB to native
pgvector VECTOR(n) types, then adds IVFFlat cosine-similarity indexes so the
columns are ANN-searchable instead of just stored JSON blobs.

Prerequisites:
  - pgvector extension installed on the target Postgres instance.
    CREATE EXTENSION IF NOT EXISTS vector is issued at the top of upgrade()
    as a safe no-op if it's already present.

NULL safety:
  The USING clause casts JSONB → text → vector. PostgreSQL propagates NULL
  through both casts (NULL::text = NULL, NULL::text::vector = NULL), so rows
  that have no embedding yet (pre-pipeline or CLAP-not-yet-downloaded period)
  are left as NULL — no data loss, no error.

JSONB ↔ vector text compatibility:
  JSONB stores float arrays as [0.1, 0.2, ...] in standard JSON array notation.
  pgvector accepts exactly this text format for input, so the cast is lossless.

IVFFlat index tuning:
  lists=100 is the pgvector recommended default. Rule of thumb: lists ≈ sqrt(N)
  for N rows. Revisit when the table exceeds ~10k rows.

Dimensions:
  visual_embedding  768   DINOv2-base CLS token
  motion_embedding  1024  V-JEPA 2 ViT-L CLS token
  audio_embedding   512   CLAP htsat-fused audio projection
  joint_embedding   768   CLIP-ViT-L/14 (LanguageBind backbone)
"""
from alembic import op

revision = 'm3n4o5p6q7r8'
down_revision = 'l2m3n4o5p6q7'
branch_labels = None
depends_on = None


def upgrade():
    # Ensure the extension exists — no-op if already installed
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Cast JSONB → vector(n). Single ALTER TABLE statement so Postgres rewrites
    # the table once rather than four times.
    op.execute("""
        ALTER TABLE prysm_analyses
          ALTER COLUMN visual_embedding TYPE vector(768)  USING visual_embedding::text::vector,
          ALTER COLUMN motion_embedding TYPE vector(1024) USING motion_embedding::text::vector,
          ALTER COLUMN audio_embedding  TYPE vector(512)  USING audio_embedding::text::vector,
          ALTER COLUMN joint_embedding  TYPE vector(768)  USING joint_embedding::text::vector
    """)

    # IVFFlat indexes — named explicitly so downgrade() can drop them reliably
    op.execute("""
        CREATE INDEX prysm_analyses_visual_emb_idx
        ON prysm_analyses USING ivfflat (visual_embedding vector_cosine_ops)
        WITH (lists = 100)
    """)
    op.execute("""
        CREATE INDEX prysm_analyses_motion_emb_idx
        ON prysm_analyses USING ivfflat (motion_embedding vector_cosine_ops)
        WITH (lists = 100)
    """)
    op.execute("""
        CREATE INDEX prysm_analyses_audio_emb_idx
        ON prysm_analyses USING ivfflat (audio_embedding vector_cosine_ops)
        WITH (lists = 100)
    """)
    op.execute("""
        CREATE INDEX prysm_analyses_joint_emb_idx
        ON prysm_analyses USING ivfflat (joint_embedding vector_cosine_ops)
        WITH (lists = 100)
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS prysm_analyses_visual_emb_idx")
    op.execute("DROP INDEX IF EXISTS prysm_analyses_motion_emb_idx")
    op.execute("DROP INDEX IF EXISTS prysm_analyses_audio_emb_idx")
    op.execute("DROP INDEX IF EXISTS prysm_analyses_joint_emb_idx")

    # vector text format is valid JSONB array notation — cast back directly
    op.execute("""
        ALTER TABLE prysm_analyses
          ALTER COLUMN visual_embedding TYPE jsonb USING visual_embedding::text::jsonb,
          ALTER COLUMN motion_embedding TYPE jsonb USING motion_embedding::text::jsonb,
          ALTER COLUMN audio_embedding  TYPE jsonb USING audio_embedding::text::jsonb,
          ALTER COLUMN joint_embedding  TYPE jsonb USING joint_embedding::text::jsonb
    """)
