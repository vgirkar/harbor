from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from harbor.api.deps import get_container
from harbor.api.schemas import VoiceResponse
from harbor.container import Container

router = APIRouter(prefix="/v1/voice", tags=["voice"])
MAX_AUDIO_BYTES = 10 * 1024 * 1024


@router.post("/chat", response_model=VoiceResponse)
async def voice_chat(
    audio: UploadFile = File(..., description="webm/ogg/wav/mp3 recording"),
    conversation_id: str | None = Form(default=None),
    customer_email: str | None = Form(default=None),
    c: Container = Depends(get_container),
) -> VoiceResponse:
    payload = await audio.read()
    if not payload:
        raise HTTPException(400, "empty audio")
    if len(payload) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "audio larger than 10 MB")
    transcript = await c.speech.transcribe(payload, audio.content_type or "audio/webm")
    if not transcript:
        raise HTTPException(422, "could not transcribe audio")
    result = await c.runtime.run_turn(
        message=transcript, conversation_id=conversation_id, customer_email=customer_email, channel="voice"
    )
    audio_bytes, mime = await c.speech.synthesize(result.answer)
    return VoiceResponse(
        **result.to_dict(),
        transcript=transcript,
        audio_base64=base64.b64encode(audio_bytes).decode(),
        audio_mime=mime,
    )
