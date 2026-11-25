# 10. Evaluation

Without measurement every change to a prompt, a chunk size or a model is a guess. Harbor's eval harness runs on every change (offline in CI) and with real models on demand, and writes a report with the prompt hashes it measured.

## The data

**Golden set** (`golden.jsonl`, 18 items): 15 policy questions with a reference answer written from the documents and the expected source document, one order question with a verified email, one escalation, one greeting. **Attack set** (`attacks.jsonl`, 14 items): 11 attacks and 3 benign controls (chapter 8). Both are small on purpose: they are regression tests, and each is cheap enough to run on every change. Growing them from real transcripts (failed turns, thumbs-down, escalations) is the first thing I would do in production.

## The metrics

| Metric | Type | Definition | What a drop means |
|---|---|---|---|
| Routing accuracy | deterministic | share of golden items where the router's intent equals the expected intent | router prompt or model regression |
| hit@3, hit@6 | deterministic | expected source document appears in the top 3 (6) retrieved chunks' sources | chunking, embedding, fusion or reranker regression |
| MRR | deterministic | mean of 1 / rank of the first chunk from the expected document | same, more sensitive to ordering |
| Judge pass rate, correctness, groundedness, completeness, tone | LLM-as-judge | Sonnet scores the answer 1 to 5 per criterion against the reference and the retrieved documents; pass if correctness and groundedness are both at least 4 | answer prompt, model, or retrieval regression; the rationale says which |
| Verifier retries | deterministic | turns that needed a second draft | first-draft quality (or verifier strictness) |
| Ragas faithfulness | LLM | share of claims in the answer supported by the retrieved contexts | hallucination |
| Ragas answer relevancy | LLM + embeddings | how well the answer addresses the question (generated questions compared to the original) | evasive or padded answers |
| Ragas context precision | LLM | share of retrieved contexts that are relevant, rank weighted | retrieval noise |
| Ragas context recall | LLM | share of the reference answer's claims that the contexts cover | retrieval misses |
| Block rate, leak rate, benign false-positive rate | deterministic | red team (chapter 8) | guard regression either way |
| p50 and mean latency | deterministic | per turn | performance regression |

## LLM-as-judge versus the in-graph verifier

They are cousins with different jobs. The verifier runs online, per answer, and only asks "is every claim supported by these documents"; it is cheap and its output changes the answer. The judge runs offline, has a reference answer the verifier never sees, scores four criteria, and never touches production traffic. Using a different model for judging than for writing (Sonnet judges Opus) reduces self-preference bias.

## Ragas integration

Ragas 0.4 metrics take an instructor-backed LLM and an embedding object. The harness builds the LLM from the same Anthropic client and the verifier model, using instructor's Anthropic JSON mode (not tools mode, which would need forced tool choice), and adapts Harbor's embedding provider to the Ragas base class, so the evaluation uses the production embeddings. Each sample is scored per metric with its own try/except so one failing metric does not sink the run. Ragas needs real models and is therefore skipped in offline mode and marked as such in the report.

## Reports

`harbor eval [--offline] [--ragas] [--limit N]` writes `summary.json`, `golden_rows.jsonl`, `redteam_rows.jsonl` and `report.md` to `evals/reports/<timestamp>/`, with the mode, model ids and prompt hashes. CI uploads the offline report as a build artifact. The offline run on the bundled set gives routing 1.00, hit@3 0.93, hit@6 0.93, MRR 0.87, judge pass 1.00, retries 0, attacks blocked 11 of 11, leaks 0, benign false positives 0, about 15 ms per turn. The one retrieval miss ("When will I get my refund after returning something?") ranks the payments document above the returns document in the offline bag-of-words index; it is listed in the report rather than hidden.

## What the offline numbers do and do not mean

The scripted model is deterministic and obeys the prompts by construction, so judge scores in offline mode measure the plumbing (citations present, retry loop working, routing rules honoured), not language quality. The retrieval numbers in offline mode come from hashed bag-of-words vectors plus BM25, a weaker retriever than Voyage plus a cross-encoder. The honest statement is: offline proves the harness and catches regressions in logic; real-model runs produce the quality numbers, and this build was not run with live keys.

## The improvement loop

Change one thing (a prompt rule, top_k, the reranker, the chunk target), run `harbor eval --ragas`, compare `summary.json` with the previous run, keep the change only if the metrics it targets moved and the others did not drop. The prompt hash in the report ties each number to its prompt text.

### Likely interviewer probes

- *How big should a golden set be?* Big enough that a regression moves the metric beyond noise: for a pass rate, roughly 50 to 100 items; the current 18 is a seed that grows from production failures.
- *How do you validate the judge?* Spot-check its rationales against human labels on a sample and compute agreement; if it disagrees with humans on a pattern, fix the rubric. The judge rubric is a prompt file with a hash like any other.
- *What is the difference between context precision and hit@k?* hit@k is a binary, document-level, label-based measure I can compute for free; context precision is chunk-level, rank-weighted and judged by a model. They should move together; when they do not, the labels or the judge need a look.
- *Why run evals in CI if they are offline?* To catch logic regressions (a routing rule, the guard thresholds, the citation filter) on every pull request without paying for model calls; the real-model run is a manual gate before a prompt or model change ships.
- *What would you measure in production?* Thumbs feedback, escalation rate, verifier retry rate, guard block rate, latency percentiles, and cost per turn from token usage, all per prompt hash.
