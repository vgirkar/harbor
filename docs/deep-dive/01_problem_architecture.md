# 1. Problem, product and architecture

## The problem I set out to solve

Support teams for online stores answer the same questions all day (return windows, shipping costs, warranty terms), look up orders, and triage the rest to a human. A naive chatbot fails in three predictable ways: it invents policy details, it leaks or misuses data, and it has no way to tell how good it is. Harbor is my answer to those three failures: grounded answers with citations, authorization in code rather than in prompts, and an evaluation loop that produces numbers.

I picked a fictional store (Harbor Outfitters) so every document, order and customer could be published, while the shapes are the ones real stores have. The knowledge base is eight markdown policy documents, about 3,100 words, covering shipping, returns, warranty, payments, orders, account and privacy, product care, and contact. The order system is a `customers` and `orders` table with six seeded orders in different states.

## What a user can do

- Ask a policy question in text and get an answer with inline citations such as [c1] that map to the exact document section used.
- Ask about an order ("Where is my order HO-100231?") and, if the channel has verified the customer's email, get status, carrier and tracking from the database.
- Ask for a human and get a ticket reference with a priority the model assigned.
- Hold a button and speak; the recording is transcribed, runs through the same workflow, and the answer comes back as audio.
- Try to jailbreak it and be refused before the request reaches any agent.

## Request lifecycle

One turn passes through these stages. The names are the LangGraph node names, which also appear in the `trace` field of every API response, so a reviewer can see the path a request took.

| Stage | What happens | Models and systems involved |
|---|---|---|
| API | Key check, rate limit, request id, body validation (message up to 4,000 characters) | FastAPI middleware |
| guard | 19 regex rules score the message; scores at or above 0.9 block immediately, 0.3 to 0.9 go to a Claude Haiku classifier | Haiku 4.5, structured output |
| router | Classifies intent (knowledge, order, escalate, smalltalk) with the last four turns as context; confidence under 0.4 falls back to knowledge | Haiku 4.5, structured output |
| knowledge | Embeds the question, runs dense and lexical search, fuses, reranks, builds spotlighted `<document>` blocks, drafts the answer with citations | Voyage embeddings and reranker, Postgres, Opus 5.5 |
| verifier | Checks every claim in the draft against the documents; if not grounded and attempts < 2, sends the draft back with the unsupported claims listed | Sonnet 5.5, structured output |
| order | Bounded tool loop (at most three rounds) with `lookup_order` and `list_recent_orders`; the verified email is bound into the tools | Opus 5.5 with tool use, Postgres |
| escalate | Summarises the conversation into a ticket with a priority, writes the ticket row | Haiku 4.5, Postgres |
| smalltalk | Short reply steering back to support topics | Opus 5.5 |
| finalize | Output guard: secret redaction, canary check, citation validation; maps citation ids to document sections; appends the assistant message to the thread | none |
| persistence | Transcript rows written; LangGraph checkpoint saved under the conversation id | Postgres |

## Component map

```
harbor/
  api/        FastAPI app, middleware (request id, API key, rate limit), routes (chat, stream, voice, ingest, conversations, health)
  agents/     state, nodes, graph, runtime (run and stream a turn), tools, structured-output schemas, model factory, scripted fake model
  retrieval/  embeddings providers, vector index adapters (pgvector, Pinecone), RRF fusion, rerankers, hybrid retriever
  ingest/     loader, heading-aware chunker with breadcrumbs, idempotent pipeline with injection quarantine
  guard/      injection heuristics, spotlighting, output guard (secrets, citations, canary)
  voice/      ElevenLabs and OpenAI speech providers over plain HTTP
  evals/      golden set, attack set, metrics, LLM judge, Ragas adapter, runner with reports
  db/         SQLAlchemy models, repositories with the hybrid-search SQL, async engine
  prompts/    versioned markdown prompts with content hashes
  container.py  composition root: offline container (no keys, no database) and Postgres container
alembic/      migration 0001: vector extension, tables, HNSW and GIN indexes
knowledge/    the eight policy documents       seeds/  customers and orders
static/       demo page: streaming chat and push-to-talk
tests/        27 unit tests (offline) and 3 integration tests (real Postgres)
```

## Four ways to run the same code

The composition root builds one of two containers. The offline container swaps only the providers: a scripted chat model that reads the documents block and follows the retry feedback, an in-memory hybrid index (hashed bag-of-words vectors plus BM25), in-memory orders and tickets, and a LangGraph `MemorySaver`. Everything else, including the graph, the guard, the streaming code, the API and the evaluation runner, is the production code. The Postgres container wires Claude, Voyage, pgvector or Pinecone, the Postgres checkpointer and real speech providers.

| Mode | Command | Needs | Use |
|---|---|---|---|
| Offline demo | `harbor demo` or `harbor chat --offline` | nothing | demos on a laptop, unit tests, CI, eval plumbing |
| Local full stack | `make db migrate seed ingest serve` | Docker for Postgres, Anthropic and Voyage keys | development against real models |
| Containers | `docker compose up --build` | the same keys in `.env` | deployment-shaped run |
| CI | GitHub Actions | nothing | lint, unit, integration against a pgvector service, offline eval report as artifact |

### Likely interviewer probes

- *Why a graph instead of one big prompt with tools?* Because the control flow has hard requirements a prompt cannot guarantee: the guard must run before anything else, the verifier must run after every policy answer, the tool loop must be bounded, and the trace must be inspectable. A graph makes those guarantees structural and testable.
- *What is the unit of state?* A conversation thread. The `messages` list is the durable part (checkpointed), everything else in the state is scratch for one turn.
- *What would you remove first if you had to simplify?* The verifier, and I would say why: it doubles the model cost of a policy answer. I kept it because unsupported policy claims are the most expensive failure in this domain, and the eval harness lets me measure what it buys.
