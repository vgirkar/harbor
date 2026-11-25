# 3. Data layer and database integration

Postgres is the system of record for everything Harbor knows and everything it did. The vector index is either inside the same database (pgvector) or an external index that only holds ids and vectors (Pinecone). This chapter is the one to know cold: the schema, the indexes, the SQL in the hot path, and how the three consumers (the application, the hybrid search, the LangGraph checkpointer) share one database.

## Schema

All ids are UUIDs. Timestamps are `timestamptz`. JSON columns are `jsonb`.

| Table | Purpose | Notable columns and constraints |
|---|---|---|
| documents | One row per source file | `source` unique (the stable id, e.g. `knowledge/returns-and-refunds.md`), `title`, `content_hash` (sha256 of the file, drives idempotent ingestion), `metadata` |
| chunks | Retrieval unit | `document_id` (cascade delete), `ordinal`, `content` (what the model sees), `embed_text` (breadcrumb + content, what was embedded and indexed), `token_estimate`, `metadata` (breadcrumb, section, injection score), `quarantined`, `embedding vector(1024)`, `tsv tsvector` generated column; unique on (document_id, ordinal) |
| customers | Identity for the order agent | `email` unique |
| orders | The business system the tools read | `order_number` unique, `customer_id`, `status`, `total numeric(10,2)`, `items jsonb`, carrier, tracking, placed, shipped and delivered timestamps |
| conversations | One per thread | `channel` (text or voice), `customer_email` (verified identity from the API caller) |
| messages | Transcript for analytics and audit | `role`, `content`, `intent`, `citations jsonb`, `guard jsonb` (the guard decision for user turns), `verdict jsonb`, `latency_ms` |
| tickets | Escalations | `conversation_id` (set null on delete), `summary`, `priority`, `status` |
| checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations | LangGraph state per thread | Owned and created by `AsyncPostgresSaver.setup()`; keyed by `thread_id`, which Harbor sets to the conversation id |

## The two indexes that make hybrid search work

```sql
embedding vector(1024)
tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', embed_text)) STORED

CREATE INDEX ix_chunks_embedding_hnsw ON chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv);
```

**HNSW** (Hierarchical Navigable Small World) is a graph index for approximate nearest neighbour search. `m = 16` is the number of links per node (recall versus memory), `ef_construction = 64` is the candidate list size at build time (build time versus index quality). Both are pgvector defaults and fine for tens of thousands of chunks. At query time `SET hnsw.ef_search` raises recall at the cost of latency; I left the default of 40 because the lexical leg and the reranker already compensate for approximate misses. I chose HNSW over IVFFlat because HNSW needs no training step, handles inserts well and gives better recall at the same latency; IVFFlat only wins on build time and memory at very large scale.

**The generated tsvector** is computed by Postgres from `embed_text`, so the breadcrumb ("Returns and refunds > Refund timing and method") is searchable alongside the body, and it can never drift from the content because the application cannot write it. The `english` configuration gives stemming (returning, returns, return all match) and stop-word removal. The GIN index makes `tsv @@ query` an index lookup.

## The hybrid-search SQL

Dense leg, cosine similarity through the HNSW index. `<=>` is cosine distance, so similarity is `1 - distance`. The vector is passed as a literal and cast, which lets the planner use the index:

```sql
SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS score
FROM chunks
WHERE embedding IS NOT NULL AND quarantined = false
ORDER BY embedding <=> CAST(:q AS vector)
LIMIT :k
```

Lexical leg. The question is tokenised and the terms are joined with `or` in websearch syntax, so a long natural-language question matches chunks that contain some of its words (plain `websearch_to_tsquery` would AND every term and return almost nothing for "How long do I have to return an item?"). `ts_rank_cd` with normalisation flag 32 scales the score into 0 to 1 and rewards term density:

```sql
SELECT id, ts_rank_cd(tsv, q, 32) AS score
FROM chunks, websearch_to_tsquery('english', :query) AS q
WHERE tsv @@ q AND quarantined = false
ORDER BY score DESC
LIMIT :k
```

Both legs exclude quarantined chunks at the SQL level, so a poisoned document can never be retrieved even if some later code path forgets to filter.

## One driver, three consumers

The application uses SQLAlchemy 2 async sessions over psycopg 3 (`postgresql+psycopg://`). The LangGraph checkpointer needs a libpq-style DSN and its own connection semantics (autocommit, dict rows, no prepared statements), so it gets its own small `AsyncConnectionPool` (1 to 10 connections) built from the same URL. Keeping everything on psycopg 3 means one driver to understand and one set of failure modes; I rejected asyncpg for the ORM because it would have added a second driver purely for the checkpointer.

Connection hygiene: `pool_pre_ping` on the SQLAlchemy engine detects dead connections after a database restart, `pool_recycle` of 30 minutes avoids idle-timeout surprises behind load balancers, and the readiness probe runs `SELECT 1` through the engine so a broken pool shows up as 503 rather than as the first failed request.

## Transactions and idempotency

Ingestion is per document inside one session: upsert the document row, delete old chunks (collecting their ids so the external index can delete them too), insert new chunks with embeddings, upsert to the index, commit. A crash mid-document rolls back that document; the next run re-ingests it because the hash check only skips documents whose stored hash matches. Chat persistence writes the user and assistant rows after the graph has finished, in one session, so a transcript never has an orphan user turn from a failed generation (the checkpoint, written by LangGraph during the run, is the record of partial progress).

Order lookups enforce authorization in the query: the join on `customers.email` means the tool cannot return an order that does not belong to the verified email, whatever the model passes as an argument.

## Migrations and the dimension coupling

Alembic migration 0001 creates the extension, tables and indexes with explicit SQL so the DDL is reviewable. The vector dimension is read from settings at migration time (1,024 for voyage-3.5); switching embedding models to a different dimension is a schema change (new migration, re-embed, reindex), and I document that rather than hide it. The checkpointer's tables are created by its own `setup()` at application start, which is idempotent.

## Pinecone as the external index

When `VECTOR_BACKEND=pinecone`, the adapter creates a serverless cosine index if missing, upserts vectors in batches of 100 keyed by chunk id with the document source as metadata, and on query returns ids and scores that Postgres hydrates. The chunk rows keep their embedding column too, so switching back is a configuration change, and `harbor ingest` followed by `POST /v1/ingest/reindex` pushes every live vector to the new index. The lexical leg stays in Postgres in both modes, which is the main reason Postgres remains mandatory.

### Likely interviewer probes

- *Why keep vectors in Postgres at all if you have Pinecone?* Transactional consistency between chunk text, metadata and vector, one backup, and no second system until scale demands it. Pinecone is the documented escape hatch, not the default.
- *How do you keep the tsvector in sync?* It is a generated stored column; Postgres computes it from `embed_text` on every insert and update.
- *What is the cost of a generated tsvector on writes?* One `to_tsvector` call per chunk insert, negligible against the embedding call that precedes it.
- *How would you shard or scale reads?* Read replicas for the retrieval legs (both queries are read-only), the checkpointer and transcript writes stay on the primary. Beyond that, vectors move to Pinecone and lexical search to OpenSearch, and Postgres keeps transcripts and orders.
- *Why not store the conversation only in the checkpoint?* The checkpoint is a serialized LangGraph state optimised for resuming a graph, not for querying. The `messages` table is the analytics and audit view with intent, citations, guard decisions and latency per turn.
- *What happens if the embedding model changes?* New migration with the new dimension, re-embed every chunk (the pipeline's `force=True` path), reindex Pinecone if used. Mixed-dimension indexes are impossible by construction because the column type carries the dimension.
