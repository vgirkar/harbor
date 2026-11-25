# 11. API, security and operations

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | /v1/chat | API key | one turn; returns answer, intent, citations, guard record, verdict, ticket id, trace, attempts, latency |
| POST | /v1/chat/stream | API key | same turn as server-sent events: `conversation`, `status`, `token`, `rewrite`, `done` |
| POST | /v1/voice/chat | API key | multipart audio in; transcript, answer and audio out |
| GET | /v1/conversations/{id} | API key | transcript with intents and citations |
| POST | /v1/ingest/knowledge | admin key | (re)ingest the bundled folder, optional `force` |
| POST | /v1/ingest/upload | admin key | ingest uploaded markdown or text files |
| POST | /v1/ingest/reindex | admin key | push every live vector to the external index |
| GET | /healthz, /readyz | none | liveness; readiness runs `SELECT 1` and reports the mode |
| GET | / | none | the demo page |

Request bodies are Pydantic models: message 1 to 4,000 characters, optional conversation id, optional customer email validated as an email. The customer email is documented as the verified identity from the caller's own auth layer; Harbor trusts the API key holder to have verified it, which is the standard split between a support backend and the storefront that fronts it.

## Middleware, outermost first

1. **Request id.** Reads `X-Request-ID` or generates one, binds it into structlog context so every log line of the request carries it, and echoes it in the response with a response-time header.
2. **API key.** Paths under `/v1/` require `X-API-Key`; ingestion paths require the admin key. Comparison uses `secrets.compare_digest`. Missing or wrong keys get 401, a client key on an admin path gets 403.
3. **Rate limit.** Token bucket per key (or per client IP without a key) refilled at the configured per-minute rate, 429 with `retry-after` when empty. In-process by design for a single-container deployment; the class is the seam for a Redis bucket when there are several replicas.
4. **CORS** with an explicit origin list.

## Logging and tracing

structlog renders a readable console in development and JSON lines in production (`APP_ENV=prod`). Every turn logs the router decision with confidence, retrieval counts and top score, the verifier verdict, tool calls with arguments, guard blocks with category and score, output-guard redactions, and a final `turn.done` with intent, latency and the node trace. Set `LANGSMITH_TRACING=true` and a key and LangChain emits full traces of every model call without code changes.

## Configuration and secrets

Everything is an environment variable with a typed default, listed in `.env.example` with comments. Provider keys are only read by the adapter that needs them; a missing key for a selected provider raises at boot with a message naming the variable. Nothing secret is logged; the output guard's credential patterns are the last line against a key appearing in an answer.

## Packaging and deployment

- **Dockerfile.** Multi-stage: a build stage installs the package with the evals extra, the runtime stage copies site-packages and the application, runs as a non-root user, exposes 8000, has a health check, and starts with `alembic upgrade head` followed by uvicorn. The image is 1.38 GB because the evals extra pulls Ragas and its data dependencies; a slimmer production image would omit that extra.
- **docker compose.** `db` (pgvector/pgvector:pg17 with a health check and a volume) and `api` (built from the Dockerfile, waits for the healthy database, reads `.env` if present).
- **CI.** GitHub Actions: Python 3.12, a pgvector service container, ruff, the unit suite, the integration suite against the service, the offline eval, and the report uploaded as an artifact; a second job builds the Docker image. The run on the initial push was green in 3 minutes.

## The demo front end

The page in `static/` is a dependency-free HTML, CSS and JavaScript app, served by FastAPI at the root and by Netlify as a static site. It follows the patterns that current AI products have converged on: a sidebar with suggested questions and connection settings, an empty state with task cards, a streaming answer that shows the agent's steps as they happen (guard, router, retrieval, verifier, with a visible "rewriting" step when the verifier rejects a draft), inline numbered citations that open a sources panel with the section, snippet and reranker score, a status line per answer (verified, unverified claims, order lookup, ticket created, blocked with the category), copy and feedback actions, a details view of the guard and verifier records, hold-to-talk voice with a recording timer, light and dark themes and a phone layout with a slide-in sidebar and a bottom-sheet sources panel. The point for an interview is that the UI exposes the system's honesty: the user sees what was checked and where every claim came from.

## Hosting on free tiers

Netlify hosts the demo page (https://harbor-support.netlify.app): a static site with a Backend URL box, deployed with `netlify deploy --prod --dir static`. Netlify cannot run a long-lived Python API with Postgres, so the API lives on a container host and the page talks to it across origins (the API's `CORS_ORIGINS` lists the Netlify site). The free combination documented in `deploy/HOSTING.md` is a Hugging Face Docker Space (16 GB RAM, persistent process, enough for the local models) or a Render free web service (`render.yaml` blueprint, 512 MB, sleeps when idle), Neon or Supabase for Postgres with pgvector, Groq for the LLM, and fastembed inside the container for embeddings and reranking. The Dockerfile accepts `--build-arg PRELOAD_LOCAL_MODELS=1` to bake the two ONNX models into the image so a cold start does not download them.

## Failure handling

| Failure | Behaviour |
|---|---|
| Model API transient error | SDK retries 3 times with backoff; then 500 with the request id |
| Invalid structured output (empty tool call, malformed JSON) | one retry with a corrective note, then the node's safe default (router: knowledge; verifier: skip; summary: raw text; guard: heuristics decide) |
| Speech API error | tenacity retries 3 times; then 5xx |
| Database down | readiness 503; requests fail fast through `pool_pre_ping` |
| Pinecone down | dense leg errors; readiness check reports the index |
| Oversized audio or message | 413 or 422 before any model call |
| Verifier rejects twice | second draft shipped with invalid citations stripped; counted in evals |
| Tool budget exhausted | model asked once more to answer with what it has |

## Scaling path

Horizontal: stateless containers behind a load balancer (state is in Postgres), the rate limiter moved to Redis, Postgres read replicas for the retrieval legs. Throughput: uvicorn workers per container, async everywhere so a container holds many in-flight turns. Corpus: Pinecone adapter, `ef_search` tuning, partitioning by tenant. Cost: prompt caching, Sonnet for answers on simple routes, batch eval runs. Ingestion: move to a queue worker when uploads are large.

### Likely interviewer probes

- *Why API keys and not OAuth?* The caller is a storefront or support tool, not an end user; machine-to-machine keys with constant-time comparison and per-key limits are the right tool. End-user identity arrives as the verified email from the caller.
- *What would you add before a real launch?* Redis rate limiting, PII redaction in transcripts, prompt caching, alerting on guard block rate and verifier retry rate, a retention job for transcripts, and a staging environment running the real-model eval as a gate.
- *How do you deploy a schema change?* Alembic migration in the same pull request, applied by the container at start; additive changes first, destructive ones in a later release.
- *Why is the front end on Netlify and the API elsewhere?* Netlify's runtime is Node serverless with short execution limits and no database; a LangGraph turn needs a persistent Python process, a connection pool and pgvector. Splitting them is the normal shape: static front end on a CDN, API on a container host.
