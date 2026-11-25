# 2. Tech stack and why each choice

The guiding rule was to own as little as possible that a vendor does better, and to keep every vendor behind a small interface so it can be swapped by changing one environment variable. The table lists what I chose, what I considered, and the reason.

| Layer | Choice | Considered | Why this one |
|---|---|---|---|
| Language and runtime | Python 3.11+, asyncio end to end | Node, Go | The LLM ecosystem (LangGraph, Ragas, pgvector bindings) is Python first; async matters because a turn spends most of its time waiting on network calls |
| Web framework | FastAPI 0.165 | Flask, Django | Native async, Pydantic validation, OpenAPI for free, trivial SSE with sse-starlette |
| Orchestration | LangGraph 1.2 | Plain LangChain agent, hand-rolled state machine, Temporal | Explicit state, conditional edges, built-in checkpointing to Postgres, event streaming per node; still plain Python functions so it is easy to test |
| LLM | Claude via langchain-anthropic 1.7 | OpenAI GPT, open models on vLLM | Strong instruction following for grounded answers; native structured outputs; three tiers (Opus, Sonnet, Haiku) let me match cost to each role |
| Models per role | Opus 5.5 answers, Sonnet 5.5 verifies, Haiku 4.5 classifies and reranks | One model for everything | Classification and reranking are cheap, high-volume calls where Haiku is adequate; the customer-facing answer gets the strongest model; verification needs judgement but not creativity |
| Embeddings | Voyage AI voyage-3.5, 1,024 dimensions | OpenAI text-embedding-3, local BGE models | Anthropic's recommended embeddings partner; asymmetric query and document inputs; the same vendor provides a reranker so one key covers both; OpenAI adapter kept as an optional extra |
| Reranker | Voyage rerank-2.5 (cross-encoder), LLM reranker as fallback | Cohere rerank, local cross-encoder, none | A cross-encoder reads query and passage together and corrects most bi-encoder mistakes; hosted so no GPU; the Haiku listwise reranker exists so the pipeline works with one vendor fewer |
| Vector store | pgvector (HNSW, cosine) in the same Postgres | Pinecone, Weaviate, Qdrant, FAISS | One database for documents, chunks, vectors, full-text search, transcripts and checkpoints means one backup, one transaction boundary and one connection pool; adequate well past a million chunks |
| Scale-out index | Pinecone adapter (serverless) | Keep only pgvector | Shows the seam: when the corpus or query rate outgrows one Postgres, vectors move to Pinecone and Postgres stays the system of record that hydrates the matched ids |
| Lexical search | Postgres full-text search (tsvector, GIN, ts_rank_cd) | Elasticsearch, OpenSearch, BM25 in Python | Already in the database, stemming and stop words included, one index; a dedicated engine is only worth it at much larger scale |
| Fusion | Reciprocal Rank Fusion, k = 60 | Score normalisation and weighted sum, learned fusion | Rank based so it never has to reconcile cosine similarity with ts_rank; one parameter; the standard choice in the literature |
| Database access | SQLAlchemy 2.1 async with psycopg 3 | asyncpg, raw psycopg | One driver for the ORM, the hybrid-search SQL and the LangGraph checkpointer; Alembic for migrations |
| Chunking | langchain-text-splitters: markdown header splitter then recursive character splitter | Fixed windows, semantic chunking | Policies are organised by heading; a chunk that straddles two policies produces wrong answers; breadcrumbs give each chunk its context |
| Speech | ElevenLabs Scribe and Flash v2.5 over HTTP, OpenAI as alternative | Deepgram, Whisper locally, a realtime voice API | Highest quality TTS I have worked with; plain httpx calls keep dependencies small; the provider is a two-method protocol |
| Evaluation | Ragas 0.4 collections API, own LLM-as-judge rubric, own retrieval metrics | DeepEval, TruLens, promptfoo | Ragas gives the standard RAG metrics; my own judge gives domain criteria and a pass gate; deterministic metrics (routing, hit@k, MRR) cost nothing and run in CI |
| Logging | structlog with contextvars | stdlib logging, loguru | Structured JSON in production, request id bound once per request and present on every line |
| Config | pydantic-settings | python-dotenv, dynaconf | Typed, validated at import, one place to read |
| Packaging and CI | pyproject with extras, ruff, pytest, Docker multi-stage, GitHub Actions with a pgvector service container | Poetry, tox | Minimal and standard; CI exercises the real database path |

## Versions in the build

langgraph 1.2.24, langchain 1.5.2, langchain-core 1.8.4, langchain-anthropic 1.7.12, anthropic SDK 2.14.0, ragas 0.4.4, instructor 1.3.2, pgvector 0.5.1, psycopg 3.3.9, SQLAlchemy 2.1.9, alembic 1.20.1, fastapi 0.165.0, pinecone 10.3.0, voyageai 0.6.0, structlog 26.1.0. Postgres image pgvector/pgvector:pg17. Python 3.11 locally, 3.12 in the Docker image.

## Two version-specific decisions worth knowing

**Structured output on Claude Opus 5.5 and Sonnet 5.5.** These models reject forced tool choice (`tool_choice: any` or a named tool returns HTTP 400). LangChain's default `with_structured_output` uses forced tool calling, so on those models I pass `method="json_schema"`, which uses Claude's native structured-output feature. On Haiku 4.5, forced tool calling still works and is the reliable path, so the model factory picks the method per model id. This is the kind of detail that breaks silently when a model is upgraded, so it lives in one function, `structured_method`.

**Thinking and effort.** Opus 5.5 and Sonnet 5.5 always think; depth is controlled with `output_config.effort`. I run answers at medium effort and verification and classification at low effort. Haiku 4.5 does not accept the effort parameter, so the factory only sets it on models that support it.

### Likely interviewer probes

- *Why not one vendor for everything?* Anthropic does not offer embeddings or reranking. Voyage is its recommended partner and gives both behind one key. The adapters make the choice reversible.
- *Why pgvector over a dedicated vector database?* Operational simplicity and transactional consistency with the chunk text and metadata. I would move vectors out when the index no longer fits comfortably in Postgres memory or when query rate needs independent scaling, and the Pinecone adapter is that path.
- *Why LangGraph rather than writing the state machine yourself?* Checkpointing, streaming of node events and the conditional-edge model are exactly the parts that are tedious and error prone to write. The nodes themselves are plain async functions, so the framework surface I depend on is small.
- *What did you deliberately not use?* No agent framework that hides the loop (the order agent's tool loop is twenty lines I can read), no LangChain chains for prompting (prompts are markdown files rendered with two placeholders), no ORM magic in the hot path (the hybrid search is explicit SQL).
