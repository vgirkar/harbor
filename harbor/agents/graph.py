"""Graph assembly.

START -> guard -> (blocked | router) -> {knowledge -> verifier -> (knowledge | finalize), order, escalate,
smalltalk, blocked} -> finalize -> END

The verifier edge is the self-correction loop: at most MAX_ANSWER_ATTEMPTS drafts, then we ship the best we have
(the output guard still strips invalid citations and secrets).
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from harbor.agents.nodes import Services, build_nodes
from harbor.agents.state import AgentState


def build_graph(services: Services, checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    nodes = build_nodes(services)
    g: StateGraph = StateGraph(AgentState)
    for name, fn in nodes.items():
        g.add_node(name, fn)

    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", lambda st: "blocked" if st.get("route") == "blocked" else "router")
    g.add_conditional_edges(
        "router",
        lambda st: st.get("route") or "knowledge",
        {"knowledge": "knowledge", "order": "order", "escalate": "escalate", "smalltalk": "smalltalk"},
    )
    g.add_edge("knowledge", "verifier")

    max_attempts = services.settings.max_answer_attempts

    def after_verify(st: AgentState) -> str:
        grounded = (st.get("verdict") or {}).get("grounded", True)
        return "knowledge" if (not grounded and st.get("attempts", 0) < max_attempts) else "finalize"

    g.add_conditional_edges("verifier", after_verify, {"knowledge": "knowledge", "finalize": "finalize"})
    for leaf in ("order", "escalate", "smalltalk", "blocked"):
        g.add_edge(leaf, "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
