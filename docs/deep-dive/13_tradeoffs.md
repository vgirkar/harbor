# 13. Trade-offs, limitations and roadmap

Interviewers trust a candidate who names the weaknesses before being asked. These are the ones I would raise.

## Trade-offs I made deliberately

| Decision | What it costs | Why I accepted it |
|---|---|---|
| Verifier on every policy answer | roughly doubles model cost and adds 1 to 2 s | unsupported policy claims are the most damaging failure in support; the cost is measurable and the node is removable |
| Opus 5.5 for answers | the most expensive tier | the customer-facing text is where quality is judged; routing and classification are already on Haiku, and Sonnet is the first lever if cost matters |
| Retrieval reuse on retry | a retry cannot fix a retrieval miss | retries exist to fix writing; a retrieval miss shows up in hit@k and is fixed in the pipeline, not per request |
| Ship the second draft even if still not grounded | some weak answers reach the customer | refusing after a retry is worse for the customer; the output guard still strips invalid citations and the eval counts retries |
| In-process rate limiter | wrong under several replicas | one container is the deployment unit here; the class is the seam for Redis |
| Postgres for everything | one database to scale | one backup, one transaction, one pool; Pinecone is the documented path out |
| Plain markdown knowledge base | no PDF or HTML loaders | the loader is the only format-specific code and everything downstream is format-agnostic |
| Request-response voice | no barge-in, no streaming audio | keeps the full quality pipeline; a realtime layer belongs in front of Harbor, not inside it |

## Limitations to state plainly

- The paid providers (Claude, Voyage, Pinecone, ElevenLabs) were not exercised in this build because no keys were available. The live path was verified with free components instead (Ollama, fastembed), which proves the integration code but not the quality the paid models would give; the command to run the eval with real keys is in the README.
- Offline eval numbers measure plumbing, not language quality; the Ollama 3B numbers measure a small model, not the production configuration.
- The golden set has 18 items and the attack set 14; both are seeds.
- No PII redaction before storing transcripts; no retention job.
- History is a fixed window of eight messages; very long threads need a summarisation node.
- The Docker image includes the evaluation extra and is 1.38 GB; a production image would omit it.
- Single Alembic migration; no data migration tooling for re-embedding yet beyond `ingest --force`.
- The LLM reranker is listwise with a 1,200 character cap per candidate; it is a fallback, not the primary.

## Roadmap, in the order I would do it

1. Run the eval with Groq (free) and then with Claude and Voyage, record both baselines, and tune the relevance gate, top_k and chunk target against them.
2. Prompt caching on the system block; measure cache reads and cost per turn.
3. Redis rate limiter and multi-replica deployment behind a load balancer.
4. PII redaction and transcript retention; alerting on guard block rate, verifier retries and latency percentiles.
5. Conversation summarisation node for long threads.
6. Human-in-the-loop interrupt before actions with side effects (refunds, address changes) once write tools exist.
7. Grow the golden and attack sets from production transcripts; nightly real-model eval as a release gate.
8. Realtime voice front end for a phone channel, with Harbor as the backend.
9. Deploy the API on the free host described in `deploy/HOSTING.md` and point the Netlify page at it.
