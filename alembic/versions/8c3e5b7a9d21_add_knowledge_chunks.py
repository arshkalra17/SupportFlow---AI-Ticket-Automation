"""add_knowledge_chunks

Revision ID: 8c3e5b7a9d21
Revises: 4f2a9c1d7e30
Create Date: 2026-10-26 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8c3e5b7a9d21'
down_revision: Union[str, None] = '4f2a9c1d7e30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Section-level chunks of knowledge_base/*.md replace the whole-document
    # table; the migrate service re-seeds them from the markdown files.
    op.create_table(
        'knowledge_chunks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('doc_slug', sa.String(), nullable=False),
        sa.Column('doc_title', sa.String(), nullable=False),
        sa.Column('heading', sa.String(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('embedding', Vector(384), nullable=False),
        sa.Column(
            'tsv',
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('english', coalesce(doc_title, '')), 'A') || "
                "setweight(to_tsvector('english', coalesce(heading, '')), 'A') || "
                "setweight(to_tsvector('english', content), 'B')",
                persisted=True,
            ),
        ),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_knowledge_chunks_doc_slug', 'knowledge_chunks', ['doc_slug'])
    op.create_index('ix_knowledge_chunks_tsv', 'knowledge_chunks', ['tsv'], postgresql_using='gin')
    op.create_index(
        'ix_knowledge_chunks_embedding_hnsw',
        'knowledge_chunks',
        ['embedding'],
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )
    # Document frequencies for BM25 keyword search (app/retrieval.py)
    op.execute(
        "CREATE MATERIALIZED VIEW knowledge_term_stats AS "
        "SELECT word AS lexeme, ndoc AS df FROM ts_stat('SELECT tsv FROM knowledge_chunks')"
    )
    op.execute("CREATE UNIQUE INDEX ix_knowledge_term_stats_lexeme ON knowledge_term_stats (lexeme)")
    op.drop_table('knowledge_documents')


def downgrade() -> None:
    op.create_table(
        'knowledge_documents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('embedding', Vector(384), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_knowledge_documents_id', 'knowledge_documents', ['id'])
    op.execute("DROP MATERIALIZED VIEW IF EXISTS knowledge_term_stats")
    op.drop_table('knowledge_chunks')
