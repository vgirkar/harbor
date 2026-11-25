# 5. Retrieval: hybrid search, fusion and reranking

This is the chapter interviewers dig into. The design is: two cheap recall-oriented searches that fail differently, a rank-based fusion that needs no score calibration, and one expensive precision-oriented reranker on a small candidate set, followed by a gate that lets the model say "I don't know".

## Why hybrid

Dense retrieval (embedding similarity) understands paraphrase: "money back" finds "refund". It is weak on exact tokens: order numbers, product codes, "Klarna", "DHL", rare words the embedding model saw little of. Lexical retrieval (full-text search) is the mirror image: exact and rare tokens are its strength, synonyms its weakness. Running both and fusing them is the cheapest way to get the union of their strengths; the reranker then restores precision on the merged set.

## The pipeline, with the actual parameters

| Step | What | Parameter | Why that value |
|---|---|---|---|
| 1 | Embed the question with the query input type | voyage-3.5, 1,024 dims | Asymmetric models embed queries and documents differently; using the right side matters |
| 2a | Dense search through HNSW, cosine, excluding quarantined | top 20 | Recall leg; cheap; 20 is enough candidates for a 42 to 10,000 chunk corpus |
| 2b | Lexical search, terms OR-ed, ts_rank_cd normalised | top 20 | Same reasoning |
| 3 | Reciprocal Rank Fusion | k = 60 | See below |
| 4 | Hydrate the top candidates from Postgres | max(4 × top_n, 12) = 24 | Cross-encoders are priced per passage; 24 is a good trade between recall and cost |
| 5 | Rerank | Voyage rerank-2.5 to top 6 | Six chunks of about 100 to 450 tokens fit comfortably in the prompt with history |
| 6 | Relevance gate | score at or below 0.15 dropped | A chunk the reranker scores that low is noise; if nothing survives, the model is told there are no documents and must say so |

## Reciprocal Rank Fusion

For each result list, a document at rank r contributes 1 / (k + r); the fused score is the sum over lists. With k = 60 the contributions are 1/61 for rank 1, 1/62 for rank 2, and so on, which is a gentle slope: a document ranked 3rd in both lists (2 × 1/63 = 0.0317) beats one ranked 1st in one list only (1/61 = 0.0164). That is the property I want: agreement between two different retrievers is strong evidence. k controls how much top ranks dominate; smaller k makes rank 1 worth much more than rank 2. The value 60 comes from the original paper (Cormack, Clarke and Buettcher, 2009) and is used by most search engines that offer hybrid search.

The alternative, normalising cosine similarity and ts_rank into one scale and taking a weighted sum, requires calibrating two scores with different distributions that shift when the corpus changes. RRF needs no calibration, has one parameter, and the per-source ranks are kept on every result for explainability (the API returns `dense_rank` and `lexical_rank` on retrieved chunks).

## Reranking

A bi-encoder (the embedding model) scores query and passage independently, so it cannot see interactions like negation or a condition that only applies to one product category. A cross-encoder reads them together and is far more accurate, but it costs a forward pass per pair, which is why it only runs on 24 candidates and not on the corpus. I use Voyage rerank-2.5 hosted. The `LLMReranker` alternative asks Claude Haiku to score all candidates 0 to 10 in one listwise call; it exists so the pipeline runs with one vendor fewer, and as an experiment arm in evals. `none` keeps the fused order and is the baseline every reranker has to beat.

The reranker's score is also what the relevance gate uses, which is the honest way to get "I don't know" behaviour: the gate is a number from a model that saw the question, not a cosine threshold tuned by hand.

## Contextual chunk headers

Each chunk is embedded as breadcrumb + content and indexed the same way (chapter 4). This is a cheap version of "contextual retrieval": the chunk carries the document and section it came from, so both legs can match on context the chunk text alone would miss.

## Failure modes I designed for

| Failure | Mitigation |
|---|---|
| Question outside the knowledge base | Relevance gate empties the document list; the prompt instructs the model to say so and offer a ticket; the verifier is skipped because there is nothing to verify |
| Right document, wrong chunk | Overlap of 60 tokens, breadcrumbs, and the reranker reading full passages |
| Exact tokens (order numbers, brand names) | Lexical leg |
| Paraphrase | Dense leg |
| Approximate index misses | Lexical leg and reranker compensate; `hnsw.ef_search` can be raised if evals show recall loss |
| Poisoned chunk | Quarantine at ingestion; excluded in SQL |
| Index and database out of sync (Pinecone) | Hydration by id drops ids Postgres no longer has; reindex endpoint rebuilds |

## Latency budget (estimated with live providers)

Embedding 100 to 200 ms, the two SQL queries 10 to 50 ms, hydration 5 ms, rerank 150 to 300 ms. Retrieval is therefore roughly 300 to 600 ms of a turn, with the Opus answer (2 to 5 s) dominating. These figures are reasoned from typical vendor latencies and the local SQL timings (the integration test logs 53 ms for both legs on a cold cache), not measured end to end with live keys in this build.

## How retrieval is measured

Every eval run computes hit@3, hit@6 and MRR against the expected source document per golden question, plus Ragas context precision and context recall when a real model is available. Changing any parameter in the table above is a one-line config change followed by `harbor eval`, which is the workflow I would use to tune top_k, the gate, or the chunk size.

### Likely interviewer probes

- *Why RRF and not a learned fusion?* No training data at this stage; RRF is the strong default. With click or thumbs data I would train a small reranker or learn fusion weights, measured against the same hit@k.
- *How do you choose k in RRF?* 60 is the published default; smaller k sharpens the top ranks. I would sweep it with the eval harness; in practice it rarely moves results much.
- *What would you do with a corpus of a million chunks?* Keep the design, move vectors to Pinecone or tune pgvector (partitioning by tenant, `ef_search`, more memory), move lexical to OpenSearch if ts_rank latency grows, keep the reranker on 24 to 50 candidates. The retriever interface does not change.
- *Why six chunks?* Enough to cover a question that spans two sections and show alternatives, small enough that the answer prompt stays near 2,000 tokens and the verifier can check every claim.
- *What is the difference between retrieval precision and faithfulness?* Precision is whether what we retrieved is relevant; faithfulness is whether what the model said is supported by what we retrieved. Both are measured; they fail independently.
- *When is the lexical leg useless?* When the query shares no stems with any chunk (pure paraphrase); the dense leg carries it. When both fail, the gate empties the context and the model declines.
