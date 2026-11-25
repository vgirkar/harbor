# 15. Numbers to remember and glossary

## Numbers

| What | Value |
|---|---|
| Code | about 4,900 lines of Python, 9 packages, 10 prompts, 33 tests (30 unit, 3 integration) |
| Knowledge base | 8 documents, about 3,100 words, 42 chunks, about 97 tokens per chunk on average |
| Chunking | H1 to H3 splits, 450 token target, 60 token overlap, 4 characters per token |
| Embeddings | voyage-3.5, 1,024 dimensions, or local bge-small, 384 dimensions; batches of 32 at ingestion |
| Indexes | HNSW cosine m = 16, ef_construction = 64; GIN on a generated English tsvector |
| Retrieval | dense 20 + lexical 20, RRF k = 60, 24 candidates reranked to 6, gate 0.15 |
| Guard | 19 rules; hard block at 0.9; classifier band 0.3 to 0.9; classifier block at confidence 0.7; quarantine at 0.5 |
| Graph | 9 nodes; at most 2 drafts; at most 3 tool rounds; history window 8 (writer), 4 (router) |
| Models | Opus 5.5 medium effort 1,500 tokens; Sonnet 5.5 low effort 1,024; Haiku 4.5 512 |
| Cost per policy turn | about 2 cents at list prices, estimated, dominated by Opus input; prompt caching is the first saving |
| Latency per policy turn | 4 to 9 s with hosted providers, estimated; retrieval 300 to 600 ms of that; offline 15 ms; measured 114 s with Ollama qwen2.5:3b on a 2019 Intel laptop CPU (two drafts), 29 s for an order turn, 89 ms for a heuristic block |
| Eval sets | 18 golden (15 knowledge, 1 order, 1 escalate, 1 smalltalk); 14 red team (11 attacks, 3 benign) |
| Offline results | routing 1.00, hit@3 0.93, hit@6 0.93, MRR 0.87, judge pass 1.00, blocked 11/11, leaks 0, benign FP 0 |
| API | 9 routes; 4,000 character message limit; 10 MB audio limit; 60 requests per minute per key by default |
| Image and CI | 1.38 GB image with eval extras, 96 s build; CI green in about 3 minutes with a pgvector service |
| Hosting | demo page on Netlify (harbor-support.netlify.app); API on a container host; free recipe: HF Spaces or Render + Neon/Supabase + Groq |
| Versions | langgraph 1.2.24, langchain 1.5.2, langchain-anthropic 1.7.12, ragas 0.4.4, pgvector 0.5.1, psycopg 3.3.9, fastapi 0.165, Postgres 17 |

## Glossary

| Term | Meaning in Harbor |
|---|---|
| RAG | Retrieval-Augmented Generation: retrieve relevant text, give it to the model, require the answer to be grounded in it |
| Dense retrieval | Similarity between the query embedding and chunk embeddings (cosine) |
| Lexical retrieval | Full-text search on stemmed tokens (Postgres tsvector, ts_rank_cd) |
| Hybrid retrieval | Both of the above, fused |
| RRF | Reciprocal Rank Fusion, score = sum of 1/(k + rank) over result lists |
| HNSW | Hierarchical Navigable Small World, a graph index for approximate nearest-neighbour search |
| Cross-encoder | A model that scores a query and a passage together; used as the reranker |
| Bi-encoder | A model that embeds query and passage separately; the embedding model |
| Breadcrumb | "Document > Section" prefix added to a chunk's indexed text for context |
| Spotlighting | Marking untrusted text with delimiters and a rule that it is data, after sanitising the delimiters |
| Canary | A secret string in the prompt whose appearance in output proves a leak |
| Relevance gate | Minimum reranker score for a chunk to be shown to the model |
| Structured output | Model response constrained to a JSON schema (Pydantic model) |
| Checkpoint | LangGraph's persisted graph state per thread |
| Thread | One conversation; `thread_id` equals the conversation id |
| Verifier | Online groundedness check of a draft against its documents |
| LLM-as-judge | Offline scoring of answers against a reference with a rubric |
| Faithfulness | Share of answer claims supported by retrieved contexts (Ragas) |
| Context precision / recall | Relevance of what was retrieved / coverage of what was needed (Ragas) |
| hit@k, MRR | Expected document within the top k / mean reciprocal rank of its first appearance |
| Token bucket | Rate limiter that refills capacity at a fixed rate and spends one token per request |
| Idempotent ingestion | Re-running ingestion changes nothing unless a file's hash changed |
