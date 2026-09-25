"""V7 — AI operations assistant schemas (mirrored in frontend/lib/types.ts)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    conversation_id: int | None = None

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Ask a question")
        return v


class Link(BaseModel):
    label: str
    href: str


class ToolCallOut(BaseModel):
    tool: str
    label: str
    args: dict[str, Any]
    ok: bool
    facts: list[str]
    error: str | None
    ms: int


class AnswerOut(BaseModel):
    title: str
    summary: str
    points: list[str]
    notes: list[str] = []
    links: list[Link] = []
    follow_ups: list[str] = []


class AskOut(BaseModel):
    conversation_id: int
    message_id: int
    question: str
    answer: AnswerOut
    evidence: list[ToolCallOut]
    mode: str  # deterministic | llm
    provider: str
    model: str | None
    intent: str | None
    grounded: bool
    fallback_reason: str | None
    latency_ms: int


class ToolInfo(BaseModel):
    name: str
    description: str


class AssistantStatus(BaseModel):
    provider: str
    mode: str
    model: str | None
    llm_configured: bool
    read_only: bool = True
    max_steps: int
    tools: list[ToolInfo]
    examples: list[str]
    capabilities: list[str]


class ConversationOut(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    answer: dict[str, Any] | None
    tool_calls: list[dict[str, Any]] | None
    provider: str | None
    model: str | None
    intent: str | None
    grounded: bool | None
    fallback_reason: str | None
    latency_ms: int | None
    created_at: datetime


class ConversationDetail(BaseModel):
    id: int
    title: str
    context: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]
