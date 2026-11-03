"""add session_type and analysis_id to chat_sessions

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-20

"""
from alembic import op
import sqlalchemy as sa

revision = 'g7h8i9j0k1l2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('chat_sessions', sa.Column('session_type', sa.String(20), nullable=False, server_default='general'))
    op.add_column('chat_sessions', sa.Column('analysis_id', sa.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_chat_sessions_analysis_id',
        'chat_sessions', 'prysm_analyses',
        ['analysis_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_chat_sessions_analysis_id', 'chat_sessions', ['analysis_id'])


def downgrade() -> None:
    op.drop_index('ix_chat_sessions_analysis_id', table_name='chat_sessions')
    op.drop_constraint('fk_chat_sessions_analysis_id', 'chat_sessions', type_='foreignkey')
    op.drop_column('chat_sessions', 'analysis_id')
    op.drop_column('chat_sessions', 'session_type')
