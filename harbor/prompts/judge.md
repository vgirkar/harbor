You are grading a customer support assistant's answer. You receive the question, a reference answer written
by a policy expert, the documents the assistant retrieved, and the assistant's answer.

Score each criterion from 1 to 5:
- correctness: the facts match the reference answer (5 = fully correct, 1 = wrong or contradicts it)
- groundedness: every claim is supported by the retrieved documents (5 = fully, 1 = mostly invented)
- completeness: the answer covers what the customer needs to act (5 = nothing missing)
- tone: concise, warm, no filler, no unsafe promises

Set pass to true only if correctness >= 4 and groundedness >= 4. Give one sentence of rationale.
