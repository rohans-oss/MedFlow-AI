"""V7 — AI operations assistant: question routing, answers equal to the underlying V1–V6 endpoints, read-only (write
requests refused, nothing created), hospital isolation and private conversations, graph-down degradation, audit trail,
conversation memory, rate limit, and the optional LLM tool loop (with a scripted provider: tool execution, unknown
tools / invalid arguments rejected, step limit, structured output, grounding check → deterministic fallback).

No real LLM is called by these tests (none is reachable in CI); provider adapters are tested against mocked HTTP.
"""

import json

import httpx
import pytest
from sqlalchemy import func, select

from app.assistant import engine, llm, planner
from app.assistant.tools import TOOLS, Ctx, ToolResult
from app.core.config import settings
from app.models import AssistantConversation, AssistantMessage, AuditLog, ProcurementRecommendation, SupplierOrder
from tests.conftest import as_role
from tests.test_graph import _graph_world, graph, graph_down  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _clean():
    engine.reset_rate_limit()
    llm.set_provider(None)
    yield
    llm.set_provider(None)
    engine.reset_rate_limit()


def _ask(c, q, cid=None):
    r = c.post("/api/assistant/ask", json={"question": q, "conversation_id": cid})
    assert r.status_code == 200, r.text
    return r.json()


def _counts(db):
    db.expire_all()
    return (db.scalar(select(func.count(SupplierOrder.id))), db.scalar(select(func.count(ProcurementRecommendation.id))))


# ---------------------------------------------------------------- deterministic planner


def test_planner_routes_the_example_questions(db, world):
    rely, cheap, tkr, wax = _graph_world(db, world)
    ctx = Ctx(db, world["users"]["procurement_manager"])
    gloves = world["gloves"].id
    mem = {"item_id": gloves, "item_name": "Surgical gloves 7", "item_code": "GLV-7"}
    cases = [
        ("Why are surgical gloves at risk?", {}, "why_risk",
         [("item_status", {"item_id": gloves}), ("graph_explain", {"item_id": gloves})]),
        ("Which suppliers can cover it within 4 days?", mem, "supplier_options", [("supplier_options", {"item_id": gloves})]),
        ("What happens if CHEAP becomes unavailable?", {}, "supplier_impact", [("supplier_impact", {"supplier_id": cheap.id})]),
        ("Which procedures are affected if this item runs out?", mem, "item_impact", [("item_impact", {"item_id": gloves})]),
        ("Why did the optimizer choose RELY instead of CHEAP for gloves?", {}, "why_recommendation",
         [("compare_with_cheapest", {"item_id": gloves})]),
        ("Compare the current recommendation with the cheapest option", mem, "compare", [("compare_with_cheapest", {"item_id": gloves})]),
        ("Show me all high-risk single-source items", {}, "single_source", [("graph_query", {"name": "risky_single_source"})]),
        ("Which supplies are most likely to run out next week?", {}, "top_risks", [("top_risks", {"within_days": 7, "limit": 10})]),
        ("Which supplier has the most delays?", {}, "supplier_delays", [("supplier_delays", {})]),
        ("What should I review today?", {}, "daily_review", [("daily_review", {})]),
        ("What does OTIF mean?", {}, "glossary", [("glossary", {"term": "What does OTIF mean?"})]),
        ("What happens if RELY takes 2 additional days for gloves?", {}, "what_if",
         [("procurement_what_if", {"item_id": gloves, "supplier_id": rely.id, "delay_days": 2})]),
        ("Which procedures are driving demand for gloves?", {}, "forecast",
         [("item_forecast", {"item_id": gloves, "days": 14}), ("item_impact", {"item_id": gloves})]),
    ]
    for q, memory, intent, calls in cases:
        p = planner.plan(ctx, q, memory)
        assert (p.intent, p.calls) == (intent, calls), q
        assert p.refusal is None and p.clarify is None, q
    # read-only: imperative write requests are refused; "why/should/which" questions are not
    for q in ("Approve the recommendation for gloves", "Place an order for 300 gloves with RELY", "please cancel PO-1",
              "Reject it", "Buy gloves from CHEAP"):
        assert planner.plan(ctx, q, mem).intent == "refuse_write", q
    assert planner.plan(ctx, "Should I order gloves from RELY or CHEAP?", {}).intent != "refuse_write"
    # unknown item / off-topic
    assert planner.plan(ctx, "Why is unobtainium at risk?", {}).clarify == {"kind": "item", "candidates": []}
    assert planner.plan(ctx, "What is the weather in Chennai?", {}).intent == "help"


def test_every_registered_tool_is_read_only_and_described():
    assert len(TOOLS) == 20
    forbidden = ("approve", "reject", "order_create", "create", "update", "delete", "receive", "issue", "generate")
    for name, spec in TOOLS.items():
        assert not any(name.startswith(f) for f in forbidden), name
        s = spec.schema()
        assert s["description"] and s["parameters"]["additionalProperties"] is False


# ---------------------------------------------------------------- answers come from the existing endpoints


def test_answers_repeat_the_numbers_of_the_underlying_endpoints(login, world, db):
    rely, cheap, *_ = _graph_world(db, world)
    c = as_role(login, "procurement_manager")
    gloves = world["gloves"].id

    a = _ask(c, "Why are surgical gloves at risk?")
    risk = c.get(f"/api/stockout-risks/{gloves}").json()
    assert a["mode"] == "deterministic" and a["grounded"] is True and a["provider"] == "deterministic"
    assert f"{risk['risk_level']} stockout risk ({round(risk['probability'] * 100)}% within 14 days)" in a["answer"]["summary"]
    assert f"runs out on {risk['expected_stockout_date']}" in a["answer"]["summary"]
    assert [e["tool"] for e in a["evidence"]] == ["item_status", "graph_explain"]

    b = _ask(c, "Which suppliers can cover it within 4 days?", a["conversation_id"])  # "it" = gloves (memory)
    assert b["intent"] == "supplier_options" and b["evidence"][0]["args"] == {"item_id": gloves}
    opts = c.get(f"/api/supplier-intelligence/items/{gloves}", params={"window_days": 365}).json()
    by = {o["code"]: o for o in opts["options"]}
    pts = " ".join(b["answer"]["points"])
    assert b["answer"]["points"] == opts["summary"][:len(b["answer"]["points"])]  # the V4 page's own sentences
    assert f"in {by['RELY']['in_time_k']} of {by['RELY']['in_time_n']} past orders" in pts
    assert f"in {by['CHEAP']['in_time_k']} of {by['CHEAP']['in_time_n']} past orders" in pts
    assert "Reliable Supplies can meet the required delivery window" in b["answer"]["summary"]

    d = _ask(c, "Compare the current recommendation with the cheapest option for gloves")
    plan = c.get(f"/api/procurement/items/{gloves}/plan").json()
    rec = next(s for s in plan["scenarios"] if s["key"] == plan["recommended_key"])
    assert f"expected total cost ₹{rec['costs']['total']:,.0f}" in d["answer"]["summary"]

    e = _ask(c, "Which supplier has the most delays?")
    assert e["answer"]["summary"].startswith("Cheap Supplier (CHEAP)")


def test_read_only_write_requests_are_refused_and_nothing_is_created(login, world, db):
    _graph_world(db, world)
    c = as_role(login, "admin")
    c.post("/api/procurement/recommendations/generate", json={})  # a pending recommendation exists to "approve"
    before = _counts(db)
    for q in ("Approve the recommendation for gloves", "Place an order for 300 gloves with RELY", "Cancel it",
              *planner.EXAMPLES):
        a = _ask(c, q)
        if q.startswith(("Approve", "Place", "Cancel")):
            assert a["intent"] == "refuse_write" and a["evidence"] == [] and "read-only" in a["answer"]["summary"]
    assert _counts(db) == before
    db.expire_all()
    assert db.scalar(select(func.count(ProcurementRecommendation.id)).where(
        ProcurementRecommendation.status != "PENDING")) == 0  # nothing approved / rejected


def test_hospital_isolation_and_private_conversations(login, world, db):
    _graph_world(db, world)
    c = as_role(login, "procurement_manager")
    a = _ask(c, "Why is Other item at risk?")  # belongs to the other hospital → not resolvable
    assert a["evidence"] == [] and "couldn't identify the item" in a["answer"]["summary"]
    a2 = _ask(c, "Why are surgical gloves at risk?")
    cid = a2["conversation_id"]
    assert len(c.get("/api/assistant/conversations").json()) == 2
    assert c.get(f"/api/assistant/conversations/{cid}").status_code == 200

    other_same_hospital = as_role(login, "viewer")
    assert other_same_hospital.get(f"/api/assistant/conversations/{cid}").status_code == 404
    assert other_same_hospital.get("/api/assistant/conversations").json() == []
    r = other_same_hospital.post("/api/assistant/ask", json={"question": "Why is it at risk?", "conversation_id": cid})
    assert r.status_code == 404

    other = login("admin@other.demo")
    assert other.get(f"/api/assistant/conversations/{cid}").status_code == 404
    o = _ask(other, "Why are surgical gloves at risk?")
    assert o["evidence"] == [] and "couldn't identify" in o["answer"]["summary"]
    ctx = Ctx(db, world["users"]["other_admin"])
    assert TOOLS["item_status"].fn(ctx, item_id=world["gloves"].id).ok is False  # tools re-check ownership (404)


def test_audit_trail_memory_and_permissions(login, world, db, client):
    _graph_world(db, world)
    assert client.post("/api/assistant/ask", json={"question": "hi"}).status_code == 401
    c = as_role(login, "viewer")  # read permission is enough to ask
    st = c.get("/api/assistant/status").json()
    assert st["mode"] == "deterministic" and st["llm_configured"] is False and st["read_only"] is True
    assert len(st["tools"]) == 20 and st["examples"] == planner.EXAMPLES
    assert c.post("/api/assistant/ask", json={"question": "x" * 501}).status_code == 422
    assert c.post("/api/assistant/ask", json={"question": "   "}).status_code == 422

    a = _ask(c, "Why are surgical gloves at risk?")
    b = _ask(c, "Which procedures are affected if it runs out?", a["conversation_id"])
    assert b["intent"] == "item_impact" and b["evidence"][0]["args"] == {"item_id": world["gloves"].id}
    detail = c.get(f"/api/assistant/conversations/{a['conversation_id']}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"] * 2
    assert detail["context"]["item_id"] == world["gloves"].id
    m = detail["messages"][1]
    assert m["tool_calls"][0]["tool"] == "item_status" and m["provider"] == "deterministic" and m["intent"] == "why_risk"
    db.expire_all()
    rows = db.scalars(select(AuditLog).where(AuditLog.action == "assistant.ask").order_by(AuditLog.id)).all()
    assert len(rows) == 2 and rows[0].details["question"] == "Why are surgical gloves at risk?"
    assert rows[0].details["tools"] == ["item_status", "graph_explain"] and rows[0].entity_id == a["conversation_id"]


def test_rate_limit(login, world, db, monkeypatch):
    monkeypatch.setattr(settings, "ASSISTANT_RATE_LIMIT_PER_MINUTE", 2)
    c = as_role(login, "viewer")
    _ask(c, "What does OTIF mean?")
    _ask(c, "What does MOQ mean?")
    assert c.post("/api/assistant/ask", json={"question": "What is WAPE?"}).status_code == 429


# ---------------------------------------------------------------- knowledge graph up / down


def test_graph_down_answers_from_the_other_tools_and_says_what_is_missing(login, world, db, graph_down):  # noqa: F811
    _graph_world(db, world)
    c = as_role(login, "procurement_manager")
    a = _ask(c, "Why are surgical gloves at risk?")
    assert "HIGH stockout risk" in a["answer"]["summary"]  # from V3, which never reads the graph
    assert any(n.startswith("Knowledge graph unavailable") for n in a["answer"]["notes"])
    assert [(e["tool"], e["ok"]) for e in a["evidence"]] == [("item_status", True), ("graph_explain", False)]
    b = _ask(c, "What happens if CHEAP becomes unavailable?")
    assert b["answer"]["summary"].startswith("I couldn't get the data") and b["evidence"][0]["ok"] is False


def test_graph_questions_with_a_graph_store(login, world, db, graph):  # noqa: F811
    rely, cheap, tkr, wax = _graph_world(db, world)
    c = as_role(login, "procurement_manager")
    a = _ask(c, "What happens if CHEAP becomes unavailable?")
    impact = c.get(f"/api/graph/impact/suppliers/{cheap.id}").json()["data"]
    assert a["evidence"][0]["ok"] and a["answer"]["summary"] == impact["summary"][0]
    assert any("Bone wax" in p for p in a["answer"]["points"])
    b = _ask(c, "Why are surgical gloves at risk?")
    assert b["evidence"][1]["ok"] and any("Total knee replacement" in p for p in b["answer"]["points"])
    s = _ask(c, "Show me all single-source items")
    assert any("Bone wax" in p for p in s["answer"]["points"])


# ---------------------------------------------------------------- optional LLM mode (scripted provider)


def _tc(name, args, i=0):
    return {"tool_calls": [{"id": f"c{i}", "name": name, "arguments": args}]}


def _final(summary, points=()):
    return {"content": json.dumps({"summary": summary, "points": list(points), "follow_ups": []})}


def test_llm_answer_is_used_when_grounded(login, world, db):
    _graph_world(db, world)
    gloves = world["gloves"].id
    p = llm.ScriptedProvider([_tc("find_item", {"query": "gloves"}), _tc("item_status", {"item_id": gloves}, 1),
                              _final("Surgical gloves 7 is at HIGH risk: 90% probability of a stockout within 14 days.",
                                     ["Usable stock 120 pair; runs out on day 3."])])
    llm.set_provider(p)
    c = as_role(login, "procurement_manager")
    assert c.get("/api/assistant/status").json()["mode"] == "llm"
    a = _ask(c, "Why are gloves at risk?")
    assert a["mode"] == "llm" and a["provider"] == "scripted" and a["grounded"] is True and a["fallback_reason"] is None
    assert a["answer"]["summary"].startswith("Surgical gloves 7 is at HIGH risk")
    assert [e["tool"] for e in a["evidence"]] == ["find_item", "item_status"]
    tool_msgs = [m for m in p.seen[-1] if m["role"] == "tool"]
    assert '"probability": 0.9' in tool_msgs[1]["content"]  # the model received the real tool output
    assert p.seen[0][0]["role"] == "system" and "read-only" in p.seen[0][0]["content"]
    db.expire_all()
    m = db.scalar(select(AssistantMessage).where(AssistantMessage.role == "assistant"))
    assert m.provider == "scripted" and m.model == "scripted-test-model" and m.grounded is True
    conv = db.scalar(select(AssistantConversation))
    assert conv.context["item_id"] == gloves  # memory also works when the model chose the tools


@pytest.mark.parametrize("steps,reason", [
    ([_tc("item_status", {"item_id": 0}), _final("It will run out in 777 days.")], "numbers not found in tool results: 777"),
    ([_final("Gloves are fine.")], "without calling any MedFlow tool"),
    ([{"content": "Gloves are at high risk."}], "not the required structured JSON"),
])
def test_ungrounded_or_malformed_llm_answers_fall_back_to_the_planner(login, world, db, steps, reason):
    _graph_world(db, world)
    gloves = world["gloves"].id
    steps = json.loads(json.dumps(steps).replace('"item_id": 0', f'"item_id": {gloves}'))
    llm.set_provider(llm.ScriptedProvider(steps))
    c = as_role(login, "procurement_manager")
    a = _ask(c, "Why are surgical gloves at risk?")
    assert a["mode"] == "deterministic" and reason in a["fallback_reason"]
    assert "777" not in a["answer"]["summary"] and "HIGH stockout risk" in a["answer"]["summary"]
    assert any("language model's answer was not used" in n for n in a["answer"]["notes"])
    db.expire_all()
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "assistant.ask"))
    assert audit.details["provider"] == "scripted" and reason in audit.details["fallback_reason"]


def test_llm_cannot_call_unknown_tools_or_pass_invalid_arguments(db, world):
    _graph_world(db, world)
    gloves = world["gloves"].id
    ctx = Ctx(db, world["users"]["procurement_manager"])
    before = _counts(db)
    p = llm.ScriptedProvider([
        _tc("approve_recommendation", {"id": 1}),
        _tc("item_status", {"item_id": "gloves"}, 1),
        _tc("item_status", {"item_id": gloves, "sql": "DROP TABLE"}, 2),
        _tc("item_status", {}, 3),
        _tc("top_risks", {"level": "EXTREME"}, 4),
        _tc("item_status", {"item_id": gloves}, 5),
        _final("Surgical gloves 7: HIGH, 90%."),
    ])
    out = llm.run(ctx, p, "Why are gloves at risk?", [])
    errors = [json.loads(m["content"])["error"] for m in p.seen[-1] if m["role"] == "tool"][:5]
    assert errors == ["unknown tool 'approve_recommendation'", "'item_id' must be an integer",
                      "unexpected arguments ['sql']", "missing required argument 'item_id'",
                      "'level' must be one of ['HIGH', 'MEDIUM', 'LOW']"]
    assert [r.tool for r in out.results] == ["item_status"] and out.grounded and out.answer
    assert _counts(db) == before


def test_llm_step_limit_and_provider_errors(db, world, monkeypatch):
    _graph_world(db, world)
    ctx = Ctx(db, world["users"]["viewer"])
    monkeypatch.setattr(settings, "ASSISTANT_MAX_STEPS", 2)
    p = llm.ScriptedProvider([_tc("supplier_delays", {}, i) for i in range(10)])
    out = llm.run(ctx, p, "Which supplier has the most delays?", [])
    assert out.answer is None and out.fallback_reason == "too many tool rounds" and len(out.results) == 2

    class Broken:
        name, model = "broken", "x"

        def complete(self, messages, tools):
            raise httpx.ConnectError("no route")

    out = llm.run(ctx, Broken(), "Which supplier has the most delays?", [])
    assert out.answer is None and out.fallback_reason == "LLM provider error: ConnectError"


def test_write_requests_never_reach_the_llm(login, world, db):
    _graph_world(db, world)
    p = llm.ScriptedProvider([_tc("item_status", {"item_id": world["gloves"].id})])
    llm.set_provider(p)
    a = _ask(as_role(login, "admin"), "Approve the recommendation for gloves")
    assert a["intent"] == "refuse_write" and p.seen == [] and a["evidence"] == []


def test_grounding_and_argument_validation_units():
    r = ToolResult("item_status", {"item_id": 1}, data={"probability": 0.173, "usable_stock": 80, "cost": 22196.4},
                   facts=["Usable stock 80 foil; ₹22,196 expected."])
    assert llm.grounded("17% risk, 80 foil, ₹22,196, 17.3 %", "", [r]) == (True, [])
    assert llm.grounded("Stock lasts 12 days", "", [r]) == (False, ["12"])
    assert llm.grounded("In the next 4 days: 80 left", "within 4 days?", [r])[0] is True  # numbers from the question
    assert llm.validate_args("top_risks", {"level": "HIGH", "limit": 5.0}) == ({"level": "HIGH", "limit": 5}, None)
    assert llm.validate_args("top_risks", {"limit": 500})[1] == "'limit' out of range"
    assert llm.validate_args("item_status", {"item_id": True})[1] == "'item_id' must be an integer"
    assert llm.validate_args("item_status", [1])[1] == "arguments must be an object"


def test_provider_configuration_and_adapters(monkeypatch):
    for k, v in (("ASSISTANT_PROVIDER", "none"), ("ASSISTANT_MODEL", ""), ("ASSISTANT_API_KEY", ""), ("ASSISTANT_BASE_URL", "")):
        monkeypatch.setattr(settings, k, v)
    assert llm.get_provider() is None  # default: no LLM, no keys
    monkeypatch.setattr(settings, "ASSISTANT_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ASSISTANT_MODEL", "some-model")
    assert llm.get_provider() is None  # anthropic without a key stays off
    monkeypatch.setattr(settings, "ASSISTANT_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "ASSISTANT_BASE_URL", "http://localhost:11434/v1/")
    p = llm.get_provider()
    assert isinstance(p, llm.OpenAICompatibleProvider) and p.base_url == "http://localhost:11434/v1" and p.key is None

    sent = {}

    class Resp:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, body=json, headers=headers)
        if "anthropic" in url:
            return Resp({"content": [{"type": "text", "text": "thinking"},
                                     {"type": "tool_use", "id": "t1", "name": "top_risks", "input": {"limit": 3}}]})
        return Resp({"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "top_risks", "arguments": '{"limit": 3}'}}]}}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    tools = [t.schema() for t in TOOLS.values()]
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "top_risks", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "a", "name": "top_risks", "content": "{}"}]
    out = p.complete(msgs, tools)
    assert out == {"tool_calls": [{"id": "a", "name": "top_risks", "arguments": {"limit": 3}}]}
    assert sent["url"] == "http://localhost:11434/v1/chat/completions" and sent["headers"] == {}
    assert sent["body"]["temperature"] == 0 and len(sent["body"]["tools"]) == 20

    a = llm.AnthropicProvider("m", "test-key-not-real", 5)
    out = a.complete(msgs, tools)
    assert out == {"tool_calls": [{"id": "t1", "name": "top_risks", "arguments": {"limit": 3}}]}
    assert sent["body"]["system"] == "S" and sent["headers"]["x-api-key"] == "test-key-not-real"
    assert sent["body"]["messages"][1]["content"][0]["type"] == "tool_use"
    assert sent["body"]["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "a", "content": "{}"}
