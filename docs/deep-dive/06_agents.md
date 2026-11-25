# 6. The multi-agent workflow on LangGraph

## State

`AgentState` is a typed dictionary. `messages` is the durable conversation, with LangGraph's `add_messages` reducer so nodes append rather than overwrite; it is what the checkpointer persists per thread. Everything else is scratch for one turn: `user_input`, `guard` (the decision record), `route` and `route_confidence`, `retrieved` (serialized chunks), `draft`, `attempts`, `verdict`, `final_answer`, `citations`, `ticket_id`, and `trace` (a list reducer that accumulates node names). Keeping scratch out of the durable messages means the transcript the model sees in later turns is clean: human and assistant messages only, with no leaked tool payloads or documents.

## Nodes and edges

```
START -> guard -> (blocked | router)
router -> knowledge | order | escalate | smalltalk
knowledge -> verifier -> (knowledge if not grounded and attempts < 2, else finalize)
order | escalate | smalltalk | blocked -> finalize -> END
```

Every node is an async function `(state, config) -> partial state` built by `build_nodes(services)`, where `services` carries the model factory, retriever, order service and ticket service. That closure style is what makes the graph testable: tests inject in-memory services and the scripted model, and the node code is unchanged.

| Node | Model | Input it builds | Output |
|---|---|---|---|
| guard | Haiku (only on the uncertain band) | the user message | guard record, route = blocked or empty |
| router | Haiku, structured `RouteDecision` | last four turns + latest message | intent, confidence |
| knowledge | Opus 5.5, tagged `answer` for streaming | system prompt (with retry feedback if any), last 8 messages, `<documents>` block + question | draft, retrieved, attempts + 1 |
| verifier | Sonnet 5.5, structured `GroundingVerdict` | question, documents, draft | grounded, score, unsupported claims, fix instruction |
| order | Opus 5.5 with two tools | system prompt + verified email line, history, question; tool results spotlighted | draft |
| escalate | Haiku, structured `TicketSummary` | transcript | ticket row, draft with reference and SLA |
| smalltalk | Opus 5.5 | short system prompt, history | draft |
| blocked | none | | fixed refusal |
| finalize | none | draft, retrieved | final answer, citations, assistant message appended |

## The knowledge agent and the verifier loop

The knowledge node retrieves on the first attempt only; a retry reuses the same chunks, because the problem a retry fixes is the writing, not the search. The verifier receives the question, the same `<documents>` block and the draft and returns a structured verdict. If not grounded and attempts are below two, the edge sends the state back to knowledge with the verdict in it, and the knowledge prompt renders a feedback paragraph: the unsupported claims verbatim and the fix instruction. The second draft is shipped regardless of the verdict, because the output guard still strips invalid citations and the alternative, refusing to answer, is worse for the customer than a draft the verifier scored low on one claim. The attempt count and verdict are in the response, so the eval harness counts retries.

Why a separate model for verification: a model is a poor judge of its own draft in the same context, and Sonnet at low effort is cheaper than a second Opus call. Why not verify order and small talk answers: order answers come from tool results that are already data, small talk makes no policy claims.

## The order agent: a bounded tool loop

I wrote the loop rather than using a prebuilt agent so the behaviour is readable in twenty lines: at most three rounds; in each round the model may call tools; each tool result is wrapped in a `<tool_result>` block and appended; when the model answers without tool calls the loop ends; if the budget runs out, one last call asks it to reply with what it has. The two tools are created per request with the verified email bound into the closure, and the tool uses that email no matter what the model passes. The repository query joins on the email, so authorization is enforced twice, in code and in SQL, and never in the prompt. If no verified email is present, the prompt tells the model to ask for email and order number, and the query still requires both to match.

## When a model misbehaves

Every structured call goes through one helper that retries once with a corrective message ("your previous reply was not valid, fill in every field") and then raises a typed error. Each node catches that error and applies the safest default for its job: the router defaults to knowledge, the verifier lets the draft through and marks the verdict as skipped (the output guard still validates citations), the escalation node files the ticket with the raw message and normal priority, and the guard on the uncertain band falls closed, blocking at the heuristic threshold without the classifier. I added this after a live run with a 3B model returned a tool call with every field null one hour into an evaluation; a turn must never fail because a model produced bad JSON. Three unit tests drive the graph with a model that never returns valid output and assert those defaults.

## Memory and checkpointing

`thread_id` is the conversation id. The Postgres saver writes a checkpoint after every node, which gives three things: multi-turn memory (the router sees the last four turns, the writer the last eight), resumability after a crash mid-turn, and time travel for debugging (`graph.aget_state(config)` returns the full state at the last checkpoint; a test asserts that two turns produce exactly human, ai, human, ai in the thread). The offline container uses `MemorySaver` with the same interface.

## Streaming

`stream_turn` consumes `graph.astream_events`. Node starts become `status` events with a human label ("Searching the knowledge base"). Chat model chunks tagged `answer` become `token` events, so only the customer-facing model streams and the router, guard and verifier stay silent. If the verifier rejects a draft, a `rewrite` event tells the client to clear what it has shown; this is honest streaming rather than hiding the retry. The `done` event carries the full result. The demo page renders all four.

## Models, effort and cost per turn (estimated)

| Role | Model | Effort | Max tokens | Why |
|---|---|---|---|---|
| answer, order, smalltalk | claude-opus-5-5 | medium | 1,500 | The customer-facing text; strongest instruction following and grounding |
| verifier, judge | claude-sonnet-5-5 | low | 1,024 | Judgement task with a fixed rubric; low effort keeps it fast |
| router, guard, reranker, ticket summary | claude-haiku-4-5 | n/a | 512 | Classification at high volume; cheapest and fastest |

A policy turn is roughly: Haiku router 500 tokens in, Opus answer about 2,500 in and 250 out, Sonnet verifier about 2,000 in and 100 out, plus embedding and reranking. At list prices (Opus 4 and 20 dollars per million tokens, Sonnet 2 and 10, Haiku 1 and 5) that is about 2 cents per turn, dominated by the Opus input. Prompt caching of the system prompt would cut the Opus input cost by most of its share; the prompt is already laid out with the stable system text first and the volatile documents after it, which is the prerequisite.

### Likely interviewer probes

- *What makes this "multi-agent" rather than one agent with branches?* Each node has its own prompt, model, tools and output schema, and they hand each other typed state. The router is a supervisor that delegates to specialists; the verifier is a critic. That is the supervisor-worker pattern, implemented as a graph rather than as agents messaging each other, which keeps control flow explicit.
- *How do you stop infinite loops?* Two structural limits: `attempts < 2` on the verifier edge and three tool rounds in the order node. There is no path in the graph that can cycle without hitting one.
- *What happens on a model error mid-turn?* Transport errors are retried by the SDK; invalid structured output is retried once with a corrective note and then replaced by the node's safe default; a persistent transport failure raises, the API returns 500 with the request id, nothing is appended to the transcript, and the checkpoint holds the state up to the failed node for debugging.
- *How would you add human-in-the-loop approval for refunds?* A node before the action that calls LangGraph `interrupt()`; the checkpoint holds the state until a staff endpoint resumes the thread with the decision. The persistence is already there.
- *Why structured outputs everywhere?* Because free-text classification is a parsing bug waiting to happen. Every classifier returns a Pydantic model; on Opus and Sonnet 5.5 that uses native structured outputs, on Haiku forced tool calling.
- *How is the conversation history trimmed?* Window of eight messages for the writer and four for the router. For long threads the next step is a summarisation node that replaces older messages with a summary in state; the reducer makes that a local change.
