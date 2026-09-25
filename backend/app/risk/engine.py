"""V3 serving: current stockout risk per item.

    usable batches now (V1) ─┐
    served forecast (V2A/B) ─┼─► FEFO projection (V3.1) ─► stockout date, days left, shortage, expiry loss
    supplier lead time ──────┘                           └► order-by date, can a delivery arrive in time?
    point-in-time features ──► active risk model (V3.2) ─► probability ─► risk level (thresholds) + SHAP factors
                                                                       └► plain-English reasons (from the numbers)
Each refresh appends a `stockout_predictions` row per item; the newest row per item is the current risk.
Refreshed after every stock movement of the item, after (re)training, and lazily when the day changes.
"""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.db.base import utcnow
from app.ml.data import _local_date
from app.models import (
    Forecast,
    ModelVersion,
    MovementType,
    RiskLevel,
    RiskModelVersion,
    StockMovement,
    StockoutPrediction,
)
from app.risk.features import AHEAD, FEATURE_LABELS, FEATURES, HORIZON, build
from app.risk.history import EXPIRY_WINDOW, load_history
from app.risk.model import RiskXGB, cover_buckets, cover_probability, cover_rule_label
from app.risk.pipeline import FLOOR_DAYS, _static
from app.risk.projection import project, replenishment

LOOKBACK_DAYS = 120  # history needed for features (60-day stockout count + 56-day weekday profile)
_BOOSTERS: dict[int, RiskXGB] = {}


def active_models(db: Session, hospital_id: int) -> tuple[RiskModelVersion | None, ModelVersion | None]:
    risk = db.scalar(select(RiskModelVersion).where(RiskModelVersion.hospital_id == hospital_id,
                                                    RiskModelVersion.is_active.is_(True)))
    fc = db.scalar(select(ModelVersion).where(ModelVersion.hospital_id == hospital_id, ModelVersion.is_active.is_(True)))
    return risk, fc


def _booster(mv: RiskModelVersion) -> RiskXGB | None:
    if mv.model_type != "xgboost_classifier" or not mv.artifact:
        return None
    if mv.id not in _BOOSTERS:
        _BOOSTERS[mv.id] = RiskXGB.from_bytes(mv.artifact)
    return _BOOSTERS[mv.id]


_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def lead_time_floor(days_left: int | None, rep, today: date) -> str | None:
    """Deterministic safety net (V3.1): the level can't be lower than what the lead time implies.

    HIGH: even an order placed today would arrive after the projected stockout.
    MEDIUM: the order-by date is within FLOOR_DAYS days.
    V3 does not count orders in transit (V5 Procurement does, weighted by arrival probability), so this may flag
    items whose delivery is on its way.
    """
    if days_left is None or rep.lead_time_days is None:
        return None
    if rep.can_replenish_in_time is False:
        return RiskLevel.HIGH
    if rep.order_by_date is not None and rep.order_by_date <= today + timedelta(days=FLOOR_DAYS):
        return RiskLevel.MEDIUM
    return None


def _floor_reason(p: StockoutPrediction, floor: str) -> str:
    if floor == RiskLevel.HIGH:
        return (f"Risk raised to HIGH by the lead-time rule: an order placed today ({p.lead_time_days}-day lead time) would "
                f"arrive after the projected stockout on {p.expected_stockout_date.isoformat()} — unless a delivery is already "
                f"on its way (this projection does not count orders in transit; see Procurement).")
    return (f"Risk raised to MEDIUM by the lead-time rule: order by {p.order_by_date.isoformat()} to receive stock before the "
            f"projected stockout — unless a delivery is already on its way (this projection does not count orders in "
            f"transit; see Procurement).")


def level_for(prob: float, mv: RiskModelVersion) -> str:
    if prob >= mv.high_threshold:
        return RiskLevel.HIGH
    if prob >= mv.warn_threshold:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _served_daily(db: Session, fc: ModelVersion, ids: list[int], today: date) -> dict[int, list[float]]:
    rows = db.execute(select(Forecast.consumable_id, Forecast.forecast_date, Forecast.predicted)
                      .where(Forecast.model_version_id == fc.id, Forecast.consumable_id.in_(ids),
                             Forecast.forecast_date >= today).order_by(Forecast.forecast_date)).all()
    out: dict[int, list[float]] = {}
    for cid, _d, p in rows:
        out.setdefault(cid, []).append(float(p))
    for cid, vals in out.items():  # a stale model covers fewer than AHEAD days: extend with its last-week average
        if len(vals) < AHEAD:
            tail = float(np.mean(vals[-7:])) if vals else 0.0
            out[cid] = vals + [tail] * (AHEAD - len(vals))
    return out


def _consumed_today(db: Session, hospital_id: int, ids: list[int], today: date) -> dict[int, float]:
    since = datetime.combine(today - timedelta(days=1), datetime.min.time())
    rows = db.execute(select(StockMovement.consumable_id, StockMovement.created_at, StockMovement.quantity)
                      .where(StockMovement.hospital_id == hospital_id, StockMovement.consumable_id.in_(ids),
                             StockMovement.movement_type.in_([MovementType.ISSUE, MovementType.RETURN]),
                             StockMovement.created_at >= since)).all()
    out: dict[int, float] = {}
    for cid, ts, q in rows:
        if _local_date(ts) == today:
            out[cid] = out.get(cid, 0.0) - float(q)
    return out


def refresh(db: Session, hospital_id: int, consumable_ids: list[int] | None = None) -> list[StockoutPrediction]:
    """Recompute and store current risk for the given items (default: all). No-op without trained models."""
    risk_mv, fc = active_models(db, hospital_id)
    if risk_mv is None or fc is None:
        return []
    today = business_today()
    lh = load_history(db, hospital_id, data_end=today - timedelta(days=1), consumable_ids=consumable_ids,
                      lookback_days=LOOKBACK_DAYS)
    ids = list(lh.items)
    if not ids:
        return []
    served = _served_daily(db, fc, ids, today)
    used_today = _consumed_today(db, hospital_id, ids, today)
    booster = _booster(risk_mv)
    rates = (risk_mv.params or {}).get("cover_rates")
    known_until = None if lh.schedule_end is None else pd.Timestamp(lh.schedule_end)
    out = []
    for cid, it in lh.items.items():
        usable = float(sum(b.quantity for b in it.batches))
        expiring = float(sum(b.quantity for b in it.batches
                             if b.expiry_date is not None and b.expiry_date < today + timedelta(days=EXPIRY_WINDOW)))
        demand = list(served.get(cid, [0.0] * AHEAD))
        demand[0] = max(demand[0] - used_today.get(cid, 0.0), 0.0)  # part of today's demand is already issued
        proj = project(it.batches, demand, today)
        rep = replenishment(proj, today, it.lead_time_days)

        prob, drivers, feats = 0.0, [], None
        if len(it.frame) and usable > 0:
            f = build(it.frame, _static(it), kit_ahead=it.kit_future, origins=np.array([len(it.frame) - 1]),
                      usable_override=usable, expiring_override=expiring,
                      known_until=known_until)
            feats = f.iloc[0]
            if booster is not None:
                prob = float(booster.predict(f)[0])
                contrib = booster.contributions(f)[0]
                drivers = sorted(({"feature": k, "label": FEATURE_LABELS[k], "value": _num(feats[k]),
                                   "contribution": round(float(v), 4)}  # log-odds; > 0 raises risk
                                  for k, v in zip(FEATURES, contrib[:-1], strict=True) if abs(v) > 1e-6),
                                 key=lambda d: -abs(d["contribution"]))[:8]
            elif rates:
                prob = float(cover_probability(f, rates)[0])
        floor = None
        if usable <= 0:
            prob, level = 1.0, RiskLevel.HIGH
        else:
            level = level_for(prob, risk_mv)
            floor = lead_time_floor(proj.days_of_stock_remaining, rep, today)
            if floor is not None and _RANK[floor] > _RANK[level]:
                level = floor
            else:
                floor = None

        pred = StockoutPrediction(
            hospital_id=hospital_id, consumable_id=cid, risk_model_version_id=risk_mv.id, forecast_model_version_id=fc.id,
            created_at=utcnow(), as_of=today, horizon_days=HORIZON, usable_stock=int(round(usable)),
            forecast_7=round(proj.demand(7), 1), forecast_14=round(proj.demand(14), 1), forecast_30=round(proj.demand(AHEAD), 1),
            days_of_stock_remaining=proj.days_of_stock_remaining, expected_stockout_date=proj.stockout_date,
            shortage_quantity=int(round(proj.shortage(HORIZON))), expiring_quantity=int(round(proj.expired())),
            lead_time_days=rep.lead_time_days, order_by_date=rep.order_by_date, can_replenish_in_time=rep.can_replenish_in_time,
            probability=round(prob, 4), risk_level=level, probability_source=risk_mv.model_type,
            drivers=drivers,
            projection=[{"date": d.date.isoformat(), "demand": round(d.demand, 2), "stock_end": round(d.stock_end, 1),
                         "unmet": round(d.unmet, 2), "expired": round(d.expired, 1)} for d in proj.days],
        )
        pred.reasons = reasons(pred, it, feats, drivers, today, rates if booster is None else None)
        if floor is not None:
            pred.reasons.insert(0, _floor_reason(pred, floor))
        db.add(pred)
        out.append(pred)
    db.flush()
    return out


def _days(n: int | None) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else round(f, 3)


# A phrase is used only when the feature's actual value supports it (SHAP says "pushes risk up", not "is high").
_FACTOR_RULES = [
    ("stock_to_forecast_7", lambda f: f["stock_to_forecast_7"] < 1.0, "stock below next week's demand"),
    ("stock_to_forecast_14", lambda f: f["stock_to_forecast_14"] < 1.0, "stock below 14-day demand"),
    ("stock_to_forecast_30", lambda f: f["stock_to_forecast_30"] < 1.0, "stock below 30-day demand"),
    ("usable_stock", lambda f: f["stock_to_forecast_14"] < 1.5, "low current stock"),
    ("projected_cover_days", lambda f: f["projected_cover_days"] <= 14, "few days of cover left"),
    ("cover_minus_lead_time", lambda f: f["cover_minus_lead_time"] < 0, "cover shorter than supplier lead time"),
    ("cover_minus_lead_time", lambda f: 0 <= f["cover_minus_lead_time"] <= 3, "little cover beyond supplier lead time"),
    ("stock_to_reorder", lambda f: f["stock_to_reorder"] <= 1.2, "stock near or below the reorder level"),
    ("trend_7_28", lambda f: f["trend_7_28"] >= 1.1, "rising consumption"),
    ("recent_mean_7", lambda f: f["trend_7_28"] >= 1.1, "rising consumption"),
    ("forecast_14", lambda f: f["recent_mean_28"] > 0 and f["forecast_14"] > 14 * f["recent_mean_28"] * 1.1, "demand expected to rise"),
    ("forecast_7", lambda f: f["recent_mean_28"] > 0 and f["forecast_7"] > 7 * f["recent_mean_28"] * 1.1, "demand expected to rise"),
    ("procedure_demand_14", lambda f: f["procedure_share_14"] >= 0.2, "high procedure demand"),
    ("procedure_share_14", lambda f: f["procedure_share_14"] >= 0.2, "high procedure demand"),
    ("volatility_28", lambda f: f["volatility_28"] >= 0.5, "volatile demand"),
    ("lead_time_days", lambda f: f["lead_time_days"] >= 7, "long supplier lead time"),
    ("expiring_share_14", lambda f: f["expiring_share_14"] >= 0.1, "stock expiring soon"),
    ("expiring_14", lambda f: f["expiring_share_14"] >= 0.1, "stock expiring soon"),
    ("stockout_days_60", lambda f: f["stockout_days_60"] > 0, "recent stockout history"),
    ("days_since_stockout", lambda f: f["days_since_stockout"] < 60, "recent stockout history"),
    ("days_since_receipt", lambda f: f["days_since_receipt"] >= 21, "long time since last delivery"),
]


def main_factors(drivers: list[dict], feats, n: int = 3) -> list[str]:
    """Plain phrases for features that push risk UP (SHAP > 0) AND whose value supports the phrase; no duplicates."""
    if feats is None:
        return []
    f = {k: (float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else np.nan) for k, v in feats.items()
         if k in FEATURE_LABELS}
    out: list[str] = []
    for d in sorted(drivers, key=lambda d: -d["contribution"]):
        if d["contribution"] <= 0.05:
            break
        for feature, ok, phrase in _FACTOR_RULES:
            if feature == d["feature"] and phrase not in out:
                try:
                    if ok(f):
                        out.append(phrase)
                        break
                except (KeyError, TypeError):
                    continue
        if len(out) == n:
            break
    return out


def reasons(p: StockoutPrediction, it, feats, drivers: list[dict], today: date, rates: list[float] | None) -> list[str]:
    """Plain-English reasons built only from computed numbers (no generated text)."""
    unit = it.unit
    out: list[str] = []
    if p.usable_stock <= 0:
        out.append(f"Out of stock now: usable stock is 0 {unit}.")
        if p.lead_time_days is not None:
            out.append(f"A delivery ordered today would arrive in about {p.lead_time_days} days "
                       f"({(today + timedelta(days=p.lead_time_days)).isoformat()}).")
        return out
    f = main_factors(drivers, feats)
    if f and p.risk_level != RiskLevel.LOW:
        out.append("Main factors: " + " + ".join(f) + ".")
    if p.expected_stockout_date is not None:
        out.append(f"Usable stock ({p.usable_stock:,} {unit}) covers about {_days(p.days_of_stock_remaining)} of forecast demand; "
                   f"without a delivery it is expected to run out on {p.expected_stockout_date.isoformat()}.")
    else:
        out.append(f"Usable stock ({p.usable_stock:,} {unit}) covers the full 30-day forecast ({p.forecast_30:,.0f} {unit}).")
    if p.shortage_quantity > 0:
        out.append(f"Expected 14-day demand ({p.forecast_14:,.0f} {unit}) exceeds usable stock: about "
                   f"{p.shortage_quantity:,} {unit} short without a delivery.")
    if feats is not None:
        share, proc = float(feats["procedure_share_14"]), float(feats["procedure_demand_14"])
        if share >= 0.2 and proc >= 1:
            out.append(f"Scheduled procedures over the next 14 days imply about {proc:,.0f} {unit} by the configured kits "
                       f"(14-day forecast: {p.forecast_14:,.0f} {unit}).")
        trend = float(feats["trend_7_28"])
        if trend >= 1.15 and float(feats["recent_mean_28"]) > 0:
            out.append(f"Consumption is rising: last 7 days average {float(feats['recent_mean_7']):,.1f}/day vs "
                       f"{float(feats['recent_mean_28']):,.1f}/day over 28 days (+{trend - 1:.0%}).")
        so = int(feats["stockout_days_60"])
        if so > 0:
            out.append(f"This item was out of stock on {so} of the last 60 days.")
        if it.reorder_level and p.usable_stock <= it.reorder_level:
            out.append(f"Stock is at or below the reorder level ({it.reorder_level:,} {unit}).")
    if p.lead_time_days is not None and p.expected_stockout_date is not None:
        if p.can_replenish_in_time:
            if p.order_by_date == today:
                out.append(f"Order today: with a {p.lead_time_days}-day lead time, a delivery arrives just before the projected stockout.")
            else:
                out.append(f"Order by {p.order_by_date.isoformat()} for a delivery to arrive before the projected stockout "
                           f"(lead time {p.lead_time_days} days).")
        else:
            out.append(f"An order placed today would arrive in about {p.lead_time_days} days — after the projected stockout "
                       f"on {p.expected_stockout_date.isoformat()}. Deliveries already on order are not counted here (see Procurement).")
    if p.expiring_quantity > 0:
        out.append(f"About {p.expiring_quantity:,} {unit} are expected to expire before they can be used.")
    if rates is not None and feats is not None:
        out.append(f"Probability from the cover rule: items with {cover_rule_label(_bucket(feats))} of projected cover "
                   f"stocked out within 14 days {p.probability:.0%} of the time in training data.")
    return out


def _bucket(feats) -> int:
    return int(cover_buckets(np.array([float(feats["projected_cover_days"])]))[0])


def latest(db: Session, hospital_id: int, consumable_ids: list[int] | None = None) -> dict[int, StockoutPrediction]:
    sub = (select(StockoutPrediction.consumable_id, func.max(StockoutPrediction.id).label("mid"))
           .where(StockoutPrediction.hospital_id == hospital_id).group_by(StockoutPrediction.consumable_id))
    if consumable_ids is not None:
        sub = sub.where(StockoutPrediction.consumable_id.in_(consumable_ids))
    sub = sub.subquery()
    rows = db.scalars(select(StockoutPrediction).join(sub, StockoutPrediction.id == sub.c.mid)).all()
    return {r.consumable_id: r for r in rows}


def ensure_fresh(db: Session, hospital_id: int) -> bool:
    """Refresh all items when the current risk predates today or the active models. Returns True if refreshed."""
    risk_mv, fc = active_models(db, hospital_id)
    if risk_mv is None or fc is None:
        return False
    newest = db.scalar(select(StockoutPrediction).where(StockoutPrediction.hospital_id == hospital_id)
                       .order_by(StockoutPrediction.id.desc()).limit(1))
    stale = (newest is None or newest.as_of < business_today() or newest.risk_model_version_id != risk_mv.id
             or newest.forecast_model_version_id != fc.id)
    if stale:
        refresh(db, hospital_id)
    return stale
