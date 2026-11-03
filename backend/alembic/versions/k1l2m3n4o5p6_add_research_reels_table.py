"""add research_reels table

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = 'k1l2m3n4o5p6'
down_revision = 'j0k1l2m3n4o5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'research_reels',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('instagram_url', sa.String(512), nullable=False),
        sa.Column('batch_source', sa.String(32), nullable=False),
        sa.Column('pipeline_version', sa.String(128), nullable=False),

        # creator / niche
        sa.Column('niche', sa.String(64), nullable=True),
        sa.Column('creator_handle', sa.String(128), nullable=True),
        sa.Column('follower_count', sa.Integer(), nullable=True),
        sa.Column('follower_tier', sa.String(16), nullable=True),
        sa.Column('region', sa.String(32), nullable=True),
        sa.Column('content_subformat', sa.String(128), nullable=True),

        # raw engagement
        sa.Column('view_count', sa.Integer(), nullable=True),
        sa.Column('like_count', sa.Integer(), nullable=True),
        sa.Column('comment_count', sa.Integer(), nullable=True),
        sa.Column('upload_date', sa.Date(), nullable=True),
        sa.Column('duration_sec', sa.Float(), nullable=True),

        # derived features
        sa.Column('days_since_upload', sa.Integer(), nullable=True),
        sa.Column('log_relative_views', sa.Float(), nullable=True),
        sa.Column('views_per_follower', sa.Float(), nullable=True),
        sa.Column('engagement_rate', sa.Float(), nullable=True),

        # scored parameters
        sa.Column('score_hook', sa.Float(), nullable=True),
        sa.Column('score_retention', sa.Float(), nullable=True),
        sa.Column('score_audio', sa.Float(), nullable=True),
        sa.Column('score_visual', sa.Float(), nullable=True),
        sa.Column('score_trend', sa.Float(), nullable=True),
        sa.Column('score_shareability', sa.Float(), nullable=True),

        # advisory + raw output
        sa.Column('advisory_json', JSONB(), nullable=True),
        sa.Column('raw_pipeline_json', JSONB(), nullable=True),

        # R2 pointers
        sa.Column('r2_frames_prefix', sa.String(512), nullable=True),
        sa.Column('r2_audio_key', sa.String(512), nullable=True),

        # health + audit
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
    )

    # Unique constraint on URL (dedup key)
    op.create_unique_constraint('uq_research_reels_url', 'research_reels', ['instagram_url'])

    # Single-column indexes
    op.create_index('ix_research_reels_batch_source', 'research_reels', ['batch_source'])
    op.create_index('ix_research_reels_niche',        'research_reels', ['niche'])
    op.create_index('ix_research_reels_creator',      'research_reels', ['creator_handle'])
    op.create_index('ix_research_reels_follower_tier','research_reels', ['follower_tier'])
    op.create_index('ix_research_reels_region',       'research_reels', ['region'])

    # Composite index
    op.create_index('ix_research_reels_niche_creator','research_reels', ['niche', 'creator_handle'])


def downgrade():
    op.drop_index('ix_research_reels_niche_creator', table_name='research_reels')
    op.drop_index('ix_research_reels_region',        table_name='research_reels')
    op.drop_index('ix_research_reels_follower_tier', table_name='research_reels')
    op.drop_index('ix_research_reels_creator',       table_name='research_reels')
    op.drop_index('ix_research_reels_niche',         table_name='research_reels')
    op.drop_index('ix_research_reels_batch_source',  table_name='research_reels')
    op.drop_constraint('uq_research_reels_url', 'research_reels', type_='unique')
    op.drop_table('research_reels')
