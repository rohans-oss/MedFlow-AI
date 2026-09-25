"""V5 — procurement intelligence API (recommend-only; approval records orders in MedFlow's own log).

GET  /procurement/settings                        cost-model parameters (+ defaults, explanations, formula)
PUT  /procurement/settings                        edit them (procurement:configure)
GET  /procurement/needs-attention                 items needing a decision this review cycle (in-transit aware)
GET  /procurement/items/{id}/plan                 live scenarios for one item (read-only, nothing stored)
POST /procurement/items/{id}/what-if              recompute under supplier delays / demand change (nothing stored)
POST /procurement/recommendations/generate        store PENDING recommendations (procurement:recommend)
GET  /procurement/recommendations                 approval queue / history
GET  /procurement/recommendations/{id}            full detail (scenarios, costs, settings snapshot)
POST /procurement/recommendations/{id}/approve    approve as recommended or modified (procurement:approve)
POST /procurement/recommendations/{id}/reject     reject with a reason (procurement:approve)
GET  /procurement/evaluation                      are the delivery (arrival) probabilities calibrated? (backtest)
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import DB, client_ip, get_owned, require
from app.core.permissions import PROCUREMENT_APPROVE, PROCUREMENT_CONFIGURE, PROCUREMENT_RECOMMEND, READ
from app.models import Consumable, ProcurementRecommendation, RecommendationStatus, Supplier, SupplierOrder, User
from app.procurement import arrival, engine
from app.schemas.procurement import (
    ApproveIn,
    ArrivalEvaluation,
    Attention,
    GenerateIn,
    GenerateOut,
    Plan,
    RecommendationDetail,
    RecommendationRow,
    RejectIn,
    SettingsOut,
    SettingsValues,
    WhatIfIn,
    WhatIfOut,
)
from app.services import alerts, audit
from app.services import procurement as svc

router = APIRouter(prefix="/procurement", tags=["procurement"])
Reader = Annotated[User, Depends(require(READ))]
Recommender = Annotated[User, Depends(require(PROCUREMENT_RECOMMEND))]
Approver = Annotated[User, Depends(require(PROCUREMENT_APPROVE))]
Configurer = Annotated[User, Depends(require(PROCUREMENT_CONFIGURE))]


def _current(db: DB, hospital_id: int) -> None:
    from app.api.risk import ensure_current  # V3 lazy daily refresh (stock, forecast and risk as of today)

    ensure_current(db, hospital_id)


# ---------------------------------------------------------------- settings


@router.get("/settings", response_model=SettingsOut)
def get_settings(user: Reader, db: DB):
    return svc.settings_out(db, user.hospital_id)


@router.put("/settings", response_model=SettingsOut)
def put_settings(body: SettingsValues, request: Request, db: DB, user: Configurer):
    _, changed = svc.save_settings(db, user.hospital_id, user, body.model_dump())
    audit.record(db, user, "procurement.settings.update", "procurement_settings", None, {"changed": changed},
                 client_ip(request))
    db.commit()
    return svc.settings_out(db, user.hospital_id)


# ---------------------------------------------------------------- live views (no writes except the V3 daily refresh)


@router.get("/needs-attention", response_model=Attention)
def needs_attention(user: Reader, db: DB):
    _current(db, user.hospital_id)
    out = engine.needs_attention(db, user.hospital_id)
    pending = svc.pending_by_item(db, user.hospital_id)
    for r in out["items"]:
        p = pending.get(r["item"]["id"])
        if p is not None:
            r["pending_recommendation_id"] = p.id
            r["pending_summary"] = ("No new order" if not p.lines else
                                    " + ".join(f"{ln['quantity']:,} from {ln['code']}" for ln in p.lines))
    return out


def _plan_ctx(db: DB, user: User, item_id: int):
    item = get_owned(db, Consumable, item_id, user.hospital_id, "Item")
    _current(db, user.hospital_id)
    return item


@router.get("/items/{item_id}/plan", response_model=Plan)
def item_plan(item_id: int, user: Reader, db: DB):
    item = _plan_ctx(db, user, item_id)
    cm = engine.cost_model(db, user.hospital_id)
    ctxs, skipped = engine.load_contexts(db, user.hospital_id, cm, [item.id])
    if item.id not in ctxs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Cannot plan {item.name}: {skipped.get(item.id)}")
    return engine.plan_item(ctxs[item.id], cm)


@router.post("/items/{item_id}/what-if", response_model=WhatIfOut)
def what_if(item_id: int, body: WhatIfIn, user: Reader, db: DB):
    """Recompute stockout risk, shortage, cost and arrival if suppliers are late / demand changes. Stores nothing."""
    item = _plan_ctx(db, user, item_id)
    delays = {}
    for d in body.delays:
        get_owned(db, Supplier, d.supplier_id, user.hospital_id, "Supplier")
        delays[d.supplier_id] = d.days
    out = engine.whatif(db, user.hospital_id, item, delays, body.demand_change_pct)
    if "error" in out:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Cannot plan {item.name}: {out['error']}")
    return out


# ---------------------------------------------------------------- recommendations


@router.post("/recommendations/generate", response_model=GenerateOut, status_code=status.HTTP_201_CREATED)
def generate(body: GenerateIn, request: Request, db: DB, user: Recommender):
    _current(db, user.hospital_id)
    ids = None
    if body.item_ids is not None:
        ids = [get_owned(db, Consumable, i, user.hospital_id, "Item").id for i in dict.fromkeys(body.item_ids)]
    out = svc.generate(db, user.hospital_id, user, ids)
    audit.record(db, user, "procurement.generate", "procurement_run", None,
                 {"run_id": out["run_id"], "created": out["created"], "superseded": out["superseded"],
                  "orders_recommended": out["orders_recommended"], "purchase_value": out["purchase_value"],
                  "solver": out["solver"]}, client_ip(request))
    db.commit()
    return out


def _owned_rec(db: DB, rec_id: int, hospital_id: int) -> ProcurementRecommendation:
    return get_owned(db, ProcurementRecommendation, rec_id, hospital_id, "Recommendation")


@router.get("/recommendations", response_model=list[RecommendationRow])
def list_recommendations(
    user: Reader, db: DB,
    status_filter: Annotated[str, Query(alias="status", pattern="^(PENDING|APPROVED|REJECTED|SUPERSEDED|decided|all)$")] = "PENDING",
    limit: int = Query(200, ge=1, le=500),
):
    q = select(ProcurementRecommendation).where(ProcurementRecommendation.hospital_id == user.hospital_id)
    if status_filter == "decided":
        q = q.where(ProcurementRecommendation.status.in_([RecommendationStatus.APPROVED, RecommendationStatus.REJECTED]))
    elif status_filter != "all":
        q = q.where(ProcurementRecommendation.status == status_filter)
    rows = db.scalars(q.order_by(ProcurementRecommendation.id.desc()).limit(limit)).all()
    out = [svc.row(db, r) for r in rows]
    if status_filter == "PENDING":  # most urgent first: orders before "no order", then by order-by date
        out.sort(key=lambda r: (not r["lines"], r["order_by_date"] or date.max, -r["purchase_value"]))
    return out


@router.get("/recommendations/{rec_id}", response_model=RecommendationDetail)
def get_recommendation(rec_id: int, user: Reader, db: DB):
    return svc.detail(db, _owned_rec(db, rec_id, user.hospital_id))


@router.post("/recommendations/{rec_id}/approve", response_model=RecommendationDetail)
def approve(rec_id: int, body: ApproveIn, request: Request, db: DB, user: Approver):
    rec = _owned_rec(db, rec_id, user.hospital_id)
    lines = None if body.lines is None else [ln.model_dump() for ln in body.lines]
    orders = svc.approve(db, rec, user, lines, body.reason)
    alerts.evaluate(db, user.hospital_id, [rec.consumable_id], refresh_risk=False)
    audit.record(db, user, "procurement.approve", "procurement_recommendation", rec.id,
                 {"sku": rec.consumable.sku, "modified": rec.modified, "reason": rec.decision_reason,
                  "recommended": rec.lines, "approved": rec.final_lines,
                  "orders": [o.reference for o in orders]}, client_ip(request))
    for o in orders:
        audit.record(db, user, "supplier_order.create", "supplier_order", o.id,
                     {"reference": o.reference, "supplier": o.supplier.code, "sku": o.consumable.sku,
                      "quantity": o.quantity_ordered, "expected_date": o.expected_date.isoformat(),
                      "recommendation_id": rec.id}, client_ip(request))
    db.commit()
    db.refresh(rec)
    return svc.detail(db, rec)


@router.post("/recommendations/{rec_id}/reject", response_model=RecommendationDetail)
def reject(rec_id: int, body: RejectIn, request: Request, db: DB, user: Approver):
    rec = _owned_rec(db, rec_id, user.hospital_id)
    svc.reject(db, rec, user, body.reason.strip())
    audit.record(db, user, "procurement.reject", "procurement_recommendation", rec.id,
                 {"sku": rec.consumable.sku, "reason": rec.decision_reason, "recommended": rec.lines}, client_ip(request))
    db.commit()
    db.refresh(rec)
    return svc.detail(db, rec)


@router.get("/evaluation", response_model=ArrivalEvaluation)
def evaluation(user: Reader, db: DB):
    """Expanding-window backtest of P(delivered within quoted + k days) — the probabilities that weight in-transit
    orders and new-order scenarios — against the 'quote is certain' assumption."""
    orders = db.scalars(select(SupplierOrder).where(SupplierOrder.hospital_id == user.hospital_id))
    return arrival.backtest([engine._rec(o) for o in orders])
