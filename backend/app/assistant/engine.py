"""V7 — the assistant's single entry point: question → tools → grounded answer → stored with its evidence.

    1. Write requests ("approve…", "order…") are refused before anything runs — the assistant is read-only.
    2. With an LLM configured, the model picks tools (llm.run). Its answer is used only if it is structured and
       grounded in this turn's tool results; otherwise the deterministic answer is used and the reason is stored.
    3. Without an LLM (default), the deterministic planner picks the tools and the composer writes the answer from
       the tools' facts.
    4. Every question and answer is stored (assistant_messages: tool calls, provider, grounding) and audited.
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.assistant import llm, planner
from app.assistant.tools import TOOLS, Ctx, ToolResult
from app.core.config import settings
from app.db.base import utcnow
from app.models import AssistantConversation, AssistantMessage, Consumable, User
from app.services import audit

_calls: dict[int, deque] = defaultdict(deque)


def _rate_limit(user: User) -> None:
    now = time.monotonic()
    q = _calls[user.id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= settings.ASSISTANT_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many questions in the last minute. Try again shortly.")
    q.append(now)


def reset_rate_limit() -> None:
    _calls.clear()


def own_conversation(db: Session, user: User, conversation_id: int) -> AssistantConversation:
    """A conversation is private to the user who started it (others — even in the same hospital — get 404)."""
    conv = db.get(AssistantConversation, conversation_id)
    if conv is None or conv.user_id != user.id or conv.hospital_id != user.hospital_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conv


def _history(conv: AssistantConversation | None) -> list[dict]:
    out: list[dict] = []
    if conv is None:
        return out
    for m in conv.messages[-6:]:
        text = m.content if m.role == "user" else ((m.answer or {}).get("summary") or m.content)
        out.append({"role": m.role, "content": text})
    return out


def _evidence(results: list[ToolResult]) -> list[dict]:
    return [{"tool": r.tool, "label": planner.tool_label(r.tool), "args": r.args, "ok": r.ok, "facts": r.facts[:20],
             "error": r.error, "ms": r.ms} for r in results]


def _memory(db: Session, conv: AssistantConversation, p: planner.Plan, results: list[ToolResult]) -> dict:
    mem = dict(conv.context or {})
    item, sup = p.item, p.supplier
    if item is None:  # LLM mode: remember the last item a successful tool looked at
        for r in reversed(results):
            c = db.get(Consumable, r.args["item_id"]) if r.ok and r.args.get("item_id") else None
            if c is not None and c.hospital_id == conv.hospital_id:
                item = {"id": c.id, "name": c.name, "code": c.sku}
                break
    if item:
        mem.update(item_id=item["id"], item_name=item["name"], item_code=item.get("code"))
    if sup:
        mem.update(supplier_id=sup["id"], supplier_name=sup["name"], supplier_code=sup.get("code"))
    return mem


def status_info() -> dict:
    prov = llm.get_provider()
    return {"provider": prov.name if prov else "deterministic", "mode": "llm" if prov else "deterministic",
            "model": prov.model if prov else None, "llm_configured": prov is not None,
            "max_steps": settings.ASSISTANT_MAX_STEPS,
            "tools": [{"name": t.name, "description": t.description} for t in TOOLS.values()],
            "examples": planner.EXAMPLES, "capabilities": planner.CAPABILITIES}


def ask(db: Session, user: User, question: str, conversation_id: int | None = None, ip: str | None = None) -> dict:
    _rate_limit(user)
    started = time.monotonic()
    conv = own_conversation(db, user, conversation_id) if conversation_id is not None else None
    history = _history(conv)
    memory = dict(conv.context or {}) if conv else {}
    ctx = Ctx(db, user)
    p = planner.plan(ctx, question, memory)

    provider = llm.get_provider()
    # `grounded` describes the answer that is shown: an LLM answer only when it passed the grounding check; the
    # deterministic composer only uses tool facts. An ungrounded LLM answer is never shown (fallback_reason says why).
    mode, prov_name, model, fallback = "deterministic", "deterministic", None, None
    results: list[ToolResult] = []
    answer: dict | None = None

    if provider is not None and not p.refusal:
        out = llm.run(ctx, provider, question, history)
        results = out.results
        prov_name, model = provider.name, provider.model
        if out.answer is not None:
            mode = "llm"
            notes = [f"{planner.tool_label(r.tool)} unavailable: {r.error}" for r in results if not r.ok]
            links = []
            for r in results:
                for ln in r.links:
                    if r.ok and ln not in links:
                        links.append(ln)
            title = f"About {p.item['name']}" if p.item else "Answer"
            answer = {"title": title, **out.answer, "notes": notes, "links": links[:5]}
            if not answer["follow_ups"]:
                answer["follow_ups"] = planner.FOLLOW_UPS.get(p.intent, planner.EXAMPLES[:3])
        else:
            fallback = out.fallback_reason

    if answer is None:  # deterministic planner (default, or fallback from the LLM)
        det = planner.run_calls(ctx, p)
        answer = planner.compose(p, det, question)
        if fallback:
            answer.setdefault("notes", []).append(f"The language model's answer was not used ({fallback}); this answer was "
                                                  "composed directly from MedFlow's tools.")
        results = results + det if fallback else det
    grounded = True

    answer.setdefault("notes", [])
    latency = int((time.monotonic() - started) * 1000)
    evidence = _evidence(results)
    now = utcnow()
    if conv is None:  # created only now: tools may commit/refresh derived data (risk snapshots, graph sync) on the way
        conv = AssistantConversation(hospital_id=user.hospital_id, user_id=user.id, title=question[:200], context={})
        db.add(conv)
        db.flush()
    db.add(AssistantMessage(conversation_id=conv.id, role="user", content=question, created_at=now))
    msg = AssistantMessage(conversation_id=conv.id, role="assistant", content=answer["summary"], answer=answer,
                           tool_calls=evidence, provider=prov_name, model=model, intent=p.intent,
                           grounded=grounded, fallback_reason=fallback, latency_ms=latency,
                           created_at=now)
    db.add(msg)
    conv.context = _memory(db, conv, p, results)
    conv.updated_at = now
    db.flush()
    audit.record(db, user, "assistant.ask", "assistant_conversation", conv.id, ip=ip, details={
        "question": question, "intent": p.intent, "mode": mode, "provider": prov_name, "model": model,
        "tools": [e["tool"] for e in evidence], "grounded": grounded, "fallback_reason": fallback,
        "refused": bool(p.refusal)})
    db.commit()
    return {"conversation_id": conv.id, "message_id": msg.id, "question": question, "answer": answer,
            "evidence": evidence, "mode": mode, "provider": prov_name, "model": model, "intent": p.intent,
            "grounded": grounded, "fallback_reason": fallback, "latency_ms": latency}
