---
title: "Harbor: Technical Deep Dive for Interviews"
subtitle: "Production RAG and multi-agent customer support assistant, text and voice"
---

25 Nov 2026 · Vinit Girkar · github.com/vgirkar/harbor

This document explains Harbor the way an interviewer will probe it: what it does, how the pieces fit, why each technology was chosen over its alternatives, how the database is integrated, how quality and safety are measured, and where the honest limits are. Every chapter ends with the questions an interviewer is likely to ask about that part and the short answer to give. The last chapters hold a question bank and the numbers worth memorising.

**How to use it.** Read the sixty-second pitch and the snapshot table first. Then read chapters 5 (retrieval), 6 (the agent graph), 8 (injection defence) and 10 (evaluation); these are the four topics that get the deepest follow-ups. Say everything in the first person: I chose, I measured, I rejected. Where a number is marked "estimated" it was reasoned from token counts and vendor pricing, not measured in this build, and should be said that way.

## The sixty-second pitch

Harbor is a customer support assistant for an online outdoor gear store, and it runs on paid frontier models or entirely on free components. A customer types or speaks a question; the system classifies it, blocks prompt-injection attempts, answers policy questions from a knowledge base with citations, looks up the customer's own orders through tools, or escalates to a human by opening a ticket. Policy answers are grounded by hybrid retrieval (dense vectors in pgvector plus Postgres full-text search, fused with Reciprocal Rank Fusion and reranked by a cross-encoder) and then checked by a second model that sends weak drafts back for a rewrite. The workflow is a LangGraph state machine with conversation memory checkpointed in Postgres. Quality is measured with a golden set, retrieval metrics, an LLM-as-judge rubric, Ragas, and a red-team suite, and the whole evaluation runs in CI in an offline mode that needs no API keys. It ships as a FastAPI service with API keys, rate limiting, streaming, structured logs, Alembic migrations, a Docker image and GitHub Actions.

## Snapshot

| | |
|---|---|
| Repository | github.com/vgirkar/harbor (public, MIT), featured on vgirkar.netlify.app; demo page at harbor-support.netlify.app |
| Domain | Customer support for a fictional store, Harbor Outfitters: 8 policy documents, 3 customers, 6 orders |
| Core stack | Python 3.11+, FastAPI, LangGraph 1.2, LangChain 1.5, Claude (Opus 5.5, Sonnet 5.5, Haiku 4.5) or any OpenAI-compatible endpoint (Groq, Gemini, Ollama), Voyage AI or local fastembed embeddings and reranker, Postgres 17 with pgvector, Pinecone adapter, ElevenLabs or OpenAI speech, Ragas 0.4 |
| Size | about 4,900 lines of Python across 9 packages, 10 versioned prompts, 33 automated tests, 1 Alembic migration |
| Agents | guard, router, knowledge agent, verifier, order agent, escalation agent, small talk, finalizer |
| Retrieval | dense top 20 + lexical top 20, RRF with k = 60, 24 candidates reranked to 6, relevance gate 0.15 |
| Defence | 19 heuristic rules, LLM classifier on the uncertain band, ingestion quarantine, spotlighting, output guard with canary |
| Evaluation | 18 golden questions, 14 red-team prompts (11 attacks, 3 benign controls), routing accuracy, hit@k, MRR, judge rubric, Ragas |
| Offline results | routing 1.00, hit@3 0.93, MRR 0.87, judge pass 1.00, attacks blocked 11 of 11, leaks 0, benign false positives 0 |
| Operations | API keys, token-bucket rate limit, request ids, structlog JSON logs, health and readiness probes, Docker multi-stage image (1.38 GB with eval extras), docker compose, CI with Postgres service |
| Verified | unit and integration suites green locally and in GitHub Actions; Docker image boots against Postgres and answers; the live path verified end to end with real models that need no key (Ollama qwen2.5:3b, fastembed bge-small embeddings, MiniLM cross-encoder reranker); the paid providers (Claude, Voyage, Pinecone, ElevenLabs) have adapters but were not exercised, no keys were available |

## What "production ready" means here

The phrase is easy to claim, so this is the concrete checklist Harbor meets, and the interviewer can verify each item in the repository.

| Concern | What Harbor does |
|---|---|
| Configuration | Every setting is a typed environment variable validated at boot (pydantic-settings); misconfiguration fails before the first request |
| Persistence | Postgres is the system of record for documents, chunks, conversations, orders and tickets; schema managed by Alembic |
| Memory | Conversation state checkpointed per thread by LangGraph's Postgres saver; transcripts stored separately for analytics |
| Safety | Five independent injection defences; authorization enforced in code, not in prompts; secrets never reach the model |
| Reliability | SDK retries on the model client, tenacity retries on speech HTTP, bounded loops (two drafts, three tool rounds), timeouts |
| Observability | Request ids on every log line and response header, node-level trace in every answer, prompt version hashes, optional LangSmith |
| Security | API key auth with constant-time comparison, admin key for ingestion, per-key rate limiting, input size limits, CORS allow-list |
| Quality | Golden set, judge rubric, Ragas and red team, with reports written to disk and run in CI |
| Delivery | Multi-stage Dockerfile, non-root user, health check, compose, GitHub Actions with a real pgvector service |
| Testability | Offline mode with a scripted model and an in-memory index so every code path runs without keys or a database |
