"""add_trending_audio_and_niche_hashtags

Revision ID: c98f5d16e31e
Revises: g7h8i9j0k1l2
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c98f5d16e31e'
down_revision: Union[str, None] = 'g7h8i9j0k1l2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'trending_audio',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('niche', sa.String(100), nullable=False, index=True),
        sa.Column('platform', sa.String(30), nullable=False, index=True),
        sa.Column('audio_title', sa.String(255), nullable=False),
        sa.Column('artist_name', sa.String(255), nullable=True),
        sa.Column('usage_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('avg_likes', sa.Float, nullable=False, server_default='0'),
        sa.Column('genre', sa.String(100), nullable=True),
        sa.Column('mood', sa.String(100), nullable=True),
        sa.Column('co_hashtags', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('week_start', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('sample_posts', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
    )
    op.create_index('ix_trending_audio_niche_platform_week',
                    'trending_audio', ['niche', 'platform', 'week_start'])

    op.create_table(
        'niche_hashtags',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('niche', sa.String(100), nullable=False, index=True),
        sa.Column('platform', sa.String(30), nullable=False, index=True),
        sa.Column('hashtag', sa.String(150), nullable=False),
        sa.Column('frequency', sa.Integer, nullable=False, server_default='0'),
        sa.Column('avg_likes', sa.Float, nullable=False, server_default='0'),
        sa.Column('week_start', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
    )
    op.create_index('ix_niche_hashtags_niche_platform_week',
                    'niche_hashtags', ['niche', 'platform', 'week_start'])


def downgrade() -> None:
    op.drop_index('ix_niche_hashtags_niche_platform_week', table_name='niche_hashtags')
    op.drop_table('niche_hashtags')
    op.drop_index('ix_trending_audio_niche_platform_week', table_name='trending_audio')
    op.drop_table('trending_audio')
