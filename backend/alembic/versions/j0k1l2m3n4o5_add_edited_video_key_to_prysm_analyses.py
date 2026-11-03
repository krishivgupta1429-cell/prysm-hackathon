"""add edited_video_key and edit_plan to prysm_analyses

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa

revision = 'j0k1l2m3n4o5'
down_revision = 'i9j0k1l2m3n4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('prysm_analyses', sa.Column('edited_video_key', sa.String(512), nullable=True))
    op.add_column('prysm_analyses', sa.Column('edit_plan', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('prysm_analyses', 'edit_plan')
    op.drop_column('prysm_analyses', 'edited_video_key')
