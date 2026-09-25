"""V7 — AI operations assistant API (read-only: it queries and explains V1–V6 results, it never changes anything).

GET  /assistant/status                  provider (deterministic by default), tools, example questions
POST /assistant/ask                     ask a question (optionally continuing a conversation)
GET  /assistant/conversations           the caller's own conversations
GET  /assistant/conversations/{id}      one of the caller's conversations with every answer and its tool calls
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from app.api.deps import DB, client_ip, require
from app.assistant import engine
from app.core.permissions import READ
from app.models import AssistantConversation, AssistantMessage, User
from app.schemas.assistant import AskIn, AskOut, AssistantStatus, ConversationDetail, ConversationOut

router = APIRouter(prefix="/assistant", tags=["ai assistant"])
Reader = Annotated[User, Depends(require(READ))]


@router.get("/status", response_model=AssistantStatus)
def assistant_status(user: Reader):
    return engine.status_info()


@router.post("/ask", response_model=AskOut)
def ask(body: AskIn, request: Request, user: Reader, db: DB):
    return engine.ask(db, user, body.question, body.conversation_id, ip=client_ip(request))


@router.get("/conversations", response_model=list[ConversationOut])
def conversations(user: Reader, db: DB, limit: int = 30):
    n = (select(AssistantMessage.conversation_id, func.count().label("n"))
         .group_by(AssistantMessage.conversation_id).subquery())
    rows = db.execute(
        select(AssistantConversation, func.coalesce(n.c.n, 0))
        .outerjoin(n, n.c.conversation_id == AssistantConversation.id)
        .where(AssistantConversation.user_id == user.id, AssistantConversation.hospital_id == user.hospital_id)
        .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id.desc())
        .limit(max(1, min(limit, 100)))).all()
    return [{"id": c.id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at, "message_count": k}
            for c, k in rows]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation(conversation_id: int, user: Reader, db: DB):
    conv = engine.own_conversation(db, user, conversation_id)
    return {"id": conv.id, "title": conv.title, "context": conv.context, "created_at": conv.created_at,
            "updated_at": conv.updated_at, "messages": conv.messages}
