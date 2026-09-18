"""add_total_and_items_to_orders

Revision ID: 16e5b4c32d54
Revises: 70bc4c7c0a9a
Create Date: 2026-09-18 22:51:47.242010

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '16e5b4c32d54'
down_revision: Union[str, None] = '70bc4c7c0a9a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add total and items columns to orders table
    op.add_column('orders', sa.Column('total', sa.Float(), nullable=True))
    op.add_column('orders', sa.Column('items', sa.JSON(), nullable=True))


def downgrade() -> None:
    # Remove total and items columns from orders table
    op.drop_column('orders', 'items')
    op.drop_column('orders', 'total')
