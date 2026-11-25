"""Request and response models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, EmailStr, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, description="Omit to start a new conversation")
    customer_email: EmailStr | None = Field(
        default=None, description="Verified identity from your auth layer"
    )


class Citation(BaseModel):
    id: str
    source: str
    title: str
    section: str | None = None
    snippet: str
    score: float


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    intent: str
    citations: list[Citation]
    guard: dict[str, Any]
    verdict: dict[str, Any]
    ticket_id: str | None
    trace: list[str]
    attempts: int = 1
    latency_ms: int


class VoiceResponse(ChatResponse):
    transcript: str
    audio_base64: str
    audio_mime: str


class IngestResponse(BaseModel):
    documents_seen: int
    documents_updated: int
    documents_unchanged: int
    chunks_written: int
    chunks_quarantined: int
    quarantined_sources: list[str]


class TranscriptMessage(BaseModel):
    role: str
    content: str
    intent: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str


class TranscriptResponse(BaseModel):
    conversation_id: str
    channel: str
    customer_email: str | None
    messages: list[TranscriptMessage]
