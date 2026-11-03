"""update user plans and credits for new pricing system

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-14 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLUS and CREATOR enum values already added outside of transaction
    # (PostgreSQL requires ADD VALUE to be committed before use in same tx)

    # Convert credits from Integer to Float
    op.alter_column('users', 'credits',
                    type_=sa.Float(),
                    existing_type=sa.Integer(),
                    postgresql_using='credits::double precision')

    # Add Razorpay fields
    op.add_column('users', sa.Column('razorpay_customer_id', sa.String(100), nullable=True))
    op.add_column('users', sa.Column('razorpay_subscription_id', sa.String(100), nullable=True))
    op.add_column('users', sa.Column('billing_cycle', sa.String(10), nullable=True))
    op.add_column('users', sa.Column('plan_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('plan_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('rollover_credits', sa.Float(), server_default='0', nullable=False))

    # Migrate existing 'AGENCY' users to 'CREATOR'
    op.execute("UPDATE users SET plan = 'CREATOR' WHERE plan = 'AGENCY'")

    # Set free tier users to 20 credits (new default)
    op.execute("UPDATE users SET credits = 20 WHERE plan = 'FREE' AND credits = 50")


def downgrade() -> None:
    op.drop_column('users', 'rollover_credits')
    op.drop_column('users', 'plan_expires_at')
    op.drop_column('users', 'plan_started_at')
    op.drop_column('users', 'billing_cycle')
    op.drop_column('users', 'razorpay_subscription_id')
    op.drop_column('users', 'razorpay_customer_id')

    # Revert credits to Integer
    op.alter_column('users', 'credits',
                    type_=sa.Integer(),
                    existing_type=sa.Float(),
                    postgresql_using='credits::integer')

    # Migrate 'CREATOR' back to 'AGENCY'
    op.execute("UPDATE users SET plan = 'AGENCY' WHERE plan = 'CREATOR'")
