"""V7 — deterministic question planner and answer composer (the default; no LLM, no API key).

    question ─► refuse write actions ─► resolve items / suppliers (this hospital only; conversation memory for "it")
             ─► intent (ordered patterns) ─► tool calls ─► answer composed from the tools' facts (numbers only)

It understands a fixed set of operational questions; anything else gets the list of what it can answer instead of a
guess. The optional LLM mode (llm.py) uses the same tools and falls back to this composer when its answer isn't grounded.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.assistant.tools import GLOSSARY, TOOLS, Ctx, ToolResult, pick, resolve

EXAMPLES = [
    "What should I review today?",
    "Why is Suture 2-0 at risk?",
    "Which suppliers can cover it within 4 days?",
    "What happens if CPS becomes unavailable?",
    "Which procedures are affected if this item runs out?",
    "Why did the optimizer choose CPS instead of OPI for suture 2-0?",
    "Compare the current recommendation with the cheapest option",
    "Show me all high-risk single-source items",
    "Which supplies are most likely to run out next week?",
    "Which supplier has the most delays?",
    "What happens if CPS takes 2 additional days?",
    "What does OTIF mean?",
]

CAPABILITIES = [
    "why an item is at risk (stock, forecast, procedures, suppliers)",
    "which suppliers can deliver an item in time, and supplier performance or delays",
    "what happens if a supplier becomes unavailable or delivers late",
    "which procedures / departments depend on an item",
    "the procurement recommendation for an item and how it compares with the cheapest option",
    "items at risk, single-source items, pending approvals, alerts, and what to review today",
    "definitions of MedFlow metrics (OTIF, safety stock, cost model…)",
]


@dataclass
class Plan:
    intent: str
    calls: list[tuple[str, dict]] = field(default_factory=list)
    item: dict | None = None
    supplier: dict | None = None
    clarify: dict | None = None  # {"kind": "item"|"supplier", "candidates": [...]} when ambiguous / missing
    refusal: str | None = None
    days: int | None = None


WRITE = re.compile(r"^\s*(please\s+|can you\s+|could you\s+|go ahead and\s+)?(approve|reject|place|buy|purchase|cancel|"
                   r"order|record|receive|confirm|issue|delete|update|change|modify|set)\b", re.I)
PRONOUN = re.compile(r"\b(it|this item|that item|this one|that one|the item|same item)\b", re.I)
DAYS = re.compile(r"(\d+)\s*(?:more|extra|additional|further)?\s*days?", re.I)


CROSS_HOSPITAL = ("I can only answer about {name} — the hospital you are working in. I don't look up other hospitals, "
                  "whether or not they exist or you belong to them. If you are a member of another hospital, switch to it "
                  "with the hospital selector at the top of the page and ask there.")
_GENERIC_HOSPITAL_WORDS = {"hospital", "hospitals", "demo", "clinic", "the", "and", "of", "multispecialty", "community",
                           "general", "nursing", "home", "medical", "centre", "center", "other", "test", "care", "health",
                           "institute", "memorial", "city", "district", "government", "private", "trust", "group"}
_OTHER_HOSPITAL = re.compile(r"\b(other|another|all|every|each|second|different|sister|neighbou?ring)\s+(of\s+(the|our)\s+)?"
                             r"hospitals?\b|\bhospitals?\s+(?!is\b|are\b|as\b|at\b|in\b|to\b|and\b|or\b)[a-z0-9]{1,2}\b"
                             r"|\b(across|between|compare)\b.*\bhospitals\b|\bhospital\s+id\b", re.I)


def _hospital_name(ctx: Ctx) -> str:
    from app.models import Hospital

    h = ctx.db.get(Hospital, ctx.hid)
    return h.name if h else "this hospital"


def _other_hospital(ctx: Ctx, q: str) -> bool:
    """Does the question refer to a hospital other than the active one? Same answer whether or not it exists — the
    reply never reveals other tenants. Distinctive words of other hospitals' names, their codes, and phrases such as
    "another hospital" / "hospital B" / "hospital id" count."""
    from sqlalchemy import select

    from app.models import Hospital

    if _OTHER_HOSPITAL.search(q):
        return True
    words = set(re.findall(r"[a-z0-9]+", q.lower()))
    rows = ctx.db.execute(select(Hospital.id, Hospital.name, Hospital.code)).all()
    mine = next(((n, c) for i, n, c in rows if i == ctx.hid), ("", ""))
    my_words = set(re.findall(r"[a-z0-9]+", mine[0].lower())) | set(re.findall(r"[a-z0-9]+", mine[1].lower()))
    for hid, name, code in rows:
        if hid == ctx.hid:
            continue
        if code and re.search(rf"(?<![A-Za-z0-9-]){re.escape(code)}(?![A-Za-z0-9-])", q):
            return True
        distinctive = {w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 4} - _GENERIC_HOSPITAL_WORDS - my_words
        if distinctive & words:
            return True
    return False


def _has(q: str, *pats: str) -> bool:
    return any(re.search(p, q, re.I) for p in pats)


def plan(ctx: Ctx, question: str, memory: dict[str, Any]) -> Plan:
    q = question.strip()
    if WRITE.search(q) and not _has(q, r"\bwhy\b", r"\bshould\b", r"\bwhat\b", r"\bwhich\b", r"\bhow\b"):
        return Plan("refuse_write", refusal=(
            "I can't approve, order, reject or change anything — the assistant is read-only. Approvals are made by a "
            "person on the Procurement → Approval queue page; I can explain any recommendation there."))

    # ---------------- V8: the assistant answers about the ACTIVE hospital only
    if _other_hospital(ctx, q):
        return Plan("cross_hospital", refusal=CROSS_HOSPITAL.format(name=_hospital_name(ctx)))

    # ---------------- entities
    item = supplier = None
    item_c, sup_c = resolve(ctx, q, "item"), resolve(ctx, q, "supplier")
    strong_sup = [c for c in sup_c if c["score"] >= 100]  # supplier code (CPS) or exact
    if strong_sup:
        supplier = strong_sup[0]
    item_pick, item_amb = pick([c for c in item_c if c["score"] >= 1.0])
    item = item_pick
    if item is None and not item_amb and PRONOUN.search(q) and memory.get("item_id"):
        item = {"id": memory["item_id"], "name": memory.get("item_name", "the item"), "code": memory.get("item_code", "")}

    def need_item(intent: str) -> Plan | None:
        if item:
            return None
        if item_amb:
            return Plan(intent, clarify={"kind": "item", "candidates": item_amb})
        if memory.get("item_id") and not item_c:
            return None
        return Plan(intent, clarify={"kind": "item", "candidates": []})

    def iid() -> int:
        return item["id"] if item else memory["item_id"]

    def mk(intent: str, calls: list[tuple[str, dict]]) -> Plan:
        it = item or ({"id": memory["item_id"], "name": memory.get("item_name"), "code": memory.get("item_code")}
                      if memory.get("item_id") and any("item_id" in a for _, a in calls) else None)
        return Plan(intent, calls, item=it, supplier=supplier)

    def sup_needed(intent: str) -> Plan | None:
        nonlocal supplier
        if supplier:
            return None
        sp, amb = pick([c for c in sup_c if c["score"] >= 1.5])
        if sp:
            supplier = sp
            return None
        if memory.get("supplier_id") and not amb:
            supplier = {"id": memory["supplier_id"], "name": memory.get("supplier_name"), "code": memory.get("supplier_code")}
            return None
        return Plan(intent, clarify={"kind": "supplier", "candidates": amb})

    # ---------------- intents (most specific first)
    if _has(q, r"^(what (is|does|are)|define|meaning of|what'?s)\b", r"\bhow (is|are) .* (calculated|computed|defined)")\
            and not item and any(k in q.lower() for k in list(GLOSSARY) + ["otif", "safety", "wape", "moq", "what-if"]):
        return mk("glossary", [("glossary", {"term": q})])
    if _has(q, r"review today", r"(review|do|look at|check|prioriti[sz]e|order) (first )?today", r"\bpriorit",
            r"needs? (my )?attention today", r"start (of )?(my|the) day", r"daily (review|brief)"):
        return mk("daily_review", [("daily_review", {})])
    if _has(q, r"\bpending\b", r"awaiting approval", r"approval queue", r"\bto approve\b"):
        return mk("pending", [("pending_recommendations", {})])
    if _has(q, r"(most|repeated|frequent|many) (delays|late)", r"delay(ed|s)? (the )?most", r"(least|less) reliable",
            r"unreliable", r"which suppliers? .*\b(late|delay)", r"supplier delays"):
        return mk("supplier_delays", [("supplier_delays", {})])
    if _has(q, r"single[- ]?source", r"only one supplier", r"one supplier only"):
        name = "risky_single_source" if _has(q, r"risk", r"danger", r"critical") else "single_source_items"
        return mk("single_source", [("graph_query", {"name": name})])
    m = DAYS.search(q)
    if _has(q, r"\btakes?\b.*\bdays?\b", r"\bdays? (late|delay|slower|longer)", r"delayed by", r"\bwhat if\b.*\bdays?\b")\
            and m and not _has(q, r"within \d+ days"):
        c = sup_needed("what_if")
        if c:
            return c
        days = int(m.group(1))
        if item or (PRONOUN.search(q) and memory.get("item_id")):
            return mk("what_if", [("procurement_what_if", {"item_id": iid(), "supplier_id": supplier["id"], "delay_days": days})])
        return Plan("what_if_supplier", [("pending_recommendations", {})], supplier=supplier, days=days)
    if _has(q, r"unavailable", r"goes (down|out|bust)", r"\bfails?\b", r"stops? (supplying|delivering)", r"can'?t deliver",
            r"cannot deliver", r"\blose\b", r"bankrupt", r"what happens if", r"impact of") and (supplier or sup_c) and not (
            item and not supplier):
        c = sup_needed("supplier_impact")
        if c:
            return c
        return mk("supplier_impact", [("supplier_impact", {"supplier_id": supplier["id"]})])
    if _has(q, r"why did", r"why (is|was) .* (recommended|chosen|picked)", r"instead of", r"why .* (choose|chose|pick)"):
        c = need_item("why_recommendation")
        if c:
            return c
        return mk("why_recommendation", [("compare_with_cheapest", {"item_id": iid()})])
    if _has(q, r"compar", r"cheapest", r"cheaper"):
        c = need_item("compare")
        if c:
            return c
        return mk("compare", [("compare_with_cheapest", {"item_id": iid()})])
    if _has(q, r"which suppliers? (can|could|will|would)", r"who can (supply|deliver|cover)", r"suppliers? .* in time",
            r"(cover|deliver|supply) .* within \d+ days", r"alternative suppliers?"):
        c = need_item("supplier_options")
        if c:
            return c
        return mk("supplier_options", [("supplier_options", {"item_id": iid()})])
    if _has(q, r"driv(e|es|ing) (the )?demand") and (item or item_amb or PRONOUN.search(q)):
        c = need_item("forecast")
        if c:
            return c
        return mk("forecast", [("item_forecast", {"item_id": iid(), "days": 14}), ("item_impact", {"item_id": iid()})])
    if _has(q, r"which (procedures|departments)", r"what (procedures|departments)", r"depend(s|ent)? on",
            r"(affected|impact) if .* (runs? out|short)", r"runs? out\?*$") and (item or PRONOUN.search(q) or item_amb):
        c = need_item("item_impact")
        if c:
            return c
        return mk("item_impact", [("item_impact", {"item_id": iid()})])
    if _has(q, r"which (scheduled )?procedures .* (affected|at risk|hit)", r"procedures (are )?at risk"):
        return mk("procedures_at_risk", [("graph_query", {"name": "procedures_at_risk"})])
    if _has(q, r"\bwhy\b") and (item or item_amb or PRONOUN.search(q)):
        c = need_item("why_risk")
        if c:
            return c
        return mk("why_risk", [("item_status", {"item_id": iid()}), ("graph_explain", {"item_id": iid()})])
    if _has(q, r"^\s*why\b") and _has(q, r"at risk", r"run(ning|s)? out", r"short", r"stock ?out"):
        return Plan("why_risk", clarify={"kind": "item", "candidates": []})  # no item of this hospital matches the name
    if _has(q, r"forecast", r"\bdemand\b", r"how (much|many) .* (need|use|consume)") and (item or item_amb):
        c = need_item("forecast")
        if c:
            return c
        return mk("forecast", [("item_forecast", {"item_id": iid(), "days": 14})])
    if _has(q, r"how (much|many) .* (order|buy)", r"what should (we|i) (order|buy) for", r"recommend") and (item or item_amb):
        c = need_item("procurement_plan")
        if c:
            return c
        return mk("procurement_plan", [("procurement_plan", {"item_id": iid()})])
    if _has(q, r"run(ning)? out", r"stock ?out", r"at risk", r"risky", r"short(age)?") and not item:
        within = 7 if _has(q, r"next week", r"this week", r"7 days") else 1 if _has(q, r"tomorrow") else (
            int(m.group(1)) if m else None)
        return mk("top_risks", [("top_risks", {"within_days": within, "limit": 10})])
    if _has(q, r"\balerts?\b"):
        return mk("alerts", [("active_alerts", {})])
    if _has(q, r"need(s)? (a )?(procurement )?decision", r"needs attention", r"what .* order"):
        return mk("needs_attention", [("needs_attention", {})])
    if supplier and not item:
        return mk("supplier_performance", [("supplier_performance", {"supplier_id": supplier["id"]})])
    if item:
        return mk("item_status", [("item_status", {"item_id": item["id"]})])
    if item_amb:
        return Plan("item_status", clarify={"kind": "item", "candidates": item_amb})
    return Plan("help")


def run_calls(ctx: Ctx, p: Plan) -> list[ToolResult]:
    import time

    out = []
    for name, args in p.calls:
        t = time.monotonic()
        r = TOOLS[name].fn(ctx, **args)
        r.ms = int((time.monotonic() - t) * 1000)
        out.append(r)
    if p.intent == "what_if_supplier" and out and out[0].ok and p.supplier:
        recs = [x for x in out[0].data["recommendations"] if any(ln["code"] == p.supplier["code"] for ln in x["lines"])]
        for x in recs[:3]:
            t = time.monotonic()
            r = TOOLS["procurement_what_if"].fn(ctx, item_id=x["item_id"], supplier_id=p.supplier["id"],
                                                delay_days=p.days or 2)
            r.ms = int((time.monotonic() - t) * 1000)
            out.append(r)
    return out


# ---------------------------------------------------------------- composer (template answers from tool facts)


def compose(p: Plan, results: list[ToolResult], question: str) -> dict:
    """Structured answer: title, summary, points, links, follow-ups — every sentence taken from tool facts."""
    if p.refusal and p.intent == "cross_hospital":
        return {"title": "This hospital only", "summary": p.refusal, "points": [], "links": [],
                "follow_ups": ["What should I review today?", "Show me all high-risk single-source items"]}
    if p.refusal:
        return {"title": "Read-only assistant", "summary": p.refusal, "points": [],
                "links": [{"label": "Approval queue", "href": "/procurement?tab=approvals"}], "follow_ups": [
                    "Compare the current recommendation with the cheapest option", "What should I review today?"]}
    if p.clarify:
        kind, cands = p.clarify["kind"], p.clarify["candidates"]
        if cands:
            return {"title": f"Which {kind}?", "summary": f"More than one {kind} matches — which one do you mean?",
                    "points": [f"{c['name']} ({c['code']})" for c in cands], "links": [],
                    "follow_ups": [question.rstrip("?") + f" — {c['name']}?" for c in cands[:3]]}
        return {"title": f"Which {kind}?", "summary": f"I couldn't identify the {kind} in your question. Name it (or its "
                f"{'SKU' if kind == 'item' else 'code'}), e.g. " + ("'Why is Suture 2-0 at risk?'" if kind == "item"
                                                                     else "'What happens if CPS becomes unavailable?'"),
                "points": [], "links": [], "follow_ups": EXAMPLES[:3]}
    if p.intent == "help":
        return {"title": "What I can answer", "summary": "I answer operational questions by querying MedFlow's own data "
                "(inventory, forecasts, stockout risk, suppliers, procurement and the knowledge graph). I can't answer "
                "this one — try one of these:", "points": CAPABILITIES, "links": [], "follow_ups": EXAMPLES[:4]}
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    points: list[str] = []
    for r in ok:
        if p.intent == "what_if_supplier" and r.tool == "pending_recommendations":
            continue
        facts = r.facts
        if p.intent == "why_risk" and r.tool == "graph_explain":  # stock / risk / forecast come from item_status
            facts = [f"{s['title']}: {ln}" for s in r.data["steps"] if s["via"] in GRAPH_HOPS for ln in s["lines"]]
        for f in facts:
            if f not in points:
                points.append(f)
    notes = [f"{tool_label(r.tool)} unavailable: {r.error}" for r in failed]
    links = []
    for r in ok:
        for ln in r.links:
            if ln not in links:
                links.append(ln)
    by = {r.tool: r for r in ok}
    if "item_forecast" in by and "item_impact" in by:
        notes.append("The two procedure counts can differ: the forecast counts every non-cancelled procedure in its window, "
                     "including today's already-completed ones; the knowledge graph counts only procedures still scheduled "
                     "in the next 14 days. Both are shown as their pages report them.")
    summary, title = _summary(p, ok, points)
    if not ok:
        summary = "I couldn't get the data needed to answer: " + "; ".join(notes)
    return {"title": title, "summary": summary, "points": [x for x in points if x != summary][:14], "notes": notes,
            "links": links[:5], "follow_ups": FOLLOW_UPS.get(p.intent, EXAMPLES[:3])}


GRAPH_HOPS = ("USES_ITEM", "USES", "SUPPLIES", "FOR_ITEM")

TOOL_LABEL = {"graph_explain": "Knowledge graph", "supplier_impact": "Knowledge graph", "item_impact": "Knowledge graph",
              "graph_query": "Knowledge graph"}

def tool_label(tool: str) -> str:
    return TOOL_LABEL.get(tool, tool.replace("_", " ").capitalize())


FOLLOW_UPS = {
    "why_risk": ["Which suppliers can cover it within 4 days?", "Which procedures are affected if this item runs out?",
                 "Compare the current recommendation with the cheapest option for it"],
    "supplier_options": ["Why did the optimizer choose this supplier for it?", "What happens if CPS becomes unavailable?"],
    "supplier_impact": ["Show me all high-risk single-source items", "Which supplier has the most delays?"],
    "item_impact": ["Why is it at risk?", "Which suppliers can cover it within 4 days?"],
    "why_recommendation": ["What happens if CPS takes 2 additional days for it?", "Which procedures are affected if this item runs out?"],
    "compare": ["Why did the optimizer choose it?", "What should I review today?"],
    "daily_review": ["Show me all high-risk single-source items", "Which supplier has the most delays?"],
    "top_risks": ["What should I review today?", "Show me all high-risk single-source items"],
}


def _summary(p: Plan, ok: list[ToolResult], points: list[str]) -> tuple[str, str]:
    by = {r.tool: r for r in ok}
    name = (p.item or {}).get("name") or ""
    first = points[0] if points else "No data."
    if p.intent == "why_risk" and "item_status" in by:
        d = by["item_status"].data
        s = f"{d['name']} is at {d['risk_level']} stockout risk ({round(d['probability'] * 100)}% within 14 days). "
        s += (by["item_status"].facts[2] if len(by["item_status"].facts) > 2 else "")
        if "graph_explain" in by:
            hops = {st["via"]: st["lines"] for st in by["graph_explain"].data["steps"] if st["via"]}
            if hops.get("USES_ITEM"):
                s += " " + hops["USES_ITEM"][0]
            if hops.get("SUPPLIES"):
                s += " " + next((x for x in hops["SUPPLIES"] if "past orders" in x), hops["SUPPLIES"][0])
        return s, f"Why {d['name']} is at risk"
    if p.intent == "supplier_options" and "supplier_options" in by:
        f = by["supplier_options"].facts
        likely = next((x for x in f if x.startswith("Based on historical delivery performance") or x.startswith("No supplier")), None)
        return " ".join([x for x in (f[0] if f else None, likely) if x]), f"Suppliers for {by['supplier_options'].data['item']['name']}"
    if p.intent in ("why_recommendation", "compare") and "compare_with_cheapest" in by:
        f = by["compare_with_cheapest"].facts
        why = next((x for x in f if x.startswith(("Buying", "This option", "The split"))), None)
        return " ".join(x for x in (f[0], why) if x), f"Recommendation for {by['compare_with_cheapest'].data['item']['name']}"
    if p.intent == "daily_review":
        d = by.get("daily_review")
        top = d.data.get("top_risks", {}) if d else {}
        n_high = len(top.get("items", [])) if isinstance(top, dict) else 0
        pend = d.data.get("pending_recommendations", {}) if d else {}
        att = d.data.get("needs_attention", {}) if d else {}
        delays = d.data.get("active_alerts:SUPPLIER_DELAY", {}) if d else {}
        def cnt(part: dict, n: int, label: str) -> str:
            return f"{label}: unavailable" if "error" in part else f"{n} {label}"

        parts = [cnt(top, n_high, "high-risk item(s)"), cnt(att, len(att.get("items", [])), "item(s) needing a procurement decision"),
                 cnt(pend, len(pend.get("recommendations", [])), "recommendation(s) awaiting approval"),
                 cnt(delays, delays.get("total", 0), "supplier-delay alert(s)")]
        return "Today: " + ", ".join(parts) + ".", "What to review today"
    if p.intent == "what_if_supplier":
        n = sum(1 for r in ok if r.tool == "procurement_what_if")
        sup = (p.supplier or {}).get("name", "the supplier")
        if not n:
            return (f"No pending recommendation uses {sup}. Name an item to test the delay on, e.g. 'What if {p.supplier.get('code')} "
                    f"takes 2 more days for suture 2-0?'"), f"What if {sup} is late"
        return f"Effect of a delay by {sup} on the {n} pending recommendation(s) that use it:", f"What if {sup} is late"
    titles = {"supplier_impact": "Supplier unavailable", "item_impact": f"What depends on {name}",
              "single_source": "Single-source items", "top_risks": "Items at risk", "pending": "Awaiting approval",
              "supplier_delays": "Supplier delays", "what_if": "What-if", "procedures_at_risk": "Procedures at risk",
              "forecast": f"Forecast for {name}", "procurement_plan": f"Procurement plan for {name}", "alerts": "Active alerts",
              "needs_attention": "Needs a procurement decision", "glossary": "Definition",
              "supplier_performance": "Supplier performance", "item_status": name or "Item status"}
    return first, titles.get(p.intent, "Answer")
