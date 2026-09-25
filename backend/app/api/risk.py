"""V3 — stockout risk API.

Reads the newest `stockout_predictions` row per item. When the stored risk predates today or the active
forecast/risk models, it is recomputed first (scoring only — never training). Training happens only via
POST /stockout-risks/train, `python -m app.ml.train` or the Docker entrypoint.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import DB, client_ip, get_owned, require
from app.core.permissions import READ, TRAIN_RISK
from app.core.security import business_today
from app.models import (
    Consumable,
    ModelVersion,
    RiskLevel,
    RiskModelVersion,
    StockoutPrediction,
    User,
)
from app.risk import engine
from app.risk.features import HORIZON
from app.risk.pipeline import run_training
from app.schemas.risk import (
    RiskCounts,
    RiskDetail,
    RiskItem,
    RiskModelDetail,
    RiskModelOut,
    RiskOverview,
    RiskTrainResult,
)
from app.services import alerts, audit

router = APIRouter(prefix="/stockout-risks", tags=["stockout risk"])
Reader = Annotated[User, Depends(require(READ))]
Trainer = Annotated[User, Depends(require(TRAIN_RISK))]
_ORDER = {RiskLevel.HIGH: 0, RiskLevel.MEDIUM: 1, RiskLevel.LOW: 2}


def ensure_current(db: DB, hospital_id: int) -> None:
    """Lazy daily refresh: recompute risk (and risk alerts) when the stored risk is from an earlier day/model."""
    if engine.ensure_fresh(db, hospital_id):
        alerts.evaluate(db, hospital_id, refresh_risk=False)
        db.commit()


def _main_reason(p: StockoutPrediction) -> str:
    rs = p.reasons or []
    for r in rs:
        if r.startswith(("Risk raised", "Main factors", "Out of stock")):
            return r
    return rs[0] if rs else ""


def _item(p: StockoutPrediction, c: Consumable, today) -> RiskItem:
    return RiskItem(
        consumable_id=c.id, name=c.name, sku=c.sku, unit=c.unit, category=c.category.name if c.category else None,
        usable_stock=p.usable_stock, reorder_level=c.reorder_level, forecast_7=p.forecast_7, forecast_14=p.forecast_14,
        forecast_30=p.forecast_30, days_of_stock_remaining=p.days_of_stock_remaining,
        expected_stockout_date=p.expected_stockout_date, shortage_quantity=p.shortage_quantity,
        expiring_quantity=p.expiring_quantity, lead_time_days=p.lead_time_days, order_by_date=p.order_by_date,
        order_overdue=p.order_by_date is not None and p.order_by_date < today,
        can_replenish_in_time=p.can_replenish_in_time, probability=p.probability, risk_level=p.risk_level,
        out_of_stock=p.usable_stock <= 0, probability_source=p.probability_source, main_reason=_main_reason(p),
        as_of=p.as_of, updated_at=p.created_at,
    )


@router.get("", response_model=RiskOverview)
def overview(user: Reader, db: DB, level: Annotated[str | None, Query(pattern="^(HIGH|MEDIUM|LOW)$")] = None):
    ensure_current(db, user.hospital_id)
    risk_mv, fc = engine.active_models(db, user.hospital_id)
    empty = RiskCounts(high=0, medium=0, low=0, out_of_stock=0, total=0)
    if risk_mv is None or fc is None:
        msg = ("Train the demand-forecasting models first (Forecasts page)." if fc is None
               else "Train the stockout-risk model to see risk.")
        return RiskOverview(model=risk_mv, forecast_model=fc.name if fc else None, as_of=None, horizon_days=HORIZON,
                            counts=empty, items=[], message=msg)
    today = business_today()
    preds = engine.latest(db, user.hospital_id)
    items = {c.id: c for c in db.scalars(select(Consumable).where(Consumable.hospital_id == user.hospital_id,
                                                                  Consumable.is_active.is_(True)))}
    rows = [_item(p, items[cid], today) for cid, p in preds.items() if cid in items]
    counts = RiskCounts(high=sum(r.risk_level == RiskLevel.HIGH for r in rows),
                        medium=sum(r.risk_level == RiskLevel.MEDIUM for r in rows),
                        low=sum(r.risk_level == RiskLevel.LOW for r in rows),
                        out_of_stock=sum(r.out_of_stock for r in rows), total=len(rows))
    if level:
        rows = [r for r in rows if r.risk_level == level]
    rows.sort(key=lambda r: (_ORDER.get(r.risk_level, 3), -r.probability,
                             r.days_of_stock_remaining if r.days_of_stock_remaining is not None else 999))
    return RiskOverview(model=risk_mv, forecast_model=fc.name, as_of=max((r.as_of for r in rows), default=None),
                        horizon_days=HORIZON, counts=counts, items=rows)


@router.get("/models", response_model=list[RiskModelOut])
def list_models(user: Reader, db: DB):
    return db.scalars(select(RiskModelVersion).where(RiskModelVersion.hospital_id == user.hospital_id)
                      .order_by(RiskModelVersion.trained_at.desc(), RiskModelVersion.id.desc())).all()


@router.get("/models/{model_id}", response_model=RiskModelDetail)
def model_detail(model_id: int, user: Reader, db: DB):
    mv = get_owned(db, RiskModelVersion, model_id, user.hospital_id, "Risk model")
    run = db.scalars(select(RiskModelVersion).where(RiskModelVersion.hospital_id == user.hospital_id,
                                                    RiskModelVersion.run_id == mv.run_id)
                     .order_by(RiskModelVersion.id)).all()
    ids = {e["consumable_id"] for m in run for e in (m.evaluation or {}).get("events", [])}
    names = dict(db.execute(select(Consumable.id, Consumable.name).where(Consumable.id.in_(ids))).all()) if ids else {}
    return RiskModelDetail(model=mv, run_candidates=run, item_names=names)


@router.post("/train", response_model=RiskTrainResult)
def train(request: Request, db: DB, user: Trainer):
    try:
        result = run_training(db, user.hospital_id, user.id)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    alerts.evaluate(db, user.hospital_id)  # refreshes risk for every item with the new model, then alerts
    bt = result["backtest"][result["model_type"]]
    audit.record(db, user, "risk.train", "risk_model_version", None,
                 {"run_id": result["run_id"], "active_model": result["active_model"],
                  "backtest": {k: bt.get(k) for k in ("precision", "recall", "pr_auc", "fp", "fn", "lead_time_recall")}},
                 client_ip(request))
    db.commit()
    return result


@router.post("/refresh", response_model=RiskOverview)
def refresh(request: Request, db: DB, user: Trainer):
    """Recompute current risk for all items and update risk alerts (no training)."""
    if engine.active_models(db, user.hospital_id)[0] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No stockout-risk model yet — train it first.")
    stats = alerts.evaluate(db, user.hospital_id)
    audit.record(db, user, "risk.refresh", "hospital", user.hospital_id, stats, client_ip(request))
    db.commit()
    return overview(user, db)


@router.get("/{item_id}", response_model=RiskDetail)
def item_risk(item_id: int, user: Reader, db: DB):
    item = get_owned(db, Consumable, item_id, user.hospital_id, "Item")
    ensure_current(db, user.hospital_id)
    p = engine.latest(db, user.hospital_id, [item.id]).get(item.id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No stockout risk for this item yet (models not trained, or the item is inactive).")
    risk_mv = db.get(RiskModelVersion, p.risk_model_version_id) if p.risk_model_version_id else None
    fc = db.get(ModelVersion, p.forecast_model_version_id) if p.forecast_model_version_id else None
    since = p.created_at - timedelta(days=30)
    hist = db.scalars(select(StockoutPrediction).where(StockoutPrediction.consumable_id == item.id,
                                                       StockoutPrediction.created_at >= since)
                      .order_by(StockoutPrediction.id)).all()
    base = _item(p, item, business_today())
    return RiskDetail(
        **base.model_dump(), horizon_days=p.horizon_days, reasons=p.reasons or [], drivers=p.drivers or [],
        projection=p.projection or [],
        history=[{"at": h.created_at, "probability": h.probability, "risk_level": h.risk_level} for h in hist[-60:]],
        risk_model=risk_mv.name if risk_mv else None, forecast_model=fc.name if fc else None,
        warn_threshold=risk_mv.warn_threshold if risk_mv else None, high_threshold=risk_mv.high_threshold if risk_mv else None,
    )
