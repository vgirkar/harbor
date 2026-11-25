from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from harbor.api.deps import get_container
from harbor.api.schemas import TranscriptMessage, TranscriptResponse
from harbor.container import Container
from harbor.db.repositories import ConversationRepository

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.get("/{conversation_id}", response_model=TranscriptResponse)
async def transcript(conversation_id: str, c: Container = Depends(get_container)) -> TranscriptResponse:
    if c.sessions is None:
        raise HTTPException(409, "transcripts require Postgres mode")
    try:
        cid = uuid.UUID(conversation_id)
    except ValueError as exc:
        raise HTTPException(400, "invalid conversation id") from exc
    async with c.sessions() as session:
        repo = ConversationRepository(session)
        conv = await repo.get(cid)
        if conv is None:
            raise HTTPException(404, "conversation not found")
        rows = await repo.transcript(cid)
    return TranscriptResponse(
        conversation_id=str(cid),
        channel=conv.channel,
        customer_email=conv.customer_email,
        messages=[
            TranscriptMessage(
                role=m.role,
                content=m.content,
                intent=m.intent,
                citations=m.citations or [],
                created_at=m.created_at.isoformat(),
            )
            for m in rows
        ],
    )
