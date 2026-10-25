"""add_graph_checkpoint_to_approvals

Revision ID: 4f2a9c1d7e30
Revises: 1963c0bffc58
Create Date: 2026-10-26 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4f2a9c1d7e30'
down_revision: Union[str, None] = '1963c0bffc58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Where the paused LangGraph run for this approval can be resumed
    op.add_column('approvals', sa.Column('graph_thread_id', sa.String(), nullable=True))
    op.add_column('approvals', sa.Column('graph_checkpoint_id', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('approvals', 'graph_checkpoint_id')
    op.drop_column('approvals', 'graph_thread_id')
