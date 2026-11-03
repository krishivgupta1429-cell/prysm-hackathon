"""add cost_usd to chat_messages

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-06-21

"""
from alembic import op
import sqlalchemy as sa

revision = 'h8i9j0k1l2m3'
down_revision = 'g7h8i9j0k1l2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_messages', sa.Column('cost_usd', sa.Float(), nullable=True))


def downgrade():
    op.drop_column('chat_messages', 'cost_usd')
