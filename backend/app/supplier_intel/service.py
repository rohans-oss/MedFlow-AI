"""V4 — supplier intelligence views built from the supplier order log (+ V3 stockout risk for deadlines)."""

from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.models import (
    ACTIVE_ORDER_STATUSES,
    Consumable,
    RiskLevel,
    StockoutPrediction,
    Supplier,
    SupplierOrder,
    SupplierProduct,
)
from app.supplier_intel.metrics import MIN_ORDERS, OrderRec, lead_time_histogram, monthly, p_within, summarize
from app.supplier_intel.predict import late_rates, predict_lead_time

WINDOW_DAYS = 365
LIKELY, POSSIBLE = 0.8, 0.5  # P(delivery in time) thresholds for the verdict wording


def _rec(o: SupplierOrder) -> OrderRec:
    return OrderRec(o.id, o.supplier_id, o.consumable_id, o.ordered_date, o.expected_date, o.quoted_lead_time_days,
                    o.quantity_ordered, o.quantity_received, o.status, o.first_delivery_date, o.completed_date,
                    float(o.unit_price))


def load_orders(db: Session, hospital_id: int, since: date | None = None, **filters) -> list[SupplierOrder]:
    q = select(SupplierOrder).where(SupplierOrder.hospital_id == hospital_id)
    if since is not None:
        q = q.where(SupplierOrder.ordered_date >= since)
    for k, v in filters.items():
        if v is not None:
            q = q.where(getattr(SupplierOrder, k) == v)
    return list(db.scalars(q.order_by(SupplierOrder.ordered_date, SupplierOrder.id)))


def _latest_risk(db: Session, hospital_id: int, ids: list[int] | None = None) -> dict[int, StockoutPrediction]:
    from app.risk import engine  # V3 (read-only)

    return engine.latest(db, hospital_id, ids)


# ---------------------------------------------------------------- scorecards


def scorecards(db: Session, hospital_id: int, window_days: int = WINDOW_DAYS) -> dict:
    today = business_today()
    since = today - timedelta(days=window_days)
    recs = [_rec(o) for o in load_orders(db, hospital_id, since)]
    hosp = summarize(recs, today)
    prior = hosp["otif_rate"] if hosp["otif_rate"] is not None else 0.8
    suppliers = db.scalars(select(Supplier).where(Supplier.hospital_id == hospital_id).order_by(Supplier.name)).all()
    rows = []
    for s in suppliers:
        mine = [r for r in recs if r.supplier_id == s.id]
        summ = summarize(mine, today, prior) if mine else None
        n_items = db.scalar(select(SupplierProduct.id).where(SupplierProduct.supplier_id == s.id).limit(1))
        rows.append({"supplier_id": s.id, "code": s.code, "name": s.name, "city": s.city, "is_active": s.is_active,
                     "default_lead_time_days": s.default_lead_time_days, "has_catalogue": n_items is not None,
                     "metrics": summ})
    rows.sort(key=lambda r: -(r["metrics"]["reliability_score"] if r["metrics"] and r["metrics"]["reliability_score"]
                              is not None else -1))
    return {"window_days": window_days, "window_start": since, "window_end": today, "hospital": hosp,
            "prior": prior, "suppliers": rows}


def supplier_detail(db: Session, hospital_id: int, supplier: Supplier, window_days: int = WINDOW_DAYS) -> dict:
    today = business_today()
    since = today - timedelta(days=window_days)
    all_recs = [_rec(o) for o in load_orders(db, hospital_id, since)]
    hosp = summarize(all_recs, today)
    prior = hosp["otif_rate"] if hosp["otif_rate"] is not None else 0.8
    orders = load_orders(db, hospital_id, since, supplier_id=supplier.id)
    recs = [_rec(o) for o in orders]
    summ = summarize(recs, today, prior)
    items = {c.id: c for c in db.scalars(select(Consumable).where(Consumable.hospital_id == hospital_id))}
    catalogue = {sp.consumable_id: sp for sp in db.scalars(select(SupplierProduct)
                                                           .where(SupplierProduct.supplier_id == supplier.id))}
    by_item: dict[int, list[OrderRec]] = {}
    for r in recs:
        by_item.setdefault(r.consumable_id, []).append(r)
    s_rate = summ["otif_rate"] if summ["otif_rate"] is not None else prior
    per_item = []
    for cid in sorted(set(by_item) | set(catalogue), key=lambda i: items[i].name if i in items else ""):
        if cid not in items:
            continue
        ir = by_item.get(cid, [])
        sp = catalogue.get(cid)
        m = summarize(ir, today, s_rate) if ir else None
        per_item.append({"consumable_id": cid, "name": items[cid].name, "sku": items[cid].sku, "unit": items[cid].unit,
                         "catalogue_price": float(sp.unit_price) if sp else None,
                         "quoted_lead_time_days": sp.lead_time_days if sp else None, "moq": sp.moq if sp else None,
                         "is_preferred": bool(sp.is_preferred) if sp else False, "metrics": m,
                         "price_history": [{"date": o.ordered.isoformat(), "price": o.unit_price}
                                           for o in sorted(ir, key=lambda o: o.ordered)]})
    return {
        "supplier": {"id": supplier.id, "code": supplier.code, "name": supplier.name, "city": supplier.city,
                     "default_lead_time_days": supplier.default_lead_time_days},
        "window_days": window_days, "window_start": since, "window_end": today, "prior": prior,
        "metrics": summ, "monthly": monthly(recs, today), "lead_time_histogram": lead_time_histogram(recs),
        "items": per_item,
        "recent_orders": [order_row(o, today) for o in sorted(orders, key=lambda o: (o.ordered_date, o.id))[-25:][::-1]],
    }


def order_row(o: SupplierOrder, today: date) -> dict:
    lt = None if o.first_delivery_date is None else (o.first_delivery_date - o.ordered_date).days
    return {
        "id": o.id, "reference": o.reference, "supplier_id": o.supplier_id, "supplier": o.supplier.name,
        "consumable_id": o.consumable_id, "item": o.consumable.name, "sku": o.consumable.sku, "unit": o.consumable.unit,
        "ordered_date": o.ordered_date, "expected_date": o.expected_date, "quoted_lead_time_days": o.quoted_lead_time_days,
        "quantity_ordered": o.quantity_ordered, "quantity_received": o.quantity_received, "unit_price": float(o.unit_price),
        "status": o.status, "first_delivery_date": o.first_delivery_date, "completed_date": o.completed_date,
        "lead_time_days": lt, "days_late": None if o.first_delivery_date is None
        else max((o.first_delivery_date - o.expected_date).days, 0),
        "overdue_days": (today - o.expected_date).days if o.status in ACTIVE_ORDER_STATUSES and today > o.expected_date
        else 0,
        "close_reason": o.close_reason, "is_synthetic": o.is_synthetic, "recommendation_id": o.recommendation_id,
    }


# ---------------------------------------------------------------- item comparison + V3 link


def _verdict(p: float | None, deadline: int | None) -> str:
    if deadline is None:
        return "no_deadline"
    if p is None:
        return "no_evidence"
    return "likely" if p >= LIKELY else "uncertain" if p >= POSSIBLE else "unlikely"


def item_options(db: Session, hospital_id: int, item: Consumable, window_days: int = WINDOW_DAYS,
                 all_recs: list[OrderRec] | None = None, risk: StockoutPrediction | None = None) -> dict:
    """Every supplier that lists the item: price, MOQ, quoted vs actual lead time, reliability for THIS item, and
    whether it can deliver before the V3 projected stockout based on its own delivery history."""
    today = business_today()
    since = today - timedelta(days=window_days)
    if all_recs is None:
        all_recs = [_rec(o) for o in load_orders(db, hospital_id, since)]
    if risk is None:
        risk = _latest_risk(db, hospital_id, [item.id]).get(item.id)
    hosp = summarize(all_recs, today)
    prior = hosp["otif_rate"] if hosp["otif_rate"] is not None else 0.8
    offers = db.execute(select(SupplierProduct, Supplier).join(Supplier, Supplier.id == SupplierProduct.supplier_id)
                        .where(SupplierProduct.consumable_id == item.id)).all()

    out_now = risk is not None and risk.usable_stock <= 0
    deadline = None
    if risk is not None and risk.expected_stockout_date is not None and risk.as_of == today and not out_now:
        deadline = (risk.expected_stockout_date - today).days  # a delivery must arrive by this many days from now
    rows = []
    for sp, s in offers:
        sup_recs = [r for r in all_recs if r.supplier_id == s.id]
        item_recs = [r for r in sup_recs if r.consumable_id == item.id]
        s_sum = summarize(sup_recs, today, prior) if sup_recs else None
        s_rate = s_sum["otif_rate"] if s_sum and s_sum["otif_rate"] is not None else prior
        i_sum = summarize(item_recs, today, s_rate) if item_recs else None
        use_item = i_sum is not None and i_sum["decided"] >= MIN_ORDERS
        basis_recs, basis = (item_recs, "item") if use_item else (sup_recs, "supplier") if sup_recs else ([], "none")
        lt = predict_lead_time(basis_recs, s.id, item.id if use_item else -1, sp.lead_time_days)
        ev = p_within(basis_recs, deadline, today) if deadline is not None and basis_recs else None
        chosen = i_sum if use_item else s_sum
        rows.append({
            "supplier_id": s.id, "supplier": s.name, "code": s.code, "is_active": s.is_active,
            "is_preferred": bool(sp.is_preferred), "unit_price": float(sp.unit_price), "moq": sp.moq,
            "quoted_lead_time_days": sp.lead_time_days,
            "typical_lead_time_days": round(lt.median, 1), "p90_lead_time_days": round(lt.p90, 1),
            "lead_time_basis": lt.basis,
            "evidence_basis": basis, "evidence_orders": (chosen or {}).get("decided", 0),
            "item_orders": len(item_recs),
            "on_time_rate": (chosen or {}).get("on_time_rate"), "otif_rate": (chosen or {}).get("otif_rate"),
            "reliability_score": (chosen or {}).get("reliability_score"), "grade": (chosen or {}).get("grade"),
            "cancellation_rate": (chosen or {}).get("cancellation_rate"), "fill_rate": (chosen or {}).get("fill_rate"),
            "price_stability": (chosen or {}).get("price_stability"),
            "in_time_k": ev["k"] if ev else None, "in_time_n": ev["n"] if ev else None,
            "p_in_time": round(ev["p"], 3) if ev and ev["p"] is not None else None,
            "verdict": _verdict(ev["p"] if ev else None, deadline),
            "arrives_typically": today + timedelta(days=int(round(lt.median))),
            "arrives_worst_case": today + timedelta(days=int(np.ceil(lt.p90))),
        })
    cheapest = min((r["unit_price"] for r in rows if r["is_active"]), default=None)
    for r in rows:
        r["price_vs_cheapest"] = None if not cheapest else round(r["unit_price"] / cheapest - 1, 4)
    rows.sort(key=lambda r: (-(r["p_in_time"] if r["p_in_time"] is not None else -1),
                             -(r["reliability_score"] or 0), r["unit_price"]))

    open_orders = [o for o in load_orders(db, hospital_id, None, consumable_id=item.id)
                   if o.status in ACTIVE_ORDER_STATUSES]
    in_transit = [open_order_view(o, all_recs, today, deadline) for o in open_orders]
    return {
        "item": {"id": item.id, "name": item.name, "sku": item.sku, "unit": item.unit},
        "risk": None if risk is None else {
            "risk_level": risk.risk_level, "probability": risk.probability, "usable_stock": risk.usable_stock,
            "expected_stockout_date": risk.expected_stockout_date, "days_of_stock_remaining": risk.days_of_stock_remaining,
            "shortage_quantity": risk.shortage_quantity, "forecast_14": risk.forecast_14, "as_of": risk.as_of,
            "out_of_stock": out_now},
        "deadline_days": deadline,
        "options": rows,
        "open_orders": in_transit,
        "summary": _summary(item, rows, deadline, out_now, in_transit, risk),
    }


def open_order_view(o: SupplierOrder, all_recs: list[OrderRec], today: date, deadline: int | None) -> dict:
    """In-transit order: predicted arrival from the supplier's history, conditional on not having arrived yet."""
    elapsed = (today - o.ordered_date).days
    item_recs = [r for r in all_recs if r.supplier_id == o.supplier_id and r.consumable_id == o.consumable_id]
    sup_recs = [r for r in all_recs if r.supplier_id == o.supplier_id]
    basis = item_recs if sum(r.lead_time is not None for r in item_recs) >= MIN_ORDERS else sup_recs
    later = [r.lead_time for r in basis if r.lead_time is not None and r.lead_time > elapsed]
    exp_lt = float(np.median(later)) if later else float(max(elapsed + 1, o.quoted_lead_time_days))
    lr = late_rates(basis, o.supplier_id, o.consumable_id) if basis else None
    ev = p_within(basis, elapsed + deadline, today, elapsed=elapsed) if deadline is not None and basis else None
    row = order_row(o, today)
    row.update({
        "days_in_transit": elapsed,
        "predicted_arrival": o.ordered_date + timedelta(days=int(round(exp_lt))),
        "p_late": None if today > o.expected_date else (round(lr["item"], 3) if lr else None),
        "p_before_stockout": round(ev["p"], 3) if ev and ev["p"] is not None else None,
        "before_stockout_k": ev["k"] if ev else None, "before_stockout_n": ev["n"] if ev else None,
    })
    return row


def _money(v: float) -> str:
    return f"₹{v:,.2f}"


def _summary(item: Consumable, rows: list[dict], deadline: int | None, out_now: bool, transit: list[dict],
             risk: StockoutPrediction | None) -> list[str]:
    """Plain-English findings from the numbers above. Never an instruction to buy."""
    out: list[str] = []
    active = [r for r in rows if r["is_active"]]
    if not active:
        return ["No active supplier lists this item."]
    if out_now:
        fastest = min(active, key=lambda r: (r["typical_lead_time_days"], -(r["reliability_score"] or 0)))
        out.append(f"{item.name} is already out of stock. Typically fastest: {fastest['supplier']} "
                   f"(~{fastest['typical_lead_time_days']:.0f} days, 90% of deliveries within {fastest['p90_lead_time_days']:.0f}).")
    elif deadline is not None:
        out.append(f"Projected stockout in {deadline} day{'s' if deadline != 1 else ''} "
                   f"({risk.expected_stockout_date.isoformat()}): a delivery ordered today must arrive within {deadline} days.")
        likely = [r for r in active if r["verdict"] == "likely"]
        for r in sorted(active, key=lambda r: -(r["p_in_time"] or 0))[:3]:
            if not r["in_time_n"]:
                out.append(f"{r['supplier']} ({_money(r['unit_price'])}): no order history to judge a {deadline}-day delivery.")
                continue
            out.append(f"{r['supplier']} ({_money(r['unit_price'])}): delivered within {deadline} days in "
                       f"{r['in_time_k']} of {r['in_time_n']} past orders"
                       f"{' for this item' if r['evidence_basis'] == 'item' else ' (all items)'} — {r['verdict']}.")
        cheapest = min(active, key=lambda r: r["unit_price"])
        if likely and cheapest not in likely:
            best = likely[0]
            out.append(f"The cheapest supplier ({cheapest['supplier']}) is unlikely to deliver in time based on its "
                       f"history; {best['supplier']} costs {best['price_vs_cheapest']:+.0%} more but has met this window "
                       f"{best['in_time_k']} of {best['in_time_n']} times.")
        if likely:
            names = ", ".join(r["supplier"] for r in likely)
            out.append(f"Based on historical delivery performance, {names} can meet the required delivery window.")
        else:
            out.append(f"No supplier has reliably delivered within {deadline} days in the past — the projected "
                       f"stockout may not be avoidable by a new order alone.")
    else:
        best = max(active, key=lambda r: r["reliability_score"] or 0)
        out.append(f"No stockout projected within 30 days. Most reliable for this item: {best['supplier']} "
                   f"(reliability {best['reliability_score'] or 0:.0f}/100).")
    for t in transit:
        if t["overdue_days"] > 0:
            out.append(f"Order {t['reference']} from {t['supplier']} ({t['quantity_ordered'] - t['quantity_received']:,} "
                       f"{item.unit} outstanding) is {t['overdue_days']} days overdue.")
        if t["p_before_stockout"] is None and deadline is not None and t["overdue_days"] > 0:
            out[-1] += " No past order from this supplier took this long, so its arrival can't be estimated from history."
        elif t["p_before_stockout"] is not None:
            out.append(f"Order {t['reference']} from {t['supplier']} is in transit; based on history it arrives before "
                       f"the projected stockout with probability {t['p_before_stockout']:.0%}.")
    out.append("Information for procurement review — no order has been placed. Recommended quantities and suppliers "
               "(with costs and scenarios) are on the Procurement page and need a person's approval.")
    return out


def at_risk(db: Session, hospital_id: int, window_days: int = WINDOW_DAYS) -> list[dict]:
    """V3 medium/high-risk items with each item's supplier options summarised."""
    today = business_today()
    since = today - timedelta(days=window_days)
    all_recs = [_rec(o) for o in load_orders(db, hospital_id, since)]
    risks = {cid: p for cid, p in _latest_risk(db, hospital_id).items()
             if p.risk_level in (RiskLevel.HIGH, RiskLevel.MEDIUM)}
    items = {c.id: c for c in db.scalars(select(Consumable).where(Consumable.id.in_(list(risks)),
                                                                  Consumable.is_active.is_(True)))} if risks else {}
    out = []
    for cid, p in risks.items():
        if cid not in items:
            continue
        v = item_options(db, hospital_id, items[cid], window_days, all_recs, p)
        likely = [o for o in v["options"] if o["verdict"] == "likely" and o["is_active"]]
        best = v["options"][0] if v["options"] else None
        out.append({"item": v["item"], "risk": v["risk"], "deadline_days": v["deadline_days"],
                    "n_suppliers": len(v["options"]), "n_likely": len(likely),
                    "best": best, "open_orders": len(v["open_orders"]),
                    "overdue_orders": sum(o["overdue_days"] > 0 for o in v["open_orders"]),
                    "headline": v["summary"][1] if len(v["summary"]) > 1 else v["summary"][0]})
    order = {RiskLevel.HIGH: 0, RiskLevel.MEDIUM: 1}
    out.sort(key=lambda r: (order.get(r["risk"]["risk_level"], 2), r["deadline_days"] if r["deadline_days"] is not None
                            else -1, -r["risk"]["probability"]))
    return out


def open_orders(db: Session, hospital_id: int, window_days: int = WINDOW_DAYS) -> list[dict]:
    today = business_today()
    all_recs = [_rec(o) for o in load_orders(db, hospital_id, today - timedelta(days=window_days))]
    risks = _latest_risk(db, hospital_id)
    rows = []
    for o in load_orders(db, hospital_id):
        if o.status not in ACTIVE_ORDER_STATUSES:
            continue
        r = risks.get(o.consumable_id)
        deadline = None
        if r is not None and r.expected_stockout_date is not None and r.as_of == today and r.usable_stock > 0:
            deadline = (r.expected_stockout_date - today).days
        v = open_order_view(o, all_recs, today, deadline)
        v["item_risk_level"] = r.risk_level if r else None
        v["item_stockout_date"] = r.expected_stockout_date if r else None
        rows.append(v)
    rows.sort(key=lambda v: (-v["overdue_days"], v["expected_date"]))
    return rows
