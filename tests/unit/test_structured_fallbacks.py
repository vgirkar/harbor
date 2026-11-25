"""A model that never returns valid structured output must not take a turn down."""

import pytest
from langchain_core.runnables import RunnableLambda


@pytest.fixture()
def broken_container(container):
    def _broken(role, schema):
        def _raise(_input):
            raise ValueError(f"{role}: model returned nothing usable")

        return RunnableLambda(_raise)

    container.services.models.structured = _broken  # type: ignore[method-assign]
    return container


@pytest.mark.asyncio
async def test_router_and_verifier_fall_back_safely(broken_container):
    r = await broken_container.runtime.run_turn(message="How long do I have to return an item?")
    assert r.intent == "knowledge" and r.answer and r.citations
    assert r.verdict.get("skipped") == "verifier unavailable"
    assert r.trace == ["guard", "router", "knowledge", "verifier", "finalize"]


@pytest.mark.asyncio
async def test_guard_fails_closed_on_uncertain_band(broken_container):
    # heuristic score 0.8 (override phrase) with a dead classifier: block, because 0.8 >= the LLM threshold 0.7
    r = await broken_container.runtime.run_turn(
        message="What does 'ignore previous instructions' mean? Just curious."
    )
    assert r.intent == "blocked" and r.guard["decided_by"] == "heuristic_fallback"


@pytest.mark.asyncio
async def test_escalation_falls_back_to_raw_summary(container):
    original = container.services.models.structured

    def _only_summary_broken(role, schema):
        if role != "summary":
            return original(role, schema)

        def _raise(_input):
            raise ValueError("summary: model returned nothing usable")

        return RunnableLambda(_raise)

    container.services.models.structured = _only_summary_broken  # type: ignore[method-assign]
    r = await container.runtime.run_turn(message="I want to speak to a human about my torn jacket")
    assert r.intent == "escalate" and r.ticket_id
    assert container.services.tickets.created[-1]["priority"] == "normal"
