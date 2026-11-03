"""add niche_formats table

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-06-23

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

revision = 'i9j0k1l2m3n4'
down_revision = ('h8i9j0k1l2m3', '9185c895e607')
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'niche_formats',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('niche', sa.String(100), nullable=False, index=True),
        sa.Column('platform', sa.String(30), nullable=False, index=True),
        sa.Column('format_label', sa.String(100), nullable=False),
        sa.Column('format_type', sa.String(50), nullable=True),
        sa.Column('frequency', sa.Integer(), default=0),
        sa.Column('avg_likes', sa.Float(), default=0.0),
        sa.Column('avg_comments', sa.Float(), default=0.0),
        sa.Column('example_hooks', JSON, nullable=True),
        sa.Column('source_creators', JSON, nullable=True),
        sa.Column('week_start', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_niche_formats_niche', 'niche_formats', ['niche'])
    op.create_index('ix_niche_formats_platform', 'niche_formats', ['platform'])
    op.create_index('ix_niche_formats_week_start', 'niche_formats', ['week_start'])


def downgrade():
    op.drop_table('niche_formats')
