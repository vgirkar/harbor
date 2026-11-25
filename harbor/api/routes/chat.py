from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from harbor.api.deps import get_container
from harbor.api.schemas import ChatRequest, ChatResponse
from harbor.container import Container

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, c: Container = Depends(get_container)) -> ChatResponse:
    result = await c.runtime.run_turn(
        message=body.message, conversation_id=body.conversation_id, customer_email=body.customer_email
    )
    return ChatResponse(**result.to_dict())


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, c: Container = Depends(get_container)) -> EventSourceResponse:
    """Server-sent events: conversation, status*, token*, rewrite?, done."""

    async def gen():
        async for ev in c.runtime.stream_turn(
            message=body.message, conversation_id=body.conversation_id, customer_email=body.customer_email
        ):
            yield {"event": ev["event"], "data": json.dumps(ev["data"])}

    return EventSourceResponse(gen())
