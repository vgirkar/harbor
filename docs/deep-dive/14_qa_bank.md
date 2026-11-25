# 14. Interview question bank

Short answers to say out loud. Each one is backed by a chapter above.

## System design

**Walk me through what happens when a customer asks "How long do I have to return an item?"**
The API checks the key and rate limit and assigns a request id. The guard scores the text (zero, nothing suspicious). The router, Claude Haiku with a structured output, returns knowledge with high confidence. The knowledge agent embeds the question with Voyage, runs dense search through pgvector's HNSW index and full-text search through Postgres in the same database, fuses the two lists with Reciprocal Rank Fusion, hydrates 24 candidates, reranks them with Voyage's cross-encoder to the top six, drops anything under the relevance gate, and renders them as `<document id="c1">` blocks. Claude Opus writes an answer citing c1 and c2. Sonnet verifies every claim against the same documents and passes it. The finalizer validates the citations, maps them to document sections, appends the message to the thread, and the transcript and checkpoint are written. The response carries the answer, the citations, the guard record, the verdict and the trace.

**Why LangGraph?** Explicit, inspectable control flow with conditional edges, persistence to Postgres per thread out of the box, and node-level event streaming. The nodes are plain async functions so the framework surface is small.

**Why not just a ReAct agent with a search tool?** Because I need guarantees a prompt cannot give: the guard always runs first, every policy answer is verified, the tool loop is bounded, and the path is logged. A graph makes those structural.

**How do you handle conversation memory?** The thread id is the conversation id; LangGraph checkpoints the `messages` list in Postgres after every node. The writer sees the last eight messages, the router the last four. Transcripts also go to a `messages` table for analytics.

## Retrieval

**Explain hybrid retrieval and why you need it.** Dense search understands paraphrase but misses exact tokens; full-text search is the reverse. I run both (top 20 each), fuse by rank with RRF, and rerank the top 24 with a cross-encoder to six. Agreement between the two retrievers is strong evidence, and the reranker restores precision.

**What is RRF?** Each list contributes 1/(k + rank) per document, summed. k = 60. It is rank based so I never have to put cosine similarity and ts_rank on one scale.

**Why rerank at all?** The embedding model scores query and passage separately; a cross-encoder reads them together and catches conditions and negations. It is expensive per pair, so it runs on 24 candidates, not the corpus.

**pgvector versus Pinecone?** pgvector by default for transactional consistency and operational simplicity; Pinecone as an adapter behind the same interface for scale-out, with Postgres still the system of record and the lexical index.

**HNSW versus IVFFlat?** HNSW needs no training, handles inserts well, better recall at equal latency; IVFFlat wins only on build time and memory at very large scale.

**How do you chunk?** By headings first so a chunk never crosses two policies, then a recursive splitter targeting 450 tokens with 60 overlap, and a breadcrumb prefix in the embedded text for context.

**How does the model say "I don't know"?** The reranker score gates the documents; with nothing above the gate, the prompt gets an empty block and rule 3 tells the model to say so and offer a ticket; the verifier is skipped.

## Agents and prompts

**How do you get structured output from Claude?** Pydantic schemas through LangChain's `with_structured_output`. On Opus 5.5 and Sonnet 5.5 I use the native `json_schema` method because those models reject forced tool choice; on Haiku 4.5 I use forced tool calling. The choice is one function keyed on the model id.

**How does the verifier loop work?** Sonnet gets the question, the documents and the draft and returns grounded, score, unsupported claims and a fix instruction. If not grounded and attempts are below two, the draft goes back to the writer with the claims quoted. Second drafts ship regardless; retries are counted in evals.

**How is authorization handled for orders?** The verified email comes from the API caller, is bound into the tool closure, and the SQL joins on it. The model cannot choose whose orders it reads. Never in the prompt.

**How do you version prompts?** Markdown files in git, content-hashed, hash logged on every use and written into eval reports.

**Which model does what and why?** Opus 5.5 writes customer-facing text, Sonnet 5.5 verifies and judges, Haiku 4.5 routes, classifies injections, summarises tickets and can rerank. Cost follows the value of each call.

## Safety

**How do you defend against prompt injection?** Five layers: heuristic scoring, an LLM classifier on the uncertain band, quarantine of poisoned chunks at ingestion, spotlighting of untrusted content with delimiter sanitising, and an output guard with secret redaction, citation validation and a canary. Plus authorization in code and read-only tools. Measured by a red-team suite with block rate, leak rate and benign false positives.

**What is the canary?** A string in the system prompt that must never appear in an answer. If it does, the guard replaces the answer and the leak is counted. It converts an invisible failure into a metric.

**What about a document that contains instructions?** Scored at ingestion; above 0.5 it is quarantined and excluded in SQL. If it still gets in, it is wrapped as data and the prompt's first rule says to ignore instructions inside documents.

## Evaluation

**How do you know it works?** A golden set with routing accuracy, hit@k and MRR, an LLM-as-judge rubric with a pass gate, Ragas faithfulness, answer relevancy, context precision and recall, and the red team. Reports with prompt hashes per run; offline mode in CI, real models on demand.

**Difference between the verifier and the judge?** The verifier runs online per answer and checks groundedness only; the judge runs offline with a reference answer and four criteria, on a different model than the writer.

**What are Ragas faithfulness and context recall?** Faithfulness: the share of claims in the answer supported by the retrieved contexts. Context recall: the share of the reference answer's claims that the retrieved contexts cover. One measures hallucination, the other retrieval misses.

**What did the offline run show?** Routing 1.00, hit@3 0.93, MRR 0.87, judge pass 1.00, 11 of 11 attacks blocked, no leaks, no benign false positives. And I say what that does and does not mean.

## Operations

**How is it deployed?** Multi-stage Docker image, non-root, health check, migrations on start; compose with a pgvector Postgres; GitHub Actions with lint, unit, integration against a pgvector service, offline eval artifact, image build.

**What breaks at scale and what do you do?** The in-process rate limiter (Redis), the single Postgres (read replicas, then Pinecone and OpenSearch), cost (prompt caching, Sonnet on simple routes), ingestion (queue worker).

**What would you add before launch?** PII redaction, prompt caching, alerting on block and retry rates, retention, a staging real-model eval gate.

## Behavioural angles

**What was the hardest decision?** Shipping the second draft when the verifier still objects. Refusing is safer for the system and worse for the customer; I chose the customer and made the retry count a metric so the trade-off is visible.

**What would you do differently?** Start from a larger golden set built from real transcripts, and run the real-model eval before tuning any retrieval parameter; in this build the offline harness came first because keys were not available.

**What did you learn about the current Claude models?** Forced tool choice is gone on the 5.5 models, thinking is always on with effort as the knob, and structured outputs are the right way to get JSON. All of that is isolated in the model factory so the next upgrade is a one-file change.
