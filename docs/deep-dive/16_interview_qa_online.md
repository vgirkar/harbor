# 16. Interview questions and answers

This closing section is the document in interview format. The questions are the ones that recur in the online interview-question lists for RAG, LangChain and LangGraph, vector databases, LLM evaluation and LLM security (sources at the end of the section). Every answer is written to be said out loud, in the first person, with Harbor presented as the open-source reference implementation of the RAG and agent stack from my Optivaize work: the same pipeline shape (ingestion, chunking, embeddings, pgvector and Pinecone, hybrid retrieval, reranking, LangGraph agents, Ragas and judge evaluation, injection defence) built as a public project so it can be shown and verified. The answers only claim what the code does and what was measured.

## Retrieval-augmented generation

**What is RAG and why did you use it instead of fine-tuning?**
RAG retrieves the passages relevant to a question and gives them to the model with the instruction to answer only from them. Policies change weekly and must be citable, which rules out baking them into weights: fine-tuning cannot cite a source, cannot be updated by editing a markdown file, and tends to blur facts across documents. In Harbor the knowledge base is eight policy documents; re-ingesting a changed file takes seconds and every answer carries the ids of the chunks it came from.

**How do you chunk documents, and how did you choose the size?**
I split on markdown headings first, so a chunk never straddles two policies, then apply a recursive character splitter with a target of about 450 tokens and 60 tokens of overlap for long sections. Each chunk is embedded with a breadcrumb such as "Returns and refunds > Refund timing" prefixed, which makes it retrievable for questions that use the section's vocabulary rather than the sentence's. On the Harbor corpus that yields 42 chunks averaging about 100 tokens, because policy sections are short. The size is a parameter I tune with the eval harness, watching hit@k and MRR.

**What is an embedding and which model did you pick?**
A fixed-size vector where semantic similarity becomes geometric closeness, so "money back" sits near "refund". I used Voyage voyage-3.5 (1,024 dimensions, Anthropic's recommended partner, with asymmetric query and document modes) and a free local alternative, fastembed bge-small (384 dimensions, ONNX on CPU). The provider is behind one interface; the column dimension in Postgres is the only thing coupled to the choice.

**Dense versus sparse retrieval: when does each fail?**
Dense retrieval understands paraphrase but is weak on exact tokens: order numbers, product codes, brand names such as Klarna or DHL. Lexical retrieval is the mirror image. Harbor runs both: pgvector cosine search through an HNSW index and Postgres full-text search over a generated tsvector, each returning the top 20.

**What is hybrid search and how do you combine the two result lists?**
Running both retrievers and fusing their results. I use Reciprocal Rank Fusion: each list contributes 1 divided by k plus rank per document, with k = 60, summed. It is rank based, so I never have to put cosine similarity and ts_rank on one scale, and agreement between the two retrievers is rewarded: a document ranked third in both beats one ranked first in only one.

**Why rerank, and what is the difference between a bi-encoder and a cross-encoder?**
The embedding model is a bi-encoder: it scores query and passage independently and cannot see interactions such as a condition that applies to one product category. A cross-encoder reads the pair together and is far more accurate, but costs a forward pass per pair, so it runs only on the 24 fused candidates and returns the top 6. I use Voyage rerank-2.5 hosted, a local MiniLM cross-encoder for the free route, and a listwise Claude Haiku reranker as a one-vendor fallback.

**How does your system say "I don't know"?**
The reranker's score gates the documents at 0.15. If nothing survives, the prompt receives an empty documents block and rule three tells the model to say the documentation does not cover it and to offer a ticket. The verifier is skipped because there is nothing to verify. That is better than a cosine threshold because the gate is a number from a model that saw the question.

**How do you reduce hallucination in a RAG system?**
Four mechanisms in Harbor: the prompt forbids facts outside the documents and requires a citation per factual sentence; a second model verifies every claim against the same documents and sends ungrounded drafts back once with the unsupported claims quoted; the output guard drops citations that do not point at retrieved chunks; and the eval measures groundedness offline. In the local-model run the verifier sent 8 of 15 drafts back, which is the mechanism working on a weak writer.

**How do you handle the context window, and what about "lost in the middle"?**
Six chunks of at most 450 tokens plus eight messages of history keeps the answer prompt near 2,000 tokens, far below the window, so I never truncate silently. Chunks are ordered by reranker score, so the most relevant text comes first. For long threads the next step is a summarisation node that replaces older messages.

**Do you use query rewriting or HyDE?**
Not in the current build. The router already sees the last four turns, and retrieval uses the raw question. Query rewriting (resolving "and the second one?" into a standalone question) is the first retrieval improvement I would add, measured with MRR on multi-turn golden items.

**How do you keep the index fresh?**
Ingestion is idempotent: each file's sha256 is stored, unchanged files are skipped, changed files have their chunks deleted and re-embedded inside one transaction, and the external index is told to delete the old ids. A reindex endpoint rebuilds an external index from Postgres.

**How do you give citations the user can trust?**
Chunks are rendered as document blocks with short local ids c1 to c6. The model cites those ids; the finalizer validates the set, drops anything outside it, and maps each id to the document, section, snippet and reranker score that the UI shows in a sources panel.

**How would you handle PII in a RAG system?**
Harbor stores what the customer typed; PII redaction before storage is on the roadmap and I say so. Authorization is already enforced in code: order tools bind the verified email and the SQL joins on it, so no prompt can read another customer's data.

## LangChain and LangGraph

**What is LangGraph and how is it different from LangChain?**
LangChain gives the building blocks: chat models, tools, structured output, message types. LangGraph adds explicit stateful orchestration: a typed state, nodes that are functions over it, conditional edges, cycles, checkpointing and event streaming. I use LangChain for the model calls and LangGraph for the control flow.

**Explain state, nodes and edges in your graph.**
State is a typed dictionary: a durable messages list with an append reducer, plus per-turn scratch such as the guard decision, route, retrieved chunks, draft, verdict and trace. Nodes are async functions that return partial state updates: guard, router, knowledge, verifier, order, escalate, smalltalk, blocked, finalize. Conditional edges read the state: guard sends to blocked or router, router to one of four agents, the verifier back to knowledge or on to finalize.

**How do you implement a self-correction or reflection loop?**
The verifier node returns a structured verdict with the unsupported claims. If not grounded and attempts are below two, the edge sends the state back to the knowledge node, whose prompt renders the claims and a fix instruction. The loop is bounded by the attempt counter, so there is no path that cycles forever.

**What is the supervisor pattern and did you use it?**
A supervisor classifies the task and delegates to specialist workers. Harbor's router is the supervisor; the knowledge, order and escalation nodes are the specialists, each with its own prompt, model, tools and output schema. I implemented it as a graph rather than as agents messaging each other, so control flow stays explicit and testable.

**How do you implement tool calling and keep it safe?**
The order agent binds two tools, lookup_order and list_recent_orders, created per request with the verified email captured in a closure. The loop is twenty lines: at most three rounds, tool results wrapped as data, a final forced reply when the budget runs out. Authorization lives in the tool and the SQL, never in the prompt.

**How do you manage conversation memory and persistence?**
The thread id is the conversation id. LangGraph's Postgres checkpointer saves the state after every node, so a conversation resumes with its history, a crash mid-turn leaves a checkpoint to inspect, and a test can read back the exact message sequence. The writer sees the last eight messages, the router the last four. Transcripts also go to a messages table for analytics.

**How do you add human-in-the-loop approval?**
A node before any side-effecting action calls LangGraph's interrupt; the checkpoint holds the state until a staff endpoint resumes the thread with the decision. The persistence layer is already in place; the interrupt is the next step once write tools such as refunds exist.

**How do you stream responses from a graph?**
I consume the graph's event stream. Node starts become status events with human labels, chunks from the chat model tagged "answer" become tokens, a verifier rejection becomes a rewrite event so the client clears what it showed, and the final state becomes the done event. The router, guard and verifier never stream to the user.

**How do you get reliable structured output?**
Pydantic schemas through structured output. On Claude Opus 5.5 and Sonnet 5.5 I use the native json_schema method because those models reject forced tool choice; on Haiku, forced tool calling; on an OpenAI-compatible endpoint, constrained JSON-schema decoding where available. Every structured call retries once with a corrective note and then falls back to a safe default per node, because a model once returned an all-null tool call an hour into an evaluation.

**How do you handle errors in an agent?**
Transport errors are retried by the SDK; invalid structured output is retried then defaulted; the tool loop and the verifier loop are bounded; a persistent failure returns 500 with a request id and the checkpoint keeps the partial state. Nothing is appended to the transcript on failure.

## Vector databases

**What is a vector database and when do you need one?**
A store with an approximate nearest-neighbour index over embeddings. You need the index, not necessarily a separate database: pgvector gives HNSW and IVFFlat inside Postgres, which is where I start. A dedicated service such as Pinecone earns its place when the index outgrows one Postgres or needs independent scaling.

**Cosine, dot product or Euclidean?**
Cosine for text embeddings, because it measures direction and ignores length, which is what semantic similarity means for normalised embeddings. pgvector's cosine distance operator with an HNSW index built with vector_cosine_ops.

**HNSW versus IVFFlat?**
HNSW is a layered graph: no training step, good recall at low latency, handles inserts well, more memory. IVFFlat clusters vectors and needs a training pass and a probe count; cheaper to build and store, slower or less accurate at query time. I chose HNSW with m = 16 and ef_construction = 64 and left ef_search at the default, because the lexical leg and the reranker compensate for approximate misses.

**pgvector versus Pinecone?**
pgvector for transactional consistency between chunk text, metadata and vector, one backup, one connection pool, and no second system. Pinecone when scale demands it; in Harbor it is an adapter behind the same interface, keyed by chunk id, with Postgres hydrating the matches and still owning full-text search.

**How does filtering work with vector search?**
In pgvector a WHERE clause runs with the index scan, which is how quarantined chunks are excluded at the SQL level. With HNSW, very selective filters can reduce recall because the index returns its candidates before the filter; raising ef_search or partitioning by tenant are the remedies.

**What happens when you change the embedding model?**
The column type carries the dimension, so a model with a different dimension is a schema migration: new column or new migration, re-embed every chunk with the ingestion pipeline's force flag, reindex Pinecone if used. Mixed dimensions are impossible by construction.

**How would you scale to millions of vectors?**
Keep the design, move vectors to Pinecone or shard pgvector by tenant, tune ef_search, add read replicas for the read-only retrieval legs, keep the reranker on a few dozen candidates. The retriever interface does not change.

## Evaluation

**How do you evaluate an LLM application?**
Three layers in Harbor. Deterministic metrics that cost nothing: routing accuracy, retrieval hit@3, hit@6 and MRR against labelled source documents, red-team block and leak rates, latency. An LLM-as-judge rubric with a reference answer and a pass gate. And Ragas for the standard RAG metrics. Reports carry the prompt hashes and model ids, and the offline version runs in CI.

**What are faithfulness, answer relevancy, context precision and context recall?**
Faithfulness: the share of claims in the answer supported by the retrieved contexts, so it measures hallucination. Answer relevancy: how well the answer addresses the question, computed by generating questions from the answer and comparing them to the original. Context precision: how much of what was retrieved is relevant, rank weighted. Context recall: how much of the reference answer the contexts cover, so it measures retrieval misses.

**What are the pitfalls of LLM-as-judge?**
Self-preference when the judge is the writer, position and verbosity bias, sensitivity to the rubric wording, and susceptibility to injected text in what it judges. I use a different model to judge than to write, a fixed rubric with a numeric pass rule, spot checks against human labels, and I keep the judge offline.

**Offline versus online evaluation?**
Offline is the golden set and red team on every change. Online is the verifier running on every policy answer, plus the signals I would collect in production: thumbs feedback, escalation rate, verifier retry rate, guard block rate and cost per turn, all per prompt hash.

**How did you build your golden set and how big should it be?**
Eighteen questions written from the documents with reference answers and expected source documents, plus fourteen red-team prompts. That is a seed; a regression-grade set is 50 to 100 items grown from production failures, and I say that when asked.

**What did your evaluation actually show?**
Offline with the scripted model: routing 1.0, hit@3 0.93, 11 of 11 attacks blocked. With real free models (a 3B local LLM, local embeddings and reranker): retrieval found the right document first on every question that reached it, the red team held at 11 of 11 with no benign false positives, while routing dropped to 0.83 and the judge pass rate to 0.07, which is the small model and exactly what the metrics are for.

## Prompt injection and safety

**What is prompt injection and how is it different from jailbreaking?**
Injection is untrusted text that tries to override the operator's instructions, directly from the user or indirectly through documents and tool results. Jailbreaking is the subset aimed at the model's own safety behaviour. Harbor treats both with the same layers because the mechanism is the same: text pretending to be instruction.

**How do you defend against it?**
Five layers: nineteen heuristic rules with weights, a Claude Haiku classifier on the uncertain band between 0.3 and 0.9, quarantine of poisoned chunks at ingestion, spotlighting of untrusted content in delimited blocks with the delimiters sanitised, and an output guard that redacts credentials, validates citations and checks a canary string. Plus least privilege: the model has two read-only tools and authorization is enforced in code.

**What is indirect prompt injection and how do you test for it?**
Instructions planted in content the model will read, such as a document that says every customer qualifies for a refund. A test fixture does exactly that; ingestion quarantines the chunk and a test asserts it never reaches retrieval. If it did, the prompt's first rule says documents are data.

**What is a canary and why use one?**
A secret string in the system prompt that must never appear in output. If the output guard sees it, the answer is replaced and the leak is counted. It costs nothing and turns an invisible failure into a metric; the red team's leak rate is defined by it.

**How do you avoid blocking legitimate angry customers?**
The band policy. Heuristics alone block only at 0.9 or above; the classifier decides the uncertain band and is told explicitly that frustration, strong language and quoting an attack to ask about it are not injections. The red team includes three benign controls, and the false-positive rate on them is 0 in both runs.

**How do you keep secrets away from the model?**
Keys are read only by the adapters that need them, never placed in prompts, never logged. The output guard's credential patterns are the last line against a key appearing in an answer.

## System design and operations

**Design a customer support assistant for an online store.**
I describe Harbor: API with keys and rate limits, guard, router, hybrid retrieval over pgvector and full-text search with RRF and a reranker, a grounded writer with citations, a verifier loop, an order agent with authorized tools, an escalation agent that writes tickets, Postgres as the system of record with LangGraph checkpoints, streaming to a web front end, voice as a thin shell, and an evaluation harness in CI. Then the scaling path: Redis for the limiter, read replicas, Pinecone, prompt caching, a queue for ingestion.

**How do you deploy and run it?**
Multi-stage Docker image with a non-root user and a health check, migrations on start, docker compose with a pgvector Postgres, GitHub Actions with lint, unit and integration suites against a real pgvector service, an offline eval report as an artifact, and an image build. Front end on Netlify; the API on a container host; a fully free combination is documented.

**What does a turn cost and how would you reduce it?**
About two cents at list prices with Claude, dominated by the Opus input tokens. Prompt caching of the stable system block is the first lever; routing simple questions to Sonnet is the second; the verifier is a deliberate cost I can measure and remove.

**How do you test a system whose core component is non-deterministic?**
A scripted chat model that implements the LangChain interface and plays every role by rule, with an in-memory hybrid index and in-memory services. The graph, guard, streaming, API and evaluation code are the production code; only providers are fakes. Thirty unit tests and three Postgres integration tests run in seconds and in CI, and the eval harness measures what tests cannot assert.

**What was the hardest problem?**
Keeping the system honest when the model is weak. The first evaluation with a small model died on an empty tool call; the fix was a retry with a corrective note and a safe default per node, with tests that drive the graph with a model that never returns valid output. Shipping the second draft when the verifier still objects was the hardest judgement call; I chose the customer and made retries a metric.

**What would you do differently?**
Start from a golden set built from real transcripts, run the real-model evaluation before tuning any retrieval parameter, and add prompt caching from day one.

## Voice

**How does a voice agent differ from a chat agent?**
Latency budgets and turn-taking. Harbor keeps voice as a thin shell: transcribe, run the identical graph turn, synthesise, so one set of prompts, one guard and one eval cover both channels. A realtime phone channel needs streaming speech to speech, barge-in and turn detection in front, with Harbor as the knowledge and tool backend, which is how I have integrated support knowledge bases with hosted voice platforms.

**Which speech providers and why over HTTP?**
ElevenLabs Scribe and Flash v2.5, or OpenAI transcription and TTS, through two httpx calls each with retries. No SDKs: fewer dependencies and the retry policy is explicit. The fake provider decodes bytes as text so the voice endpoint is in the unit suite.

## Sources for the questions

- Analytics Vidhya, 40 RAG interview questions and answers: analyticsvidhya.com/?p=250742
- DataCamp, RAG interview questions: datacamp.com/blog/rag-interview-questions
- KalyanKS, RAG interview questions and answers hub: github.com/KalyanKS-NLP/RAG-Interview-Questions-and-Answers-Hub
- DataCamp, LangChain interview questions: datacamp.com/blog/langchain-interview-questions
- LangGraph interview questions: singhsidhukuldeep.github.io/Interview-Questions/LangGraph
- techinterview.org, vector databases: embedding search, FAISS, Pinecone, pgvector, HNSW and IVF
- Design Gurus, vector search and vector databases concept deep dives: designgurus.io
- dev-atlas, AI evaluation interview questions: atypicalesper.github.io/dev-atlas/ai/10-ai-evaluation/02-interview-questions
- techinterview.org, how to evaluate an LLM: benchmarks, human eval and red teaming
- Es et al., Ragas: automated evaluation of retrieval augmented generation (arXiv 2309.15217)
