# 12. Testing strategy

## The offline mode is the test strategy

The hard problem in testing LLM systems is that the model is non-deterministic, slow and paid. Harbor's answer is a scripted chat model that implements LangChain's `BaseChatModel` and plays every role by rule: the router classifies by keywords, the guard classifier reuses the heuristics, the writer reads the `<document>` blocks in its prompt, picks the sentence that best matches the question, and cites the ids it was given; the verifier rejects a draft that contains a planted unsupported claim; the order agent emits real tool calls and summarises real tool results; the writer streams tokens. Because it reads the same prompts and produces the same message types as Claude, the surrounding code is exercised exactly as in production: structured output parsing, tag-based streaming, the retry edge, tool binding, citation validation.

Paired with it: an in-memory hybrid index (hashed bag-of-words vectors plus BM25 and the production RRF and gate), in-memory orders and tickets loaded from the same seed file, a `MemorySaver` checkpointer, and a fake speech provider. The container that wires them is thirty lines and is also what `harbor demo` runs.

## What each suite proves

| Suite | Count | Needs | Proves |
|---|---|---|---|
| Unit: chunker | 2 | nothing | heading splits, breadcrumbs, size bound, overlap |
| Unit: fusion | 2 | nothing | RRF arithmetic, preference for agreement, deterministic ties |
| Unit: guard | 6 | nothing | override, exfiltration and template attacks score high; frustration scores zero; secret redaction; invalid citation removal; canary; delimiter stripping |
| Unit: retriever | 2 | nothing | hit@3 of at least 0.8 on the golden questions with the in-memory index; poisoned chunks quarantined |
| Unit: graph | 8 | nothing | grounded cited answer and trace; verifier retry removes the unsupported claim and the invalid citation; injection blocked before routing; order lookup with verified email; refusal of another customer's order; escalation creates a high-priority ticket; small talk plus memory across turns (human, ai, human, ai in the checkpoint); streaming emits status, tokens and done |
| Unit: API | 6 | nothing | health and readiness; 401 and 403 rules; chat round trip with conversation continuity and request id header; SSE event order; voice round trip; validation errors |
| Unit: evals | 1 | nothing | the harness runs end to end and writes a report with prompt hashes; routing accuracy and false-positive assertions |
| Unit: fallbacks | 3 | nothing | a model that never returns valid structured output: router defaults to knowledge and the verifier is skipped; the guard fails closed on the uncertain band; escalation still files a ticket |
| Integration | 3 | Postgres with pgvector | migrations apply; ingestion is idempotent and quarantines; the hybrid SQL returns the right document with both legs populated; a two-turn conversation persists a transcript and a four-message checkpoint, including an order lookup through the real repository |

Thirty-three tests, about four seconds for the unit suite, about three for the integration suite. CI runs both plus the offline eval on every push.

## Gaps I am aware of

No tests against live Claude, Voyage or ElevenLabs in this build, so provider-specific behaviour (structured-output edge cases, rate limits) is covered by the design, not by a test; the next step is a nightly job with keys. No load test. No contract test for the Pinecone adapter beyond the interface. The scripted model can drift from real model behaviour; the real-model eval run is the check against that.

### Likely interviewer probes

- *Is the scripted model a mock?* It is a fake, not a mock: it has behaviour (reads documents, follows feedback, calls tools, streams) and no expectations. Mocks would couple tests to call sequences; the fake lets tests assert on outcomes.
- *How do you test prompts?* With the eval harness, not unit tests: prompts are measured, not asserted.
- *Why integration tests against a real Postgres rather than SQLite?* pgvector, generated tsvector columns, GIN and HNSW indexes and `websearch_to_tsquery` do not exist in SQLite; the SQL is the thing under test.
