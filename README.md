# Harbor

Production-grade customer support assistant for an online store, built end to end:

- **RAG** with hybrid retrieval: pgvector (dense, HNSW) + Postgres full-text search, fused with Reciprocal Rank Fusion, then reranked (Voyage rerank-2.5, or an LLM reranker). Pinecone is a drop-in alternative index.
- **Multi-agent workflow** on LangGraph: guard, router, knowledge agent, order agent (tool use), escalation agent, small talk, and a groundedness verifier that sends weak drafts back for a rewrite. Conversation memory is checkpointed in Postgres per thread.
- **LLM**: Claude (Opus 5.5 for answers, Haiku 4.5 for classification and reranking, Sonnet 5.5 for verification) via `langchain-anthropic` with structured outputs.
- **Voice**: push-to-talk in the browser, speech to text and text to speech through ElevenLabs or OpenAI; the voice turn runs the same graph as text.
- **Prompt-injection defence** in five layers: heuristic scoring, LLM classifier, document quarantine at ingestion, spotlighting of untrusted content, output guard (secret redaction, citation validation, prompt canary).
- **Evaluation**: golden set with routing accuracy, retrieval hit rate and MRR, LLM-as-judge rubric, Ragas (faithfulness, answer relevancy, context precision and recall) and an injection red-team suite. Runs in CI in offline mode.
- **Operations**: FastAPI with API keys, rate limiting, request ids, structured logs, health and readiness probes, Alembic migrations, Docker image, docker compose, GitHub Actions.

## Quick start (no keys, no database)

```bash
make install
make demo            # http://127.0.0.1:8000  (API key: dev-key)
.venv/bin/harbor chat --offline
```

Offline mode swaps Claude for a deterministic scripted model and the vector index for an in-memory one. Every code path (graph, guard, streaming, voice endpoint, evals) is the production one; only the providers are fakes.

## Full stack

```bash
cp .env.example .env     # add ANTHROPIC_API_KEY and VOYAGE_API_KEY (plus ELEVENLABS_API_KEY for voice)
make db migrate seed ingest
make serve
```

Or `docker compose up --build` to run the API and Postgres together.

```bash
curl -s localhost:8000/v1/chat -H 'x-api-key: dev-key' -H 'content-type: application/json' \
  -d '{"message": "How long do I have to return an item?"}' | jq
```

## Layout

```
harbor/
  api/          FastAPI app, middleware (auth, rate limit, request id), routes (chat, stream, voice, ingest, conversations)
  agents/       LangGraph state, nodes, graph, runtime, tools, structured output schemas, model factory, scripted fake model
  retrieval/    embeddings providers, vector index adapters (pgvector, Pinecone), RRF fusion, rerankers, hybrid retriever
  ingest/       loader, heading-aware chunker with breadcrumbs, idempotent pipeline with injection quarantine
  guard/        injection heuristics, spotlighting, output guard (secrets, citations, canary)
  voice/        ElevenLabs / OpenAI speech providers
  evals/        golden set, attack set, metrics, LLM judge, Ragas adapter, runner
  db/           SQLAlchemy models, repositories (hybrid search SQL), engine
  prompts/      versioned system prompts
alembic/        migrations (pgvector extension, HNSW + GIN indexes)
knowledge/      sample policy documents for the fictional Harbor Outfitters store
seeds/          sample customers and orders
tests/          unit (offline) and integration (Postgres) suites
```

## Tests and evals

```bash
make test                 # unit suite, offline, a few seconds
make db test-integration  # real Postgres: migrations, ingestion, hybrid SQL, checkpoints
make eval-offline         # golden set + red team with the scripted model
make eval                 # full run with Claude, Voyage and Ragas; reports in evals/reports/<timestamp>/
```

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | /v1/chat | key | one turn, JSON |
| POST | /v1/chat/stream | key | one turn, server-sent events (status, token, rewrite, done) |
| POST | /v1/voice/chat | key | multipart audio in, transcript + answer + audio out |
| GET | /v1/conversations/{id} | key | transcript |
| POST | /v1/ingest/knowledge | admin | (re)ingest bundled knowledge |
| POST | /v1/ingest/upload | admin | ingest uploaded markdown/text |
| POST | /v1/ingest/reindex | admin | push all vectors to the external index |
| GET | /healthz, /readyz | none | probes |
