"""V6.3–V6.5 — questions answered by traversing the graph (openCypher; same text on Neo4j and FalkorDB).

Every query is anchored on this hospital's nodes (keys embed the hospital id; list queries filter on hospital_id), returns
plain values, and is shown to the user next to its result. Explanations are assembled from the returned numbers — no
generated text, no LLM, no graph ML.
"""

from typing import Any

from app.graph.store import Executor

RISKY = ["HIGH", "MEDIUM"]


def key(hid: int, label: str, pk: int | str) -> str:
    return f"{hid}:{label}:{pk}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0%}"


def _n(v: float | int | None, d: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:,.{d}f}"


def _days(n: int | None) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


# ---------------------------------------------------------------- V6.3 explanation chain

EXPLAIN_CYPHER = {
    "item": """MATCH (i:Item {key: $item})
OPTIONAL MATCH (i)-[:HAS_RISK]->(r:StockoutRisk)
OPTIONAL MATCH (i)-[:HAS_FORECAST]->(f:Forecast)
RETURN properties(i) AS item, properties(r) AS risk, properties(f) AS forecast""",
    "procedures": """MATCH (p:Procedure)-[u:USES_ITEM]->(i:Item {key: $item})
OPTIONAL MATCH (d:Department)-[:PERFORMS]->(p)
RETURN p.pg_id AS id, p.name AS procedure, d.name AS department, d.pg_id AS department_id,
       p.scheduled_next_14 AS scheduled_next_14, u.quantity_per_procedure AS per_procedure,
       u.units_next_14 AS units_next_14
ORDER BY units_next_14 DESC, procedure""",
    "departments": """MATCH (d:Department)-[u:USES]->(i:Item {key: $item})
RETURN d.pg_id AS id, d.name AS department, u.units_90 AS units_90, u.share AS share, u.last_used AS last_used
ORDER BY units_90 DESC""",
    "suppliers": """MATCH (s:Supplier)-[e:SUPPLIES]->(i:Item {key: $item})
RETURN s.pg_id AS id, s.name AS supplier, s.code AS code, s.is_active AS active, s.reliability_score AS reliability,
       s.grade AS grade, e.unit_price AS unit_price, e.moq AS moq, e.quoted_lead_time_days AS quoted_lead_time,
       e.lead_time_median AS lead_time_median, e.lead_time_p90 AS lead_time_p90, e.is_preferred AS preferred,
       e.window_days AS window_days, e.window_k AS window_k, e.window_n AS window_n, e.evidence_basis AS basis,
       e.item_orders AS item_orders
ORDER BY window_k DESC, reliability DESC""",
    "orders": """MATCH (s:Supplier)-[:HAS_ORDER]->(o:SupplierOrder {is_open: true})-[:FOR_ITEM]->(i:Item {key: $item})
RETURN o.pg_id AS id, o.reference AS reference, s.name AS supplier, o.outstanding AS outstanding,
       o.expected_date AS expected_date, o.overdue_days AS overdue_days
ORDER BY expected_date""",
    "recommendation": """MATCH (r:ProcurementRecommendation)-[:FOR_ITEM]->(i:Item {key: $item})
WHERE r.status IN ['PENDING', 'APPROVED']
OPTIONAL MATCH (r)-[u:USES_SUPPLIER]->(s:Supplier)
WITH r, collect({supplier: s.name, code: s.code, quantity: u.quantity, unit_price: u.unit_price}) AS lines
RETURN properties(r) AS rec, lines
ORDER BY r.pg_id DESC LIMIT 1""",
}


def explain_item(ex: Executor, hid: int, item_id: int) -> dict:
    k = key(hid, "Item", item_id)
    head = ex.run(EXPLAIN_CYPHER["item"], {"item": k})
    if not head:
        return {"found": False}
    item, risk, fc = head[0]["item"], head[0]["risk"], head[0]["forecast"]
    procs = ex.run(EXPLAIN_CYPHER["procedures"], {"item": k})
    depts = ex.run(EXPLAIN_CYPHER["departments"], {"item": k})
    sups = ex.run(EXPLAIN_CYPHER["suppliers"], {"item": k})
    orders = ex.run(EXPLAIN_CYPHER["orders"], {"item": k})
    recs = ex.run(EXPLAIN_CYPHER["recommendation"], {"item": k})
    unit = item.get("unit") or "units"
    steps: list[dict[str, Any]] = []

    def step(kind: str, title: str, lines: list[str], nodes: list[dict], rel: str | None = None):
        steps.append({"kind": kind, "title": title, "lines": [x for x in lines if x], "nodes": nodes, "via": rel})

    usable = item.get("usable_stock", 0)
    step("item", item["name"], [f"Usable stock {_n(usable)} {unit} (reorder level {_n(item.get('reorder_level'))})"
                                + (f"; {_n(item.get('expired_stock'))} {unit} expired" if item.get("expired_stock") else "") + "."],
         [{"label": "Item", "id": item_id, "name": item["name"]}])
    if risk:
        so = risk.get("expected_stockout_date")
        left = risk.get("days_of_stock_remaining")
        lines = [f"{risk['level']} stockout risk: {_pct(risk.get('probability'))} probability within 14 days (V3, {risk.get('source')})."]
        if usable <= 0:
            lines.append("Out of stock now.")
        elif so:
            lines.append(f"Projected to run out on {so} ({_days(left)} of stock), {_n(risk.get('shortage_14'))} {unit} short "
                         f"over 14 days without a delivery.")
        else:
            lines.append("Stock covers the 30-day forecast.")
        if risk.get("reasons"):
            lines.append(risk["reasons"][0])
        step("risk", "Stockout risk", lines, [{"label": "StockoutRisk", "id": risk.get("pg_id"), "name": risk["level"]}], "HAS_RISK")
    if fc:
        lines = [f"Forecast demand: {_n(fc.get('forecast_7'))} / {_n(fc.get('forecast_14'))} / {_n(fc.get('forecast_30'))} {unit} "
                 f"over 7 / 14 / 30 days ({fc.get('source')} model {fc.get('model')}"
                 + (f", item holdout WAPE {_pct(fc.get('item_wape'))}" if fc.get("item_wape") is not None else "") + ")."]
        if usable and fc.get("forecast_14"):
            lines.append(f"Usable stock covers {min(usable / fc['forecast_14'], 9.99):.0%} of the next 14 days' demand."
                         if usable < fc["forecast_14"] else "Usable stock exceeds the next 14 days' demand.")
        step("forecast", "Forecast demand", lines, [{"label": "Forecast", "id": fc.get("pg_id"), "name": fc.get("model")}],
             "HAS_FORECAST")
    active = [p for p in procs if (p.get("scheduled_next_14") or 0) > 0]
    if procs:
        total = sum(p.get("units_next_14") or 0 for p in procs)
        lines = []
        if active:
            n_proc = sum(p["scheduled_next_14"] for p in active)
            share = f" — {total / fc['forecast_14']:.0%} of the 14-day forecast" if fc and fc.get("forecast_14") else ""
            lines.append(f"{n_proc} scheduled procedures in the next 14 days use it: {_n(total)} {unit} by the kit mappings{share}.")
            for p in active[:4]:
                lines.append(f"{p['procedure']} ({p['department'] or '—'}): {p['scheduled_next_14']} scheduled × "
                             f"{_n(p['per_procedure'], 1)} = {_n(p['units_next_14'])} {unit}.")
        else:
            lines.append(f"Used by {len(procs)} procedure type(s) (kit mappings); none scheduled in the next 14 days.")
        step("procedures", "Scheduled procedures", lines,
             [{"label": "Procedure", "id": p["id"], "name": p["procedure"]} for p in (active or procs)[:6]], "USES_ITEM")
    if depts:
        lines = [f"{d['department']}: {_n(d['units_90'])} {unit} issued in the last 90 days ({_pct(d['share'])})." for d in depts[:4]]
        step("departments", "Departments using it", lines,
             [{"label": "Department", "id": d["id"], "name": d["department"]} for d in depts[:6]], "USES")
    if sups:
        act = [s for s in sups if s["active"]]
        lines = []
        if len(act) == 1:
            lines.append(f"Single source: only {act[0]['supplier']} supplies it.")
        elif not act:
            lines.append("No active supplier lists this item.")
        for s in sups[:4]:
            ev = (f"{s['window_k']}/{s['window_n']} past orders delivered within {_days(s['window_days'])}"
                  if s.get("window_n") else "no delivery evidence for the current window" if s.get("window_days") is not None
                  else f"{_n(s.get('item_orders'))} past orders for this item")
            lead = f"typically {_n(s['lead_time_median'], 1)} d (90%: {_n(s['lead_time_p90'], 1)} d)" if s.get("lead_time_median") \
                else f"quoted {s['quoted_lead_time']} d"
            lines.append(f"{s['supplier']} ({s['code']}{', preferred' if s['preferred'] else ''}{'' if s['active'] else ', inactive'}): "
                         f"₹{s['unit_price']:,.2f}, MOQ {_n(s['moq'])}, {lead}; {ev}"
                         + (f"; reliability {s['grade']} · {s['reliability']:.0f}" if s.get("grade") else "") + ".")
        step("suppliers", "Suppliers & delivery history", lines,
             [{"label": "Supplier", "id": s["id"], "name": s["supplier"]} for s in sups], "SUPPLIES")
    if orders:
        lines = [f"{o['reference']} from {o['supplier']}: {_n(o['outstanding'])} {unit} outstanding, expected {o['expected_date']}"
                 + (f" — {o['overdue_days']} days overdue" if o.get("overdue_days") else "") + "." for o in orders]
        step("orders", "Orders in transit", lines,
             [{"label": "SupplierOrder", "id": o["id"], "name": o["reference"]} for o in orders], "FOR_ITEM")
    if recs:
        r, ln = recs[0]["rec"], [x for x in recs[0]["lines"] if x.get("supplier")]
        what = " + ".join(f"{_n(x['quantity'])} from {x['code']}" for x in ln) or "no new order"
        lines = [f"V5 {r['status'].lower()} recommendation #{r['pg_id']}: {what}; purchase ₹{_n(r.get('purchase_value'))}, "
                 f"expected cost ₹{_n(r.get('expected_cost'))}; stockout probability {_pct(r.get('p_stockout'))} with it vs "
                 f"{_pct(r.get('no_order_p_stockout'))} without."]
        step("procurement", "Procurement option", lines,
             [{"label": "ProcurementRecommendation", "id": r["pg_id"], "name": f"#{r['pg_id']}"}], "FOR_ITEM")
    summary = _summary(item, risk, fc, active, sups, orders, recs, unit)
    return {"found": True, "item": {"id": item_id, "name": item["name"], "sku": item.get("sku"), "unit": unit},
            "summary": summary, "steps": steps, "graph": _subgraph(item_id, item, risk, fc, procs, depts, sups, orders, recs),
            "cypher": EXPLAIN_CYPHER}


def _summary(item, risk, fc, procs, sups, orders, recs, unit) -> str:
    parts = [item["name"]]
    if risk:
        parts.append(f"{risk['level'].lower()} risk ({_pct(risk.get('probability'))})")
    if fc and fc.get("forecast_14"):
        parts.append(f"{_n(fc['forecast_14'])} {unit} forecast for 14 days vs {_n(item.get('usable_stock'))} usable")
    if procs:
        parts.append(f"{sum(p['scheduled_next_14'] for p in procs)} scheduled procedures depend on it")
    act = [s for s in sups if s["active"]]
    if len(act) == 1:
        parts.append(f"single source ({act[0]['code']})")
    best = next((s for s in sups if s.get("window_n")), None)
    if best:
        parts.append(f"best delivery record {best['code']} {best['window_k']}/{best['window_n']} in the window")
    if orders:
        od = sum(1 for o in orders if o.get("overdue_days"))
        parts.append(f"{len(orders)} order(s) in transit" + (f", {od} overdue" if od else ""))
    if recs:
        parts.append(f"procurement recommendation {recs[0]['rec']['status'].lower()}")
    return " → ".join(parts) + "."


def _subgraph(item_id, item, risk, fc, procs, depts, sups, orders, recs) -> dict:
    """Nodes in columns (for a layered drawing) and the relationships between them."""
    nodes, edges = [], []

    def add(nid, label, name, column, detail=""):
        if not any(n["id"] == nid for n in nodes):
            nodes.append({"id": nid, "label": label, "name": name, "column": column, "detail": detail})

    it = f"Item:{item_id}"
    add(it, "Item", item["name"], 2, f"usable {_n(item.get('usable_stock'))}")
    if risk:
        add("risk", "StockoutRisk", f"{risk['level']} risk", 2, _pct(risk.get("probability")))
        edges.append({"from": it, "to": "risk", "type": "HAS_RISK"})
    if fc:
        add("forecast", "Forecast", "Forecast 14 d", 2, _n(fc.get("forecast_14")))
        edges.append({"from": it, "to": "forecast", "type": "HAS_FORECAST"})
    for s in sups:
        sid = f"Supplier:{s['id']}"
        add(sid, "Supplier", s["supplier"], 0, f"{s['window_k']}/{s['window_n']}" if s.get("window_n") else s["code"])
        edges.append({"from": sid, "to": it, "type": "SUPPLIES"})
    for o in orders:
        oid = f"SupplierOrder:{o['id']}"
        late = f", {o['overdue_days']} d late" if o.get("overdue_days") else ""
        add(oid, "SupplierOrder", o["reference"], 1, f"{_n(o['outstanding'])} out{late}")
        edges.append({"from": oid, "to": it, "type": "FOR_ITEM"})
    if recs:
        r = recs[0]["rec"]
        rid = f"ProcurementRecommendation:{r['pg_id']}"
        add(rid, "ProcurementRecommendation", f"Recommendation #{r['pg_id']}", 1, r["status"].lower())
        edges.append({"from": rid, "to": it, "type": "FOR_ITEM"})
    for p in procs[:8]:
        pid = f"Procedure:{p['id']}"
        add(pid, "Procedure", p["procedure"], 3, f"{p['scheduled_next_14'] or 0} scheduled")
        edges.append({"from": pid, "to": it, "type": "USES_ITEM"})
        if p.get("department_id"):
            did = f"Department:{p['department_id']}"
            add(did, "Department", p["department"], 4)
            edges.append({"from": did, "to": pid, "type": "PERFORMS"})
    for d in depts[:6]:
        did = f"Department:{d['id']}"
        add(did, "Department", d["department"], 4, f"{_pct(d['share'])} of use")
        edges.append({"from": did, "to": it, "type": "USES"})
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------- V6.4 impact analysis

SUPPLIER_IMPACT_CYPHER = {
    "supplier": "MATCH (s:Supplier {key: $supplier}) RETURN properties(s) AS supplier",
    "items": """MATCH (s:Supplier {key: $supplier})-[e:SUPPLIES]->(i:Item)
OPTIONAL MATCH (alt:Supplier)-[:SUPPLIES]->(i) WHERE alt.key <> s.key AND alt.is_active = true
WITH s, e, i, collect(alt.code) AS alternatives
OPTIONAL MATCH (i)-[:HAS_RISK]->(r:StockoutRisk)
RETURN i.pg_id AS id, i.name AS item, i.sku AS sku, i.unit AS unit, i.usable_stock AS usable_stock,
       e.is_preferred AS preferred, e.unit_price AS unit_price, e.item_orders AS item_orders, alternatives,
       r.level AS risk_level, r.probability AS risk_probability, r.days_of_stock_remaining AS days_left
ORDER BY size(alternatives), risk_probability DESC""",
    "procedures": """MATCH (s:Supplier {key: $supplier})-[:SUPPLIES]->(i:Item)<-[u:USES_ITEM]-(p:Procedure)
OPTIONAL MATCH (alt:Supplier)-[:SUPPLIES]->(i) WHERE alt.key <> s.key AND alt.is_active = true
WITH p, i, u, count(alt) AS n_alt
OPTIONAL MATCH (d:Department)-[:PERFORMS]->(p)
WITH p, d, collect({item: i.name, sku: i.sku, sole_source: n_alt = 0, per_procedure: u.quantity_per_procedure}) AS items
RETURN p.pg_id AS id, p.name AS procedure, d.name AS department, p.scheduled_next_14 AS scheduled_next_14,
       items, size([x IN items WHERE x.sole_source]) AS sole_source_items
ORDER BY sole_source_items DESC, scheduled_next_14 DESC""",
    "departments": """MATCH (s:Supplier {key: $supplier})-[:SUPPLIES]->(i:Item)<-[u:USES]-(d:Department)
RETURN d.pg_id AS id, d.name AS department, count(i) AS items, sum(u.units_90) AS units_90,
       collect(i.name) AS item_names
ORDER BY units_90 DESC""",
    "orders": """MATCH (s:Supplier {key: $supplier})-[:HAS_ORDER]->(o:SupplierOrder {is_open: true})-[:FOR_ITEM]->(i:Item)
RETURN o.reference AS reference, i.name AS item, o.outstanding AS outstanding, o.expected_date AS expected_date,
       o.overdue_days AS overdue_days
ORDER BY expected_date""",
    "recommendations": """MATCH (r:ProcurementRecommendation {status: 'PENDING'})-[u:USES_SUPPLIER]->(s:Supplier {key: $supplier})
MATCH (r)-[:FOR_ITEM]->(i:Item)
RETURN r.pg_id AS id, i.name AS item, u.quantity AS quantity, r.purchase_value AS purchase_value""",
}


def impact_supplier(ex: Executor, hid: int, supplier_id: int) -> dict:
    k = key(hid, "Supplier", supplier_id)
    head = ex.run(SUPPLIER_IMPACT_CYPHER["supplier"], {"supplier": k})
    if not head:
        return {"found": False}
    sup = head[0]["supplier"]
    items = ex.run(SUPPLIER_IMPACT_CYPHER["items"], {"supplier": k})
    procs = ex.run(SUPPLIER_IMPACT_CYPHER["procedures"], {"supplier": k})
    depts = ex.run(SUPPLIER_IMPACT_CYPHER["departments"], {"supplier": k})
    orders = ex.run(SUPPLIER_IMPACT_CYPHER["orders"], {"supplier": k})
    recs = ex.run(SUPPLIER_IMPACT_CYPHER["recommendations"], {"supplier": k})
    for i in items:
        i["sole_source"] = not i["alternatives"]
        i["severity"] = ("critical" if i["sole_source"] and i.get("risk_level") in RISKY else
                         "high" if i["sole_source"] else "watch" if i.get("risk_level") in RISKY else "low")
    sole = [i for i in items if i["sole_source"]]
    risky = [i for i in items if i.get("risk_level") in RISKY]
    exposed_procs = [p for p in procs if p["sole_source_items"] > 0]
    sched = sum(p["scheduled_next_14"] or 0 for p in exposed_procs)
    summary = [f"If {sup['name']} becomes unavailable: {len(items)} item(s) lose a supplier — {len(sole)} have no active "
               f"alternative supplier; {len(risky)} are already at medium/high stockout risk."]
    if exposed_procs:
        summary.append(f"{len(exposed_procs)} procedure type(s) use a sole-source item from this supplier "
                       f"({sched} scheduled in the next 14 days).")
    if orders:
        summary.append(f"{len(orders)} open order(s) from this supplier ({_n(sum(o['outstanding'] or 0 for o in orders))} units "
                       f"outstanding) would not arrive.")
    if recs:
        summary.append(f"{len(recs)} pending procurement recommendation(s) rely on this supplier — re-check them "
                       f"(Procurement → What-if).")
    chains = []
    for i in sorted(items, key=lambda x: (not x["sole_source"], -(x.get("risk_probability") or 0)))[:5]:
        p = next((p for p in procs if any(x["sku"] == i["sku"] for x in p["items"])), None)
        chains.append([x for x in [
            {"label": "Supplier", "name": sup["name"], "detail": "unavailable"},
            {"label": "Item", "name": i["item"],
             "detail": "sole source" if i["sole_source"] else f"alternatives: {', '.join(i['alternatives'])}"},
            {"label": "Procedure", "name": p["procedure"], "detail": f"{p['scheduled_next_14'] or 0} scheduled / 14 d"} if p else None,
            {"label": "Department", "name": p["department"], "detail": ""} if p and p.get("department") else None,
            {"label": "StockoutRisk", "name": f"{i['risk_level'] or '—'} risk", "detail": _pct(i.get("risk_probability"))},
        ] if x])
    return {"found": True, "supplier": {"id": supplier_id, "name": sup["name"], "code": sup.get("code"),
                                        "reliability_score": sup.get("reliability_score"), "grade": sup.get("grade")},
            "summary": summary, "items": items, "procedures": procs, "departments": depts, "open_orders": orders,
            "recommendations": recs, "chains": chains, "cypher": SUPPLIER_IMPACT_CYPHER}


ITEM_IMPACT_CYPHER = {
    "item": "MATCH (i:Item {key: $item}) OPTIONAL MATCH (i)-[:HAS_RISK]->(r) RETURN properties(i) AS item, properties(r) AS risk",
    "procedures": """MATCH (p:Procedure)-[u:USES_ITEM]->(i:Item {key: $item})
OPTIONAL MATCH (d:Department)-[:PERFORMS]->(p)
OPTIONAL MATCH (p)-[:USES_ITEM]->(other:Item)
WITH p, u, d, i, count(other) AS kit_items
RETURN p.pg_id AS id, p.name AS procedure, d.name AS department, p.scheduled_next_14 AS scheduled_next_14,
       p.completed_last_90 AS completed_last_90, u.quantity_per_procedure AS per_procedure,
       u.units_next_14 AS units_next_14, kit_items
ORDER BY units_next_14 DESC, per_procedure DESC""",
    "departments": """MATCH (d:Department {hospital_id: $hid})
OPTIONAL MATCH (d)-[u:USES]->(i:Item {key: $item})
OPTIONAL MATCH (d)-[:PERFORMS]->(p:Procedure)-[k:USES_ITEM]->(:Item {key: $item})
WITH d, u, collect(p.name) AS procedures, sum(k.units_next_14) AS procedure_units_14
WHERE u IS NOT NULL OR size(procedures) > 0
RETURN d.pg_id AS id, d.name AS department, u.units_90 AS units_90, u.share AS share, procedures, procedure_units_14
ORDER BY units_90 DESC""",
    "suppliers": """MATCH (s:Supplier)-[e:SUPPLIES]->(i:Item {key: $item})
RETURN s.name AS supplier, s.code AS code, s.is_active AS active, e.unit_price AS unit_price,
       e.window_k AS window_k, e.window_n AS window_n
ORDER BY window_k DESC""",
}


def impact_item(ex: Executor, hid: int, item_id: int) -> dict:
    k = key(hid, "Item", item_id)
    head = ex.run(ITEM_IMPACT_CYPHER["item"], {"item": k})
    if not head:
        return {"found": False}
    item, risk = head[0]["item"], head[0]["risk"]
    procs = ex.run(ITEM_IMPACT_CYPHER["procedures"], {"item": k})
    depts = ex.run(ITEM_IMPACT_CYPHER["departments"], {"item": k, "hid": hid})
    sups = ex.run(ITEM_IMPACT_CYPHER["suppliers"], {"item": k})
    unit = item.get("unit") or "units"
    usable = item.get("usable_stock") or 0
    need = sum(p["units_next_14"] or 0 for p in procs)
    for p in procs:
        per = p["per_procedure"] or 0
        p["procedures_covered_by_stock"] = int(usable // per) if per else None
    summary = [f"{item['name']}: {len(procs)} procedure type(s) use it; the next 14 days' schedule needs {_n(need)} {unit} "
               f"by the kit mappings against {_n(usable)} usable."]
    if need > usable and need:
        summary.append(f"A shortage now would leave {_n(need - usable)} {unit} of scheduled procedure demand uncovered "
                       f"({(need - usable) / need:.0%}) — before any non-procedure use.")
    heavy = [p for p in procs if (p["per_procedure"] or 0) >= 2]
    if heavy:
        top = ", ".join(f"{p['procedure']} ({_n(p['per_procedure'], 1)} {unit})" for p in heavy[:3])
        summary.append(f"Heaviest per-procedure use: {top}.")
    act = [s for s in sups if s["active"]]
    summary.append("Single source: " + act[0]["supplier"] if len(act) == 1 else f"{len(act)} active suppliers list it.")
    return {"found": True, "item": {"id": item_id, "name": item["name"], "sku": item.get("sku"), "unit": unit,
                                    "usable_stock": usable, "risk_level": (risk or {}).get("level")},
            "summary": summary, "procedures": procs, "departments": depts, "suppliers": sups,
            "units_needed_14": need, "cypher": ITEM_IMPACT_CYPHER}


# ---------------------------------------------------------------- V6.5 predefined graph queries

CATALOG: list[dict[str, Any]] = [
    {"name": "suppliers_for_item", "title": "Which suppliers supply this item?", "params": ["item"],
     "cypher": """MATCH (s:Supplier)-[e:SUPPLIES]->(i:Item {key: $item})
RETURN s.name AS supplier, s.code AS code, s.is_active AS active, e.unit_price AS unit_price, e.moq AS moq,
       e.quoted_lead_time_days AS quoted_lead_days, e.lead_time_median AS typical_lead_days, s.grade AS grade,
       e.window_k AS in_window, e.window_n AS of_orders
ORDER BY unit_price"""},
    {"name": "procedures_using_item", "title": "Which procedures use this item (and how heavily)?", "params": ["item"],
     "cypher": """MATCH (p:Procedure)-[u:USES_ITEM]->(i:Item {key: $item})
OPTIONAL MATCH (d:Department)-[:PERFORMS]->(p)
RETURN p.name AS procedure, d.name AS department, u.quantity_per_procedure AS per_procedure,
       p.scheduled_next_14 AS scheduled_next_14, u.units_next_14 AS units_next_14
ORDER BY per_procedure DESC, units_next_14 DESC"""},
    {"name": "departments_for_item", "title": "Which departments depend on this item?", "params": ["item"],
     "cypher": ITEM_IMPACT_CYPHER["departments"]},
    {"name": "items_for_supplier", "title": "Which items are affected by this supplier?", "params": ["supplier"],
     "cypher": SUPPLIER_IMPACT_CYPHER["items"]},
    {"name": "risky_single_source", "title": "Which high/medium-risk items have only one supplier?", "params": [],
     "cypher": """MATCH (i:Item {hospital_id: $hid})-[:HAS_RISK]->(r:StockoutRisk)
WHERE r.level IN ['HIGH', 'MEDIUM']
OPTIONAL MATCH (s:Supplier)-[:SUPPLIES]->(i) WHERE s.is_active = true
WITH i, r, collect(s.name) AS suppliers
WHERE size(suppliers) <= 1
RETURN i.name AS item, i.sku AS sku, r.level AS risk, r.probability AS probability,
       r.days_of_stock_remaining AS days_left, suppliers
ORDER BY probability DESC"""},
    {"name": "procedures_at_risk", "title": "Which scheduled procedures could be affected by an item shortage?",
     "params": [],
     "cypher": """MATCH (p:Procedure {hospital_id: $hid})-[u:USES_ITEM]->(i:Item)-[:HAS_RISK]->(r:StockoutRisk)
WHERE r.level IN ['HIGH', 'MEDIUM'] AND p.scheduled_next_14 > 0
OPTIONAL MATCH (d:Department)-[:PERFORMS]->(p)
WITH p, d, collect(i.name + ' (' + r.level + ')') AS items_at_risk, sum(u.units_next_14) AS units_needed
RETURN p.name AS procedure, d.name AS department, p.scheduled_next_14 AS scheduled_next_14, items_at_risk, units_needed
ORDER BY size(items_at_risk) DESC, scheduled_next_14 DESC"""},
    {"name": "single_source_items", "title": "Which items have only one active supplier?", "params": [],
     "cypher": """MATCH (i:Item {hospital_id: $hid})
OPTIONAL MATCH (s:Supplier)-[:SUPPLIES]->(i) WHERE s.is_active = true
WITH i, collect(s.name) AS suppliers
WHERE size(suppliers) <= 1
OPTIONAL MATCH (i)-[:HAS_RISK]->(r:StockoutRisk)
RETURN i.name AS item, i.sku AS sku, suppliers, r.level AS risk
ORDER BY item"""},
    {"name": "departments_exposed", "title": "Which departments use the most high-risk items?", "params": [],
     "cypher": """MATCH (d:Department {hospital_id: $hid})-[u:USES]->(i:Item)-[:HAS_RISK]->(r:StockoutRisk)
WHERE r.level IN ['HIGH', 'MEDIUM']
RETURN d.name AS department, count(i) AS items_at_risk, sum(u.units_90) AS units_90, collect(i.name) AS items
ORDER BY items_at_risk DESC, units_90 DESC"""},
    {"name": "overdue_orders_on_risky_items", "title": "Which at-risk items are waiting on an overdue order?",
     "params": [],
     "cypher": """MATCH (s:Supplier {hospital_id: $hid})-[:HAS_ORDER]->(o:SupplierOrder)-[:FOR_ITEM]->(i:Item)-[:HAS_RISK]->(r:StockoutRisk)
WHERE o.is_open = true AND o.overdue_days > 0 AND r.level IN ['HIGH', 'MEDIUM']
RETURN i.name AS item, r.level AS risk, o.reference AS reference, s.name AS supplier, o.overdue_days AS overdue_days,
       o.outstanding AS outstanding
ORDER BY overdue_days DESC"""},
]
CATALOG_BY_NAME = {q["name"]: q for q in CATALOG}


def run_catalog(ex: Executor, hid: int, name: str, item_id: int | None = None, supplier_id: int | None = None) -> dict:
    q = CATALOG_BY_NAME[name]
    params: dict[str, Any] = {"hid": hid}
    if "item" in q["params"]:
        params["item"] = key(hid, "Item", item_id)
    if "supplier" in q["params"]:
        params["supplier"] = key(hid, "Supplier", supplier_id)
    rows = ex.run(q["cypher"], params)
    return {"name": name, "title": q["title"], "cypher": q["cypher"], "params": {k: v for k, v in params.items()},
            "columns": list(rows[0].keys()) if rows else [], "rows": rows}


def schema_counts(ex: Executor, hid: int) -> dict:
    from app.graph.projection import LABELS, RELATIONSHIPS
    from app.graph.sync import graph_counts

    nodes, edges = graph_counts(ex, hid)
    return {"labels": [{"label": lb, "count": nodes.get(lb, 0)} for lb in LABELS],
            "relationships": [{"type": t, "from": a, "to": b, "meaning": m, "count": edges.get(f"{a}-{t}->{b}", 0)}
                              for t, a, b, m in RELATIONSHIPS]}
