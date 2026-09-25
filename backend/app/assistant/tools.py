"""V7 — the assistant's tool layer: typed, read-only wrappers around the existing V1–V6 endpoints.

The assistant never calculates anything operational itself. Every tool calls the same function the corresponding page
calls (so the answer and the page can never disagree), for the caller's hospital, and returns
    data   compact numbers for the answer / the LLM
    facts  plain sentences built from those numbers (no generated text)
    links  pages where the user can verify the result
Tools are read-only: none of them records, approves, orders or changes anything (tested).
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Consumable, Supplier, User

RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


@dataclass
class ToolResult:
    tool: str
    args: dict[str, Any]
    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    ms: int = 0

    def as_dict(self) -> dict:
        return {"tool": self.tool, "args": self.args, "ok": self.ok, "data": self.data, "facts": self.facts,
                "links": self.links, "error": self.error, "ms": self.ms}


@dataclass
class Ctx:
    db: Session
    user: User

    @property
    def hid(self) -> int:
        return self.user.hospital_id


def _j(v: Any) -> Any:
    return jsonable_encoder(v)


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0%}"


def _n(v: float | int | None, d: int = 0) -> str:
    return "—" if v is None else f"{v:,.{d}f}"


def _money(v: float | None) -> str:
    return "—" if v is None else f"₹{v:,.0f}" if abs(v) >= 100 else f"₹{v:,.2f}"


# ---------------------------------------------------------------- entity resolution (hospital-scoped)

_STOP = {"the", "a", "an", "of", "for", "is", "are", "at", "in", "on", "to", "and", "or", "it", "its", "this", "that",
         "why", "what", "which", "who", "how", "when", "will", "can", "could", "would", "should", "do", "does", "did",
         "me", "my", "we", "our", "us", "show", "all", "if", "be", "with", "within", "days", "day", "risk", "high",
         "low", "medium", "supplier", "suppliers", "item", "items", "procedure", "procedures", "unavailable", "stock",
         "cm", "ml", "g", "pack", "sterile", "size", "x", "by", "from", "than", "cover", "run", "out", "happens",
         "happen", "becomes", "become", "option", "options", "current", "recommendation", "cheapest", "compare",
         "choose", "chose", "optimizer", "optimiser", "instead", "affected", "much", "many", "order", "orders",
         "today", "review", "i", "you", "please", "about", "tell", "status", "more", "additional", "extra", "takes",
         "take", "gets", "delayed", "delay", "late", "reliable", "compared", "versus", "vs", "same", "only", "one"}


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", s.lower())


def resolve(ctx: Ctx, text: str, kind: str) -> list[dict]:
    """Rank this hospital's items (or suppliers) by how well their name / SKU / code matches the text."""
    if kind == "item":
        rows = [(c.id, c.name, c.sku) for c in ctx.db.scalars(select(Consumable).where(
            Consumable.hospital_id == ctx.hid, Consumable.is_active.is_(True)))]
    else:
        rows = [(s.id, s.name, s.code) for s in ctx.db.scalars(select(Supplier).where(Supplier.hospital_id == ctx.hid))]
    q = [t for t in _tokens(text) if t not in _STOP]
    if not q or not rows:
        return []
    names = {pk: set(_tokens(name)) | {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t} for pk, name, _ in rows}
    df: dict[str, int] = {}
    for toks in names.values():
        for t in toks:
            df[t] = df.get(t, 0) + 1
    out = []
    for pk, name, code in rows:
        code_l = code.lower()
        if code_l in q or code_l in text.lower().split():
            out.append({"id": pk, "name": name, "code": code, "score": 100.0})
            continue
        toks = names[pk]
        score = sum(math.log(1 + len(rows) / df[t]) for t in set(q) if t in toks)
        if score > 0:
            out.append({"id": pk, "name": name, "code": code, "score": round(score, 3)})
    out.sort(key=lambda r: (-r["score"], r["name"]))
    return out[:5]


def pick(cands: list[dict]) -> tuple[dict | None, list[dict]]:
    """Best candidate when it is clearly ahead; otherwise (None, the close candidates) to ask the user."""
    if not cands:
        return None, []
    if len(cands) == 1 or cands[0]["score"] >= 100 or cands[0]["score"] > cands[1]["score"] * 1.25:
        return cands[0], []
    top = [c for c in cands if c["score"] >= cands[0]["score"] * 0.8]
    return None, top


# ---------------------------------------------------------------- tool implementations


def _call(fn: Callable, *args, **kw):
    """Call an existing endpoint function; turn HTTP errors into tool errors (never raise into the assistant)."""
    try:
        return _j(fn(*args, **kw)), None
    except HTTPException as e:
        return None, str(e.detail)


def t_find_item(ctx: Ctx, query: str) -> ToolResult:
    c = resolve(ctx, query, "item")
    return ToolResult("find_item", {"query": query}, data={"matches": c},
                      facts=[f"Matches for '{query}': " + ", ".join(f"{x['name']} ({x['code']})" for x in c)] if c
                      else [f"No active item matches '{query}'."])


def t_find_supplier(ctx: Ctx, query: str) -> ToolResult:
    c = resolve(ctx, query, "supplier")
    return ToolResult("find_supplier", {"query": query}, data={"matches": c},
                      facts=[f"Matches for '{query}': " + ", ".join(f"{x['name']} ({x['code']})" for x in c)] if c
                      else [f"No supplier matches '{query}'."])


def t_item_status(ctx: Ctx, item_id: int) -> ToolResult:
    from app.api import risk

    r = ToolResult("item_status", {"item_id": item_id})
    d, err = _call(risk.item_risk, item_id, ctx.user, ctx.db)
    if err:
        r.ok, r.error = False, err
        return r
    unit = d["unit"]
    r.data = {k: d[k] for k in ("consumable_id", "name", "sku", "unit", "usable_stock", "reorder_level", "forecast_7",
                                "forecast_14", "forecast_30", "days_of_stock_remaining", "expected_stockout_date",
                                "shortage_quantity", "lead_time_days", "order_by_date", "probability", "risk_level",
                                "out_of_stock", "as_of", "risk_model", "forecast_model")}
    r.data["reasons"] = d["reasons"][:6]
    so = d["expected_stockout_date"]
    r.facts = [f"{d['name']}: {d['risk_level']} stockout risk, {_pct(d['probability'])} probability of a stockout within "
               f"{d['horizon_days']} days (V3 model {d['risk_model']}, as of {d['as_of']}).",
               f"Usable stock {_n(d['usable_stock'])} {unit} (reorder level {_n(d['reorder_level'])}); forecast demand "
               f"{_n(d['forecast_7'])} / {_n(d['forecast_14'])} / {_n(d['forecast_30'])} {unit} over 7 / 14 / 30 days "
               f"from today, net of units already issued today (model {d['forecast_model']})."]
    if d["out_of_stock"]:
        r.facts.append("It is out of stock now.")
    elif so:
        r.facts.append(f"Without a delivery it runs out on {so} ({_n(d['days_of_stock_remaining'])} days of stock), "
                       f"{_n(d['shortage_quantity'])} {unit} short over 14 days.")
    r.facts += [f"Reason: {x}" for x in d["reasons"][:4]]
    r.links = [{"label": "Stockout risk", "href": f"/stockout-risks?item={item_id}"},
               {"label": "Forecast", "href": f"/forecasts?item={item_id}"}]
    return r


def t_top_risks(ctx: Ctx, level: str | None = None, within_days: int | None = None, limit: int = 10) -> ToolResult:
    from app.api import risk

    r = ToolResult("top_risks", {"level": level, "within_days": within_days, "limit": limit})
    d, err = _call(risk.overview, ctx.user, ctx.db, None)
    if err or not d.get("items"):
        r.ok, r.error = False, err or d.get("message") or "no risk data"
        return r
    rows = d["items"]
    if level:
        rows = [x for x in rows if x["risk_level"] == level]
    else:
        rows = [x for x in rows if x["risk_level"] in ("HIGH", "MEDIUM")]
    if within_days is not None:
        rows = [x for x in rows if x["out_of_stock"] or (x["days_of_stock_remaining"] is not None
                                                          and x["days_of_stock_remaining"] <= within_days)]
    rows = rows[:limit]
    c = d["counts"]
    r.data = {"counts": c, "as_of": d["as_of"], "items": [
        {k: x[k] for k in ("consumable_id", "name", "sku", "unit", "risk_level", "probability", "usable_stock",
                           "days_of_stock_remaining", "expected_stockout_date", "shortage_quantity", "out_of_stock",
                           "main_reason")} for x in rows]}
    r.facts = [f"Stockout risk across {c['total']} items: {c['high']} high, {c['medium']} medium, {c['low']} low "
               f"({c['out_of_stock']} out of stock), as of {d['as_of']}."]
    for x in rows:
        when = "out of stock now" if x["out_of_stock"] else (
            f"runs out in {x['days_of_stock_remaining']} days ({x['expected_stockout_date']})"
            if x["days_of_stock_remaining"] is not None else "covered for 30 days")
        r.facts.append(f"{x['name']}: {x['risk_level']} {_pct(x['probability'])}, {when}, "
                       f"{_n(x['shortage_quantity'])} {x['unit']} short over 14 days.")
    r.links = [{"label": "Stockout risk", "href": "/stockout-risks"}]
    return r


def t_item_forecast(ctx: Ctx, item_id: int, days: int = 14) -> ToolResult:
    from app.api import forecasts

    r = ToolResult("item_forecast", {"item_id": item_id, "days": days})
    d, err = _call(forecasts.item_forecast, item_id, ctx.user, ctx.db, days)
    if err:
        r.ok, r.error = False, err
        return r
    pi = d.get("procedure_impact") or {}
    r.data = {k: d.get(k) for k in ("item", "item_id", "unit", "horizon_days", "predicted_demand", "lower", "upper",
                                    "model_version", "forecast_source", "scheduled_procedures", "procedure_driven_demand",
                                    "usable_stock", "days_of_cover", "avg_daily_last_30")}
    r.data["forecast_start"] = pi.get("start")
    r.data["procedures"] = [{k: p.get(k) for k in ("name", "department", "count", "quantity_per_procedure",
                                                   "expected_quantity")} for p in (pi.get("types") or [])[:6]]
    r.facts = [f"Forecast for {d['item']}: {_n(d['predicted_demand'])} {d['unit']} over {d['horizon_days']} days "
               f"(approximate range {_n(d['lower'])}–{_n(d['upper'])}; model {d['model_version']}, {d['forecast_source']})."]
    if d.get("scheduled_procedures"):
        start = f" from {pi['start']}" if pi.get("start") else ""
        r.facts.append(f"{d['scheduled_procedures']} scheduled procedures in the forecast window ({d['horizon_days']} days{start}) "
                       f"imply {_n(d['procedure_driven_demand'])} {d['unit']} by the kit mappings.")
    r.facts += list(d.get("explanation") or [])[:3]
    r.links = [{"label": "Forecast", "href": f"/forecasts?item={item_id}"}]
    return r


def t_supplier_options(ctx: Ctx, item_id: int) -> ToolResult:
    from app.api import supplier_intel

    r = ToolResult("supplier_options", {"item_id": item_id})
    d, err = _call(supplier_intel.item_options, item_id, ctx.user, ctx.db, 365)
    if err:
        r.ok, r.error = False, err
        return r
    r.data = {"item": d["item"], "deadline_days": d["deadline_days"], "options": [
        {k: o[k] for k in ("supplier_id", "supplier", "code", "is_active", "unit_price", "moq", "quoted_lead_time_days",
                           "typical_lead_time_days", "p90_lead_time_days", "in_time_k", "in_time_n", "p_in_time",
                           "verdict", "reliability_score", "grade", "price_vs_cheapest")} for o in d["options"]],
        "open_orders": [{k: o[k] for k in ("reference", "supplier", "quantity_ordered", "quantity_received",
                                           "expected_date", "overdue_days", "p_before_stockout")} for o in d["open_orders"]]}
    r.facts = list(d["summary"])
    r.links = [{"label": "Supplier options", "href": f"/supplier-intelligence?item={item_id}"}]
    return r


def t_supplier_performance(ctx: Ctx, supplier_id: int | None = None) -> ToolResult:
    from app.api import supplier_intel

    r = ToolResult("supplier_performance", {"supplier_id": supplier_id})
    d, err = _call(supplier_intel.scorecards, ctx.user, ctx.db, 365)
    if err:
        r.ok, r.error = False, err
        return r
    rows = [x for x in d["suppliers"] if supplier_id is None or x["supplier_id"] == supplier_id]
    if supplier_id is not None and not rows:  # V8: a supplier id outside the active hospital is simply not found
        r.ok, r.error = False, "Supplier not found"
        return r
    r.data = {"window_days": d["window_days"], "suppliers": [
        {"supplier_id": x["supplier_id"], "name": x["name"], "code": x["code"], **{k: (x["metrics"] or {}).get(k) for k in (
            "orders", "decided", "otif_rate", "reliability_score", "grade", "on_time_rate", "avg_days_late",
            "lead_time_median", "lead_time_p90", "quoted_lead_time", "cancellation_rate", "overdue", "open")}}
        for x in rows]}
    for x in r.data["suppliers"]:
        if x["orders"]:
            r.facts.append(f"{x['name']} ({x['code']}): reliability {x['grade']} · {_n(x['reliability_score'])} (OTIF "
                           f"{_pct(x['otif_rate'])} of {x['decided']} decided orders), on time {_pct(x['on_time_rate'])}, "
                           f"lead time median {_n(x['lead_time_median'], 1)} d vs quoted {_n(x['quoted_lead_time'])} d, "
                           f"cancellations {_pct(x['cancellation_rate'])}, {x['overdue']} overdue now.")
        else:
            r.facts.append(f"{x['name']} ({x['code']}): no orders recorded — no reliability evidence.")
    r.links = [{"label": "Supplier scorecards", "href": "/supplier-intelligence?tab=scorecards"}]
    return r


def t_supplier_delays(ctx: Ctx) -> ToolResult:
    perf = t_supplier_performance(ctx)
    r = ToolResult("supplier_delays", {})
    if not perf.ok:
        r.ok, r.error = False, perf.error
        return r
    rows = [x for x in perf.data["suppliers"] if x["orders"]]
    rows.sort(key=lambda x: (-(1 - (x["on_time_rate"] or 0)), -(x["overdue"] or 0)))
    r.data = {"suppliers": rows}
    for x in rows:
        late = 1 - (x["on_time_rate"] or 0)
        r.facts.append(f"{x['name']} ({x['code']}): late on {_pct(late)} of deliveries"
                       + (f", {_n(x['avg_days_late'], 1)} days late on average" if x["avg_days_late"] else "")
                       + f"; typical lead time {_n(x['lead_time_median'], 1)} d vs quoted {_n(x['quoted_lead_time'])} d; "
                       f"{x['overdue']} order(s) overdue now.")
    r.links = perf.links
    return r


def _plan(ctx: Ctx, item_id: int) -> tuple[dict | None, str | None]:
    from app.api import procurement

    return _call(procurement.item_plan, item_id, ctx.user, ctx.db)


def _scen(s: dict) -> dict:
    return {"key": s["key"], "label": s["label"], "tags": s["tags"], "quantity": s["quantity"],
            "purchase_value": s["purchase_value"], "expected_total_cost": s["costs"]["total"],
            "p_stockout": s["metrics"]["p_stockout"], "expected_shortage": s["metrics"]["expected_shortage"],
            "arrival_date": s["metrics"]["arrival_date"],
            "lines": [{k: ln[k] for k in ("code", "supplier", "quantity", "unit_price", "window_k", "window_n",
                                          "p_arrive_by_need")} for ln in s["lines"]]}


def t_procurement_plan(ctx: Ctx, item_id: int) -> ToolResult:
    r = ToolResult("procurement_plan", {"item_id": item_id})
    d, err = _plan(ctx, item_id)
    if err:
        r.ok, r.error = False, err
        return r
    rec = next(s for s in d["scenarios"] if s["key"] == d["recommended_key"])
    rep = d["replenishment"]
    r.data = {"item": d["item"], "horizon_days": d["horizon_days"], "need_by_date": d["need_by_date"],
              "recommended": _scen(rec), "scenarios": [_scen(s) for s in d["scenarios"][:6]],
              "replenishment": {k: rep[k] for k in ("required_quantity", "moq_adjusted_quantity", "safety_stock",
                                                    "demand_over_cover", "cover_days", "usable_stock", "expected_incoming",
                                                    "order_by_date")}}
    r.facts = list(d["explanation"])
    r.links = [{"label": "Procurement scenarios", "href": f"/procurement?tab=scenarios&item={item_id}"}]
    return r


def t_compare_with_cheapest(ctx: Ctx, item_id: int) -> ToolResult:
    r = ToolResult("compare_with_cheapest", {"item_id": item_id})
    d, err = _plan(ctx, item_id)
    if err:
        r.ok, r.error = False, err
        return r
    rec = next(s for s in d["scenarios"] if s["key"] == d["recommended_key"])
    cheap = next((s for s in d["scenarios"] if "cheapest" in s["tags"]), None)
    none = next(s for s in d["scenarios"] if s["kind"] == "none")
    r.data = {"item": d["item"], "recommended": _scen(rec), "cheapest": _scen(cheap) if cheap else None, "no_order": _scen(none)}
    u = d["item"]["unit"]
    r.facts.append(f"Recommended: {rec['label']} — purchase {_money(rec['purchase_value'])}, expected total cost "
                   f"{_money(rec['costs']['total'])}, stockout probability {_pct(rec['metrics']['p_stockout'])}, expected "
                   f"shortage {_n(rec['metrics']['expected_shortage'], 1)} {u}.")
    if cheap and cheap["key"] != rec["key"]:
        r.facts.append(f"Cheapest-price option: {cheap['label']} — purchase {_money(cheap['purchase_value'])}, expected "
                       f"total cost {_money(cheap['costs']['total'])}, stockout probability {_pct(cheap['metrics']['p_stockout'])}, "
                       f"expected shortage {_n(cheap['metrics']['expected_shortage'], 1)} {u}.")
        for ln in rec["lines"] + cheap["lines"]:
            if ln["window_n"]:
                r.facts.append(f"{ln['code']} delivered within the required window in {ln['window_k']} of {ln['window_n']} "
                               f"past orders (P = {_pct(ln['p_arrive_by_need'])}).")
    elif cheap:
        r.facts.append("The recommended option is also the cheapest-price option.")
    r.facts.append(f"No new order: stockout probability {_pct(none['metrics']['p_stockout'])}, expected shortage "
                   f"{_n(none['metrics']['expected_shortage'], 1)} {u}.")
    r.facts += [x for x in d["explanation"] if x.startswith(("This option", "Buying", "Expected shortage", "The split"))]
    r.links = [{"label": "Procurement scenarios", "href": f"/procurement?tab=scenarios&item={item_id}"}]
    return r


def t_procurement_what_if(ctx: Ctx, item_id: int, supplier_id: int | None = None, delay_days: int = 0,
                          demand_change_pct: float = 0) -> ToolResult:
    from app.api import procurement
    from app.schemas.procurement import WhatIfIn

    args = {"item_id": item_id, "supplier_id": supplier_id, "delay_days": delay_days, "demand_change_pct": demand_change_pct}
    r = ToolResult("procurement_what_if", args)
    body = WhatIfIn(delays=[{"supplier_id": supplier_id, "days": delay_days}] if supplier_id and delay_days else [],
                    demand_change_pct=demand_change_pct)
    d, err = _call(procurement.what_if, item_id, body, ctx.user, ctx.db)
    if err:
        r.ok, r.error = False, err
        return r
    rec = next((s for s in d["scenarios"] if s["key"] == d["baseline_recommended"]), None)
    r.data = {"item": d["item"], "horizon_days": d["horizon_days"], "recommended": rec, "whatif_best": d["whatif_best"],
              "replanned_recommended": d["replanned_recommended"]}
    r.facts = list(d["summary"])
    r.links = [{"label": "What-if simulator", "href": f"/procurement?tab=what-if&item={item_id}"}]
    return r


def t_pending_recommendations(ctx: Ctx) -> ToolResult:
    from app.api import procurement

    r = ToolResult("pending_recommendations", {})
    d, err = _call(procurement.list_recommendations, ctx.user, ctx.db, "PENDING", 200)
    if err:
        r.ok, r.error = False, err
        return r
    rows = [{"id": x["id"], "item": x["item"]["name"], "item_id": x["item"]["id"], "lines": [
        {"code": ln["code"], "quantity": ln["quantity"]} for ln in x["lines"]], "purchase_value": x["purchase_value"],
        "expected_cost": x["expected_cost"], "p_stockout": x["p_stockout"], "order_by_date": x["order_by_date"],
        "is_current": x["is_current"]} for x in d]
    r.data = {"recommendations": rows, "with_order": sum(1 for x in rows if x["lines"]),
              "purchase_value": round(sum(x["purchase_value"] for x in rows), 2)}
    r.facts = [f"{len(rows)} recommendation(s) awaiting approval, {r.data['with_order']} with an order, purchase value "
               f"{_money(r.data['purchase_value'])}."]
    for x in rows[:8]:
        what = " + ".join(f"{ln['quantity']:,} from {ln['code']}" for ln in x["lines"]) or "no new order"
        r.facts.append(f"#{x['id']} {x['item']}: {what}" + (f", order by {x['order_by_date']}" if x["order_by_date"] else "")
                       + ("" if x["is_current"] else " (out of date — regenerate)") + ".")
    r.links = [{"label": "Approval queue", "href": "/procurement?tab=approvals"}]
    return r


def t_needs_attention(ctx: Ctx) -> ToolResult:
    from app.api import procurement

    r = ToolResult("needs_attention", {})
    d, err = _call(procurement.needs_attention, ctx.user, ctx.db)
    if err:
        r.ok, r.error = False, err
        return r
    rows = d["items"]
    r.data = {"as_of": d["as_of"], "items": [
        {k: x[k] for k in ("risk_level", "usable_stock", "order_by_date", "need_by_date", "p_stockout_no_order",
                           "required_quantity", "moq_adjusted_quantity", "reference_supplier", "overdue_orders")}
        | {"item": x["item"]["name"], "item_id": x["item"]["id"]} for x in rows]}
    overdue = [x for x in rows if x["order_by_date"] and x["order_by_date"] <= str(d["as_of"])]
    r.facts = [f"{len(rows)} item(s) need a procurement decision this review cycle; {len(overdue)} are at or past their "
               f"order-by date (in-transit orders counted at their arrival probability)."]
    for x in rows[:8]:
        r.facts.append(f"{x['item']['name']}: order by {x['order_by_date'] or '—'}, stockout probability without a new "
                       f"order {_pct(x['p_stockout_no_order'])}, quantity needed {_n(x['required_quantity'])} "
                       f"(MOQ-adjusted {_n(x['moq_adjusted_quantity'])}).")
    r.links = [{"label": "Needs attention", "href": "/procurement"}]
    return r


def _graph(fn, *args) -> tuple[dict | None, str | None]:
    d, err = _call(fn, *args)
    return (d["data"] if d else None), err


def t_graph_explain(ctx: Ctx, item_id: int) -> ToolResult:
    from app.api import graph

    r = ToolResult("graph_explain", {"item_id": item_id})
    d, err = _graph(graph.explain, item_id, ctx.user, ctx.db)
    if err:
        r.ok, r.error = False, err
        return r
    r.data = {"summary": d["summary"], "steps": [{"title": s["title"], "via": s["via"], "lines": s["lines"]} for s in d["steps"]]}
    r.facts = [d["summary"]] + [f"[{s['via'] or 'Item'}] {ln}" for s in d["steps"] for ln in s["lines"]]
    r.links = [{"label": "Knowledge graph", "href": f"/knowledge-graph?item={item_id}"}]
    return r


def t_supplier_impact(ctx: Ctx, supplier_id: int) -> ToolResult:
    from app.api import graph

    r = ToolResult("supplier_impact", {"supplier_id": supplier_id})
    d, err = _graph(graph.supplier_impact, supplier_id, ctx.user, ctx.db)
    if err:
        r.ok, r.error = False, err
        return r
    r.data = {"supplier": d["supplier"], "items": [{k: i[k] for k in ("item", "sku", "sole_source", "severity", "alternatives",
                                                                      "risk_level", "risk_probability", "days_left")}
                                                   for i in d["items"]],
              "procedures": [{k: p[k] for k in ("procedure", "department", "scheduled_next_14", "sole_source_items")}
                             for p in d["procedures"][:10]],
              "departments": [{k: x[k] for k in ("department", "items", "units_90")} for x in d["departments"]],
              "open_orders": d["open_orders"], "recommendations": d["recommendations"]}
    r.facts = list(d["summary"])
    for i in d["items"][:8]:
        alt = "no active alternative (sole source)" if i["sole_source"] else "alternatives: " + ", ".join(i["alternatives"])
        r.facts.append(f"{i['item']}: {i['severity']} — {alt}; current risk {i['risk_level'] or '—'} "
                       f"({_pct(i['risk_probability'])}).")
    for c in d["chains"][:3]:
        r.facts.append("Chain: " + " → ".join(f"{x['name']}" + (f" ({x['detail']})" if x["detail"] else "") for x in c))
    r.links = [{"label": "Impact analysis", "href": f"/knowledge-graph?tab=impact&mode=supplier&supplier={supplier_id}"}]
    return r


def t_item_impact(ctx: Ctx, item_id: int) -> ToolResult:
    from app.api import graph

    r = ToolResult("item_impact", {"item_id": item_id})
    d, err = _graph(graph.item_impact, item_id, ctx.user, ctx.db)
    if err:
        r.ok, r.error = False, err
        return r
    r.data = {"item": d["item"], "units_needed_14": d["units_needed_14"],
              "procedures": [{k: p[k] for k in ("procedure", "department", "scheduled_next_14", "per_procedure",
                                                "units_next_14", "procedures_covered_by_stock")} for p in d["procedures"]],
              "departments": [{k: x[k] for k in ("department", "units_90", "share", "procedures")} for x in d["departments"]]}
    u = d["item"]["unit"]
    r.facts = list(d["summary"])
    for p in d["procedures"][:6]:
        r.facts.append(f"{p['procedure']} ({p['department'] or '—'}): {_n(p['per_procedure'], 1)} {u} per procedure, "
                       f"{p['scheduled_next_14']} scheduled in 14 days = {_n(p['units_next_14'])} {u}.")
    r.links = [{"label": "Impact analysis", "href": f"/knowledge-graph?tab=impact&mode=item&item={item_id}"}]
    return r


GRAPH_QUERIES = {"risky_single_source", "procedures_at_risk", "single_source_items", "departments_exposed",
                 "overdue_orders_on_risky_items"}


def t_graph_query(ctx: Ctx, name: str) -> ToolResult:
    from app.api import graph
    from app.schemas.graph import QueryIn

    r = ToolResult("graph_query", {"name": name})
    if name not in GRAPH_QUERIES:
        r.ok, r.error = False, f"unknown query (one of {sorted(GRAPH_QUERIES)})"
        return r
    d, err = _graph(graph.run_query, name, QueryIn(), ctx.user, ctx.db)
    if err:
        r.ok, r.error = False, err
        return r
    r.data = {"title": d["title"], "columns": d["columns"], "rows": d["rows"][:20], "cypher": d["cypher"]}
    r.facts = [f"{d['title']} — {len(d['rows'])} result(s)."]
    for row in d["rows"][:10]:
        r.facts.append("; ".join(f"{k.replace('_', ' ')}: {', '.join(map(str, v)) if isinstance(v, list) else v}"
                                 for k, v in row.items()))
    r.links = [{"label": "Graph search", "href": "/knowledge-graph?tab=search"}]
    return r


def t_active_alerts(ctx: Ctx, alert_type: str | None = None, limit: int = 15) -> ToolResult:
    from app.api import alerts
    from app.models import AlertType

    r = ToolResult("active_alerts", {"alert_type": alert_type})
    at = AlertType(alert_type) if alert_type in {a.value for a in AlertType} else None
    from app.schemas.inventory import AlertOut

    page = alerts.list_alerts(ctx.user, ctx.db, "active", at, None, None, 1, limit)
    items = [_j(AlertOut.model_validate(a)) for a in page.items]
    rows = [{"type": a["alert_type"], "severity": a["severity"], "title": a["title"], "message": a["message"]} for a in items]
    r.data = {"total": page.total, "alerts": rows}
    d = {"total": page.total}
    counts: dict[str, int] = {}
    for a in rows:
        counts[a["type"]] = counts.get(a["type"], 0) + 1
    r.facts = [f"{d['total']} active alert(s)" + (f" of type {alert_type}" if at else "") + "."]
    r.facts += [f"{a['severity']} · {a['title']}: {a['message']}" for a in rows[:8]]
    r.links = [{"label": "Alerts", "href": "/alerts"}]
    return r


def t_daily_review(ctx: Ctx) -> ToolResult:
    """What should procurement / inventory review today? (composition of existing views; no new scoring)"""
    parts = [t_top_risks(ctx, "HIGH", None, 8), t_needs_attention(ctx), t_pending_recommendations(ctx),
             t_active_alerts(ctx, "SUPPLIER_DELAY", 10), t_active_alerts(ctx, "EXPIRING_SOON", 5)]
    r = ToolResult("daily_review", {}, data={p.tool + (f":{p.args.get('alert_type')}" if p.tool == "active_alerts" else ""):
                                             (p.data if p.ok else {"error": p.error}) for p in parts})
    for p in parts:
        r.facts += p.facts[:6] if p.ok else [f"{p.tool}: unavailable ({p.error})"]
        r.links += p.links
    return r


# ---------------------------------------------------------------- retrieval: method definitions (curated from docs/)

GLOSSARY = {
    "otif": "OTIF (on time in full): an order delivered in full by its expected date. The V4 reliability score is the "
            "OTIF rate over decided orders, pulled towards the hospital average with 5 pseudo-orders, shown with a 95 % "
            "Wilson interval.",
    "reliability score": "V4 reliability score = 100 × (OTIF orders + 5 × hospital OTIF rate) ÷ (decided orders + 5). "
                         "Grades: A ≥ 90, B ≥ 80, C ≥ 65, D below.",
    "safety stock": "V5 safety stock = z × √((lead time + review period) × σ_d² + mean demand² × σ_lead²); z comes from the "
                    "service level (default 95 %), σ_d is the served forecast's daily holdout error.",
    "stockout risk": "V3 stockout risk: probability that usable stock runs out within 14 days, from an XGBoost classifier "
                     "trained on labelled synthetic replenishment histories and evaluated on the hospital's ledger; a "
                     "lead-time rule raises the level when an order placed now would arrive too late.",
    "forecast": "V2 demand forecast: global XGBoost (V2B adds scheduled procedures × kit quantities) compared with moving "
                "averages on a 14-day holdout; the lowest-WAPE model is served. Intervals are approximate.",
    "wape": "WAPE (weighted absolute percentage error) = Σ|actual − forecast| ÷ Σ actual over the holdout.",
    "expected total cost": "V5 expected total cost = purchase + expected stockout + expected holding + expected "
                           "expiry/waste − stock carried forward; every parameter is on the Procurement → Cost model tab.",
    "cost model": "V5 cost model parameters: service level, review period, horizon, stockout multiplier (5× reference "
                  "price by default), holding rate (25 %/year), disposal %, fixed order cost, split orders, budget, "
                  "Monte Carlo paths. Placeholders to be set by each hospital.",
    "moq": "MOQ (minimum order quantity): the smallest quantity a supplier accepts; V5 rounds required quantities up to it "
           "and warns when that exceeds what can be used before expiry or stored.",
    "in transit": "V5 counts orders already in transit at their historical probability of arriving in time (conditional on "
                  "days already elapsed) — never as certain; with no comparable history they are not counted.",
    "knowledge graph": "V6 knowledge graph: a projection of PostgreSQL (the source of truth) into a graph store — items, "
                       "procedures, departments, suppliers, forecasts, risks, orders and recommendations — used for "
                       "explanation chains, impact analysis and predefined Cypher queries.",
    "what-if": "V5 what-if: re-runs the same procurement scenarios on the same simulated demand with a supplier's "
               "deliveries later or demand changed; nothing is stored.",
}


def t_glossary(ctx: Ctx, term: str) -> ToolResult:
    low = term.lower()
    q = set(_tokens(term))
    exact = [k for k in GLOSSARY if k in low]
    best = sorted(((len(q & set(_tokens(k + " " + v[:60]))), k) for k, v in GLOSSARY.items() if k not in exact), reverse=True)
    hits = (sorted(exact, key=len, reverse=True) + [k for sc, k in best if sc > 0])[:2]
    r = ToolResult("glossary", {"term": term}, data={"terms": hits})
    r.facts = [GLOSSARY[k] for k in hits] or [f"No definition found for '{term}'."]
    r.ok = bool(hits)
    r.links = [{"label": "Method documentation", "href": "/procurement?tab=cost-model"}] if hits else []
    return r


# ---------------------------------------------------------------- registry (the only functions the assistant can call)


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., ToolResult]
    description: str
    params: dict[str, dict]  # JSON-schema properties
    required: list[str] = field(default_factory=list)

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": {"type": "object", "properties": self.params, "required": self.required,
                               "additionalProperties": False}}


ID = {"type": "integer", "minimum": 1}
TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("find_item", t_find_item, "Find this hospital's items by name or SKU.",
             {"query": {"type": "string"}}, ["query"]),
    ToolSpec("find_supplier", t_find_supplier, "Find this hospital's suppliers by name or code.",
             {"query": {"type": "string"}}, ["query"]),
    ToolSpec("item_status", t_item_status, "Current stock, V3 stockout risk (probability, date, shortage, reasons) and V2 "
             "forecast totals for one item.", {"item_id": ID}, ["item_id"]),
    ToolSpec("top_risks", t_top_risks, "Items at stockout risk (default HIGH+MEDIUM), optionally running out within N days.",
             {"level": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]}, "within_days": {"type": "integer", "minimum": 0},
              "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
    ToolSpec("item_forecast", t_item_forecast, "V2 demand forecast for an item incl. scheduled-procedure demand.",
             {"item_id": ID, "days": {"type": "integer", "minimum": 1, "maximum": 30}}, ["item_id"]),
    ToolSpec("supplier_options", t_supplier_options, "V4: which suppliers can deliver the item before its projected "
             "stockout, from their own delivery history (k of n), plus orders in transit.", {"item_id": ID}, ["item_id"]),
    ToolSpec("supplier_performance", t_supplier_performance, "V4 reliability scorecards (OTIF, on-time, lead time, "
             "cancellations) for one or all suppliers.", {"supplier_id": ID}),
    ToolSpec("supplier_delays", t_supplier_delays, "Suppliers ranked by late deliveries and overdue orders.", {}),
    ToolSpec("procurement_plan", t_procurement_plan, "V5 plan for an item: required quantity, scenarios, recommended "
             "option and its explanation.", {"item_id": ID}, ["item_id"]),
    ToolSpec("compare_with_cheapest", t_compare_with_cheapest, "V5: recommended option vs the cheapest-price option vs no "
             "order for an item.", {"item_id": ID}, ["item_id"]),
    ToolSpec("procurement_what_if", t_procurement_what_if, "V5 what-if: re-evaluate the item's scenarios if a supplier "
             "delivers N days later and/or demand changes.",
             {"item_id": ID, "supplier_id": ID, "delay_days": {"type": "integer", "minimum": 0, "maximum": 60},
              "demand_change_pct": {"type": "number", "minimum": -90, "maximum": 300}}, ["item_id"]),
    ToolSpec("pending_recommendations", t_pending_recommendations, "V5 recommendations awaiting human approval.", {}),
    ToolSpec("needs_attention", t_needs_attention, "V5 items needing a procurement decision this review cycle.", {}),
    ToolSpec("graph_explain", t_graph_explain, "V6 knowledge-graph explanation chain for an item's risk.",
             {"item_id": ID}, ["item_id"]),
    ToolSpec("supplier_impact", t_supplier_impact, "V6: what is affected if a supplier becomes unavailable.",
             {"supplier_id": ID}, ["supplier_id"]),
    ToolSpec("item_impact", t_item_impact, "V6: which procedures and departments depend on an item.", {"item_id": ID}, ["item_id"]),
    ToolSpec("graph_query", t_graph_query, "V6 predefined graph question.",
             {"name": {"type": "string", "enum": sorted(GRAPH_QUERIES)}}, ["name"]),
    ToolSpec("active_alerts", t_active_alerts, "Active alerts, optionally of one type.",
             {"alert_type": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
    ToolSpec("daily_review", t_daily_review, "What to review today: high risks, items needing a decision, pending "
             "approvals, supplier delays, expiring stock.", {}),
    ToolSpec("glossary", t_glossary, "Definitions of MedFlow methods and metrics (OTIF, safety stock, cost model…).",
             {"term": {"type": "string"}}, ["term"]),
]}
