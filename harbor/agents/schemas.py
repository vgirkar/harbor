"""Structured outputs exchanged with the models. Every classifier returns one of these, never free text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["knowledge", "order", "escalate", "smalltalk"]


class RouteDecision(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=300)


class InjectionVerdict(BaseModel):
    is_injection: bool
    category: Literal[
        "override", "persona", "prompt_leak", "data_exfiltration", "cross_customer", "off_topic_abuse", "none"
    ] = "none"
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=300)


class GroundingVerdict(BaseModel):
    grounded: bool
    score: float = Field(ge=0, le=1)
    unsupported_claims: list[str] = Field(default_factory=list)
    fix_instruction: str = Field(default="", max_length=500)


class TicketSummary(BaseModel):
    summary: str = Field(max_length=800)
    priority: Literal["low", "normal", "high"] = "normal"


class JudgeScores(BaseModel):
    correctness: int = Field(ge=1, le=5)
    groundedness: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    tone: int = Field(ge=1, le=5)
    passed: bool = Field(alias="pass")
    rationale: str = Field(max_length=400)

    model_config = {"populate_by_name": True}
