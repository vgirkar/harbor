# Harbor eval report, free local route (Ollama qwen2.5:3b) (2026-11-25T13:58:25+00:00)

Mode: postgres | prompts: {'knowledge_agent': '972c50f4cd', 'router': 'd8b112d9c2', 'verifier': 'e55d871391', 'injection_classifier': 'dabc237904'}

| Metric | Value |
|---|---|
| n | 18 |
| routing_accuracy | 0.8333 |
| retrieval_hit_at_3 | 0.8 |
| retrieval_hit_at_6 | 0.8 |
| retrieval_mrr | 0.7667 |
| judge_pass_rate | 0.0667 |
| judge_correctness | 3.6667 |
| judge_groundedness | 3.1333 |
| judge_completeness | 3.4667 |
| verifier_retries | 8 |
| p50_latency_ms | 57734 |
| ragas_faithfulness | 0.75 |
| ragas_answer_relevancy | 0.6087 |
| ragas_context_precision | 0.8972 |
| ragas_context_recall | 0.875 |

## Red team

| Metric | Value |
|---|---|
| attacks | 11 |
| block_rate | 1.0 |
| leak_rate | 0.0 |
| benign_false_positive_rate | 0.0 |
