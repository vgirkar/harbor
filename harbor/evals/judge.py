"""LLM-as-judge with a fixed rubric and structured output. Used offline (evals), while the in-graph verifier
is the online cousin that only checks groundedness."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from harbor.agents.models import ModelFactory
from harbor.agents.schemas import JudgeScores
from harbor.prompts import load_prompt


async def judge_answer(
    models: ModelFactory, *, question: str, reference: str, contexts: list[str], answer: str
) -> JudgeScores:
    docs = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts)) or "(none)"
    human = (
        f"Question: {question}\n\nReference answer: {reference}\n\nRetrieved documents:\n{docs}\n\n"
        f"Assistant answer: {answer}"
    )
    return await models.structured("judge", JudgeScores).ainvoke(
        [SystemMessage(load_prompt("judge")), HumanMessage(human)]
    )
