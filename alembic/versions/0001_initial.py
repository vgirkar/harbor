"""initial schema: documents, chunks (pgvector + tsvector), customers, orders, conversations, messages, tickets

Revision ID: 0001
Revises:
Create Date: 2026-11-25
"""

from alembic import op

from harbor.config import get_settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DIM = get_settings().embedding_dim


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE documents (
            id UUID PRIMARY KEY,
            source VARCHAR(512) NOT NULL UNIQUE,
            title VARCHAR(512) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE chunks (
            id UUID PRIMARY KEY,
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            content TEXT NOT NULL,
            embed_text TEXT NOT NULL,
            token_estimate INTEGER NOT NULL DEFAULT 0,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            quarantined BOOLEAN NOT NULL DEFAULT false,
            embedding vector({DIM}),
            tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', embed_text)) STORED,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_chunk_document_ordinal UNIQUE (document_id, ordinal)
        )
        """
    )
    op.execute("CREATE INDEX ix_chunks_document_id ON chunks (document_id)")
    op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        """
        CREATE TABLE customers (
            id UUID PRIMARY KEY,
            email VARCHAR(320) NOT NULL UNIQUE,
            name VARCHAR(200) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE orders (
            id UUID PRIMARY KEY,
            order_number VARCHAR(32) NOT NULL UNIQUE,
            customer_id UUID NOT NULL REFERENCES customers(id),
            status VARCHAR(32) NOT NULL,
            total NUMERIC(10,2) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'EUR',
            items JSONB NOT NULL DEFAULT '[]'::jsonb,
            carrier VARCHAR(64),
            tracking_number VARCHAR(64),
            placed_at TIMESTAMPTZ NOT NULL,
            shipped_at TIMESTAMPTZ,
            delivered_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_orders_customer_id ON orders (customer_id)")
    op.execute(
        """
        CREATE TABLE conversations (
            id UUID PRIMARY KEY,
            channel VARCHAR(16) NOT NULL DEFAULT 'text',
            customer_email VARCHAR(320),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_conversations_customer_email ON conversations (customer_email)")
    op.execute(
        """
        CREATE TABLE messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role VARCHAR(16) NOT NULL,
            content TEXT NOT NULL,
            intent VARCHAR(32),
            citations JSONB NOT NULL DEFAULT '[]'::jsonb,
            guard JSONB NOT NULL DEFAULT '{}'::jsonb,
            verdict JSONB NOT NULL DEFAULT '{}'::jsonb,
            latency_ms INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_messages_conversation_id ON messages (conversation_id)")
    op.execute(
        """
        CREATE TABLE tickets (
            id UUID PRIMARY KEY,
            conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
            customer_email VARCHAR(320),
            summary TEXT NOT NULL,
            priority VARCHAR(16) NOT NULL DEFAULT 'normal',
            status VARCHAR(16) NOT NULL DEFAULT 'open',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_tickets_conversation_id ON tickets (conversation_id)")


def downgrade() -> None:
    for table in ("tickets", "messages", "conversations", "orders", "customers", "chunks", "documents"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
