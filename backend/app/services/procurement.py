"""V5.4 — recommendations and human approval.

    risk detected → scenarios → optimiser → recommendation (PENDING) → person reviews → approve / modify / reject
                                                                      → approved: supplier order(s) RECORDED in the V4 log

Recording an order only writes MedFlow's own order log (the same as "Record order" in V4). Nothing is sent to a
supplier or to a purchasing system, and nothing is ever approved automatically.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.db.base import utcnow
from app.models import (
    Consumable,
    ProcurementRecommendation,
    ProcurementSettings,
    RecommendationStatus,
    Supplier,
    SupplierOrder,
    SupplierProduct,
    User,
)
from app.procurement import engine
from app.procurement.costs import DEFAULTS, EXPLAIN, CostModel
from app.services import supplier_orders

FORMULA = [
    "Total expected cost = purchase + expected stockout + expected holding + expected expiry/waste − stock carried forward",
    "purchase = Σ quantity × unit price + fixed cost per order line",
    "expected stockout = expected units short over the horizon × item reference price × stockout multiplier",
    "expected holding = expected extra unit-days in stock (within the horizon and until used) × reference price × "
    "annual holding rate ÷ 365",
    "expected expiry/waste = expected extra units expiring before use × unit price × (1 + disposal %)",
    "stock carried forward = ordered units not used within the horizon × unit price (they serve later demand)",
    "Horizon = the reference supplier's expected lead time + review period (until an order at the next review "
    "could arrive). 'Expected' = average over Monte Carlo paths of demand (forecast ± its measured error) and "
    "delivery days (each supplier's delivery history).",
]


def _bad(msg: str, code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    return HTTPException(code, msg)


# ---------------------------------------------------------------- settings


def settings_out(db: Session, hospital_id: int) -> dict:
    s = engine.load_settings(db, hospital_id)
    cm = CostModel.from_settings(s)
    by = db.get(User, s.updated_by_id) if s is not None and s.updated_by_id else None
    return {"values": cm.as_dict(), "defaults": DEFAULTS, "explain": EXPLAIN, "formula": FORMULA,
            "is_default": s is None, "updated_at": s.updated_at if s else None, "updated_by": by.full_name if by else None}


def save_settings(db: Session, hospital_id: int, user: User, values: dict) -> tuple[dict, dict]:
    s = engine.load_settings(db, hospital_id)
    before = CostModel.from_settings(s).as_dict()
    if s is None:
        s = ProcurementSettings(hospital_id=hospital_id)
        db.add(s)
    for k, v in values.items():
        setattr(s, k, Decimal(str(v)) if k in ("order_cost", "budget_limit") and v is not None else v)
    s.updated_by_id = user.id
    s.updated_at = utcnow()
    db.flush()
    changed = {k: {"from": before[k], "to": values[k]} for k in values if before[k] != values[k]}
    return before, changed


# ---------------------------------------------------------------- views


def _name(db: Session, uid: int | None) -> str | None:
    u = db.get(User, uid) if uid else None
    return u.full_name if u else None


def _orders(db: Session, rec: ProcurementRecommendation) -> list[dict]:
    rows = db.scalars(select(SupplierOrder).where(SupplierOrder.recommendation_id == rec.id).order_by(SupplierOrder.id))
    return [{"id": o.id, "reference": o.reference, "supplier": o.supplier.name, "quantity": o.quantity_ordered,
             "status": o.status, "expected_date": o.expected_date} for o in rows]


def row(db: Session, rec: ProcurementRecommendation) -> dict:
    none = next((s for s in rec.scenarios if s["kind"] == "none"), None)
    c = rec.consumable
    return {
        "id": rec.id, "run_id": rec.run_id,
        "item": {"id": c.id, "name": c.name, "sku": c.sku, "unit": c.unit},
        "status": rec.status, "created_at": rec.created_at, "as_of": rec.as_of, "is_current": rec.as_of == business_today(),
        "risk_level": rec.risk_level, "need_by_date": rec.need_by_date, "order_by_date": rec.order_by_date,
        "lines": rec.lines, "quantity": rec.quantity, "purchase_value": float(rec.purchase_value),
        "expected_cost": float(rec.expected_cost),
        "p_stockout": rec.metrics.get("p_stockout"), "expected_shortage": rec.metrics.get("expected_shortage"),
        "no_order_p_stockout": none["metrics"]["p_stockout"] if none else None,
        "headline": rec.explanation[0] if rec.explanation else "",
        "generated_by": _name(db, rec.generated_by_id), "decided_by": _name(db, rec.decided_by_id),
        "decided_at": rec.decided_at, "decision_reason": rec.decision_reason, "modified": rec.modified,
        "final_lines": rec.final_lines, "orders": _orders(db, rec),
    }


def detail(db: Session, rec: ProcurementRecommendation) -> dict:
    return {**row(db, rec), "cost_breakdown": rec.cost_breakdown, "metrics": rec.metrics, "scenarios": rec.scenarios,
            "replenishment": rec.replenishment, "in_transit": rec.in_transit, "explanation": rec.explanation,
            "settings_snapshot": rec.settings_snapshot, "solver": rec.solver, "scenario_key": rec.scenario_key,
            "final_evaluation": rec.final_evaluation}


def pending_by_item(db: Session, hospital_id: int) -> dict[int, ProcurementRecommendation]:
    rows = db.scalars(select(ProcurementRecommendation).where(
        ProcurementRecommendation.hospital_id == hospital_id,
        ProcurementRecommendation.status == RecommendationStatus.PENDING).order_by(ProcurementRecommendation.id))
    return {r.consumable_id: r for r in rows}


# ---------------------------------------------------------------- generate


def generate(db: Session, hospital_id: int, user: User | None, item_ids: list[int] | None = None) -> dict:
    """Evaluate scenarios for the given items (default: every item needing attention), run CP-SAT across them and
    store one PENDING recommendation per item. Older pending recommendations for those items are superseded."""
    cm = engine.cost_model(db, hospital_id)
    skipped: list[dict] = []
    if item_ids is None:
        att = engine.needs_attention(db, hospital_id)
        item_ids = [r["item"]["id"] for r in att["items"]]
        skipped = att["skipped"]
    plans, sol = engine.plan_items(db, hospital_id, item_ids, cm)
    planned = {p["item"]["id"] for p in plans}
    skipped += [{"item_id": i, "reason": "no forecast or no supplier for this item"} for i in item_ids if i not in planned]
    run_id = uuid.uuid4().hex[:12]
    superseded = 0
    pending = pending_by_item(db, hospital_id)
    created = []
    for p in plans:
        old = pending.get(p["item"]["id"])
        if old is not None:
            old.status = RecommendationStatus.SUPERSEDED
            superseded += 1
        rec_s = next(s for s in p["scenarios"] if s["key"] == p["recommended_key"])
        lines = [{"supplier_id": ln["supplier_id"], "supplier": ln["supplier"], "code": ln["code"],
                  "quantity": ln["quantity"], "unit_price": ln["unit_price"], "moq": ln["moq"],
                  "typical_arrival_date": ln["typical_arrival_date"], "p_arrive_by_need": ln["p_arrive_by_need"],
                  "window_k": ln["window_k"], "window_n": ln["window_n"]} for ln in rec_s["lines"]]
        rec = ProcurementRecommendation(
            hospital_id=hospital_id, consumable_id=p["item"]["id"], created_at=utcnow(), as_of=p["as_of"], run_id=run_id,
            status=RecommendationStatus.PENDING, generated_by_id=user.id if user else None,
            stockout_prediction_id=p["risk"]["id"] if p["risk"] else None,
            risk_level=p["risk"]["risk_level"] if p["risk"] else None,
            need_by_date=p["need_by_date"], order_by_date=p["replenishment"]["order_by_date"],
            scenario_key=p["recommended_key"], lines=jsonable_encoder(lines), quantity=rec_s["quantity"],
            purchase_value=Decimal(str(round(rec_s["purchase_value"], 2))),
            expected_cost=Decimal(str(round(rec_s["costs"]["total"], 2))),
            cost_breakdown=rec_s["costs"], metrics=jsonable_encoder(rec_s["metrics"]),
            scenarios=jsonable_encoder(p["scenarios"]), replenishment=jsonable_encoder(p["replenishment"]),
            in_transit=jsonable_encoder(p["in_transit"]), explanation=p["explanation"],
            settings_snapshot=jsonable_encoder(cm.as_dict()),
            solver=jsonable_encoder({**p["solver"], "horizon_days": p["horizon_days"], "need_by_days": p["need_by_days"],
                                     "reference_supplier": p["reference_supplier"], "demand": p["demand"],
                                     "replenishment_by_supplier": p["replenishment_by_supplier"]}),
        )
        db.add(rec)
        created.append(rec)
    db.flush()
    return {"run_id": run_id, "created": len(created), "superseded": superseded,
            "orders_recommended": sum(1 for r in created if r.lines),
            "purchase_value": round(sum(float(r.purchase_value) for r in created), 2),
            "solver": sol, "skipped": skipped, "recommendations": [row(db, r) for r in created]}


# ---------------------------------------------------------------- decide


def _check_open(rec: ProcurementRecommendation) -> None:
    if rec.status != RecommendationStatus.PENDING:
        raise _bad(f"Recommendation is already {rec.status.lower()}", status.HTTP_409_CONFLICT)


def approve(db: Session, rec: ProcurementRecommendation, user: User, lines_in: list[dict] | None,
            reason: str | None) -> list[SupplierOrder]:
    _check_open(rec)
    today = business_today()
    if rec.as_of != today:
        raise _bad(f"This recommendation was generated on {rec.as_of.isoformat()} — stock, forecasts and supplier "
                   f"history have changed since. Generate a new one before approving.", status.HTTP_409_CONFLICT)
    item = db.get(Consumable, rec.consumable_id)
    recommended = [(ln["supplier_id"], int(ln["quantity"]), float(ln["unit_price"])) for ln in rec.lines]
    if lines_in is None:
        final = recommended
    else:
        final = []
        seen = set()
        for ln in lines_in:
            sup = db.get(Supplier, ln["supplier_id"])
            if sup is None or sup.hospital_id != rec.hospital_id:
                raise _bad("Supplier not found", status.HTTP_404_NOT_FOUND)
            if not sup.is_active:
                raise _bad(f"{sup.name} is inactive")
            if sup.id in seen:
                raise _bad("Each supplier can appear only once")
            seen.add(sup.id)
            sp = db.scalar(select(SupplierProduct).where(SupplierProduct.supplier_id == sup.id,
                                                         SupplierProduct.consumable_id == item.id))
            if sp is None:
                raise _bad(f"{sup.name} does not list {item.name} in its catalogue")
            if ln["quantity"] < (sp.moq or 1):
                raise _bad(f"{ln['quantity']:,} is below {sup.name}'s minimum order of {sp.moq:,}")
            price = float(ln["unit_price"]) if ln.get("unit_price") is not None else float(sp.unit_price)
            final.append((sup.id, int(ln["quantity"]), round(price, 2)))
    modified = sorted(final) != sorted(recommended)
    if modified and not reason:
        raise _bad("Add a reason when changing the recommended supplier or quantity")

    if modified:  # evaluate the reviewer's plan with the same model and settings, before recording anything
        cm = CostModel(**rec.settings_snapshot)
        ctxs, _ = engine.load_contexts(db, rec.hospital_id, cm, [item.id])
        if item.id in ctxs and final:
            rec.final_evaluation = jsonable_encoder(engine.evaluate_lines(ctxs[item.id], cm, final))
        elif not final:
            rec.final_evaluation = next((s for s in rec.scenarios if s["kind"] == "none"), None)

    orders = []
    for sid, qty, price in final:
        sup = db.get(Supplier, sid)
        o = supplier_orders.create_order(
            db, user, rec.hospital_id, sup, item, qty, price, today,
            notes=f"Recorded from procurement recommendation #{rec.id} (approved by {user.full_name}). "
                  f"Not sent to the supplier by MedFlow.")
        o.recommendation_id = rec.id
        orders.append(o)
    rec.status = RecommendationStatus.APPROVED
    rec.modified = modified
    rec.final_lines = [{"supplier_id": sid, "supplier": db.get(Supplier, sid).name, "quantity": q, "unit_price": p}
                       for sid, q, p in final]
    rec.decided_by_id = user.id
    rec.decided_at = datetime.now().astimezone()
    rec.decision_reason = reason
    db.flush()
    return orders


def reject(db: Session, rec: ProcurementRecommendation, user: User, reason: str) -> None:
    _check_open(rec)
    rec.status = RecommendationStatus.REJECTED
    rec.decided_by_id = user.id
    rec.decided_at = datetime.now().astimezone()
    rec.decision_reason = reason
    db.flush()
