"""add_is_admin_to_customers

Revision ID: 70bc4c7c0a9a
Revises: b2bb0349c8d8
Create Date: 2026-09-14 02:37:37.510227

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '70bc4c7c0a9a'
down_revision: Union[str, None] = 'b2bb0349c8d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_admin column to customers table
    op.add_column('customers', sa.Column('is_admin', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    # Remove is_admin column from customers table
    op.drop_column('customers', 'is_admin')
