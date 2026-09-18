"""add_customer_id_to_tickets_and_cancellation_action

Revision ID: 1963c0bffc58
Revises: 16e5b4c32d54
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1963c0bffc58'
down_revision: Union[str, None] = '16e5b4c32d54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add customer_id to tickets for customer isolation
    # nullable=True so existing tickets without a customer are retained
    op.add_column('tickets', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.create_index('ix_tickets_customer_id', 'tickets', ['customer_id'])

    # Add CANCELLATION to actions (no schema change needed — action_type is a free-form String)
    # This comment documents the new action_type value used by cancel_order tool.
    # action_type values: REFUND, REPLACEMENT_REQUEST, CANCELLATION


def downgrade() -> None:
    op.drop_index('ix_tickets_customer_id', table_name='tickets')
    op.drop_column('tickets', 'customer_id')
