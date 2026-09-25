"""Version 2 — demand forecast API.

Forecasts are produced by a training run (POST /forecasts/train, `python -m app.ml.train`, or the Docker
entrypoint) and stored; these endpoints read the active model's stored forecasts so every number is
reproducible and tied to a model version.
"""

import math
from datetime import date, timedelta
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DB, client_ip, get_owned, require
from app.core.permissions import READ, TRAIN_FORECASTS
from app.core.security import business_today
from app.ml.data import load_panel
from app.ml.features import FEATURE_LABELS
from app.ml.pipeline import InsufficientDataError, prune_old_forecasts, run_training
from app.ml.procedures import PROC_FEATURES, ProcedureData, item_impact, load_procedure_data
from app.models import Consumable, Forecast, ModelItemMetric, ModelVersion, User
from app.schemas.forecasting import (
    Backtest,
    DailyForecast,
    Driver,
    ForecastListItem,
    ForecastOverview,
    HistoryPoint,
    HorizonTotal,
    ItemForecast,
    ItemMetricOut,
    ModelComparison,
    ModelDetail,
    ModelVersionOut,
    ProcedureImpact,
    ProcedureTypeImpact,
    TrainResult,
)
from app.services import alerts, audit, stock

router = APIRouter(prefix="/forecasts", tags=["forecasts"])
Reader = Annotated[User, Depends(require(READ))]
Trainer = Annotated[User, Depends(require(TRAIN_FORECASTS))]

Z80 = 1.2816  # two-sided 80% normal interval
INTERVAL_NOTE = "approx. 80% range from holdout errors (daily noise + systematic level error)"
MAX_DAYS = 30


def _active_model(db: Session, hospital_id: int) -> ModelVersion | None:
    return db.scalar(select(ModelVersion).where(ModelVersion.hospital_id == hospital_id, ModelVersion.is_active.is_(True)))


def _require_model(db: Session, hospital_id: int) -> ModelVersion:
    mv = _active_model(db, hospital_id)
    if not mv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No forecasting model has been trained yet. Train one first.")
    return mv


def _is_stale(mv: ModelVersion) -> bool:
    return mv.data_end < business_today() - timedelta(days=1)


def _interval(pred: float, m: ModelItemMetric | None, days: int) -> tuple[float, float]:
    """Heuristic range: independent daily noise (σ·√h) plus the item's systematic holdout error (|mean error|·h).

    Daily errors are autocorrelated, so σ·√h alone is far too narrow over multi-day horizons.
    """
    if m is None:
        return pred, pred
    level = abs(m.predicted_total - m.actual_total) / m.n_points if m.n_points else 0.0
    half = Z80 * m.residual_std * math.sqrt(days) + level * days
    return max(0.0, pred - half), pred + half


def _recent_rate(db: Session, hospital_id: int, item_ids: list[int] | None, data_end: date) -> dict[int, float]:
    """Average daily consumption over the 30 days ending at data_end (known days only)."""
    rates: dict[int, float] = {}
    panel = load_panel(db, hospital_id, data_end)
    if panel.item_actual.empty:
        return rates
    last = panel.item_actual.iloc[-30:]
    for item in last.columns:
        if item_ids is None or item in item_ids:
            known = last[item].dropna()
            rates[int(item)] = float(known.mean()) if len(known) else 0.0
    return rates


# ---------------------------------------------------------------- V2B helpers


def _source(model_type: str) -> str:
    return {"xgboost_procedure": "procedure_aware", "xgboost": "consumption"}.get(model_type, "baseline")


def _run_versions(db: Session, mv: ModelVersion) -> dict[str, ModelVersion]:
    return {v.model_type: v for v in db.scalars(select(ModelVersion).where(
        ModelVersion.hospital_id == mv.hospital_id, ModelVersion.run_id == mv.run_id))}


def _pct(w: float | None) -> str:
    return "—" if w is None else f"{w * 100:.1f}%"


def _comparison(mv: ModelVersion, run: dict[str, ModelVersion], proc: ProcedureData | None) -> ModelComparison:
    v2a, v2b = run.get("xgboost"), run.get("xgboost_procedure")
    v2a_w = v2a.metrics.get("wape") if v2a else None
    v2b_w = v2b.metrics.get("wape") if v2b else None
    improved = mv.model_type == "xgboost_procedure"
    if v2b is None:
        summary = "Procedure-aware model (V2B) not trained — no usable procedure data. Forecasts use recent consumption."
    elif improved:
        summary = f"V2B improved WAPE from {_pct(v2a_w)} to {_pct(v2b_w)}. The procedure-aware model is active."
    elif mv.model_type == "xgboost":
        summary = f"V2B did not improve on V2A ({_pct(v2b_w)} vs {_pct(v2a_w)}). V2A remains the active forecasting model."
    else:
        summary = f"A baseline had the lowest holdout WAPE ({_pct(mv.metrics.get('wape'))}); neither XGBoost model is active."
    pdata = (v2b.params or {}).get("procedure_data", {}) if v2b else {}
    changed = bool(v2b and proc is not None and pdata.get("fingerprint") and pdata["fingerprint"] != proc.fingerprint)
    return ModelComparison(
        v2a_model=v2a.name if v2a else None, v2a_wape=v2a_w, v2b_model=v2b.name if v2b else None, v2b_wape=v2b_w,
        v2b_trained=v2b is not None, improved=improved, active_source=_source(mv.model_type), summary=summary,
        schedule_through=pdata.get("schedule_through"), falls_back_to_v2a_from=pdata.get("falls_back_to_v2a_from"),
        schedule_changed_since_training=changed,
    )


def _item_daily(db: Session, mv: ModelVersion | None, item_id: int) -> np.ndarray | None:
    if mv is None:
        return None
    vals = db.scalars(select(Forecast.predicted).where(Forecast.model_version_id == mv.id, Forecast.consumable_id == item_id)
                      .order_by(Forecast.horizon)).all()
    return np.array(vals) if vals else None


def _procedure_impact(db: Session, mv: ModelVersion, run: dict[str, ModelVersion], proc: ProcedureData,
                      item: Consumable, days: int, served_total: float) -> ProcedureImpact:
    start = mv.data_end + timedelta(days=1)
    imp = item_impact(proc, item.id, start, days)
    v2a_d = _item_daily(db, run.get("xgboost"), item.id)
    v2b_d = _item_daily(db, run.get("xgboost_procedure"), item.id)
    v2a_t = round(float(v2a_d[:days].sum()), 1) if v2a_d is not None else None
    v2b_t = round(float(v2b_d[:days].sum()), 1) if v2b_d is not None else None
    contribution = None
    if run.get("xgboost_procedure"):
        m = db.scalar(select(ModelItemMetric).where(ModelItemMetric.model_version_id == run["xgboost_procedure"].id,
                                                    ModelItemMetric.consumable_id == item.id))
        if m and m.drivers:
            dh = min((int(k) for k in m.drivers if int(k) >= days), default=max(int(k) for k in m.drivers))
            contribution = round(sum(v for k, v in m.drivers[str(dh)].items() if k in PROC_FEATURES), 1)
    n, q = imp["scheduled_procedures"], imp["expected_quantity"]
    if n == 0:
        note = "No relevant procedures are scheduled in this period."
    else:
        note = (f"{n} scheduled procedures that use this item need about {q:,.0f} {item.unit} "
                f"using the configured (synthetic demo) kit quantities.")
    return ProcedureImpact(
        horizon_days=days, start=start, scheduled_procedures=n, expected_quantity=q,
        types=[ProcedureTypeImpact(**t) for t in imp["types"]], departments=imp["departments"], daily=imp["daily"],
        v2a_forecast=v2a_t, v2b_forecast=v2b_t,
        procedure_effect=round(v2b_t - v2a_t, 1) if v2a_t is not None and v2b_t is not None else None,
        model_procedure_contribution=contribution,
        share_of_forecast=round(q / served_total, 4) if served_total > 0 else None, note=note,
    )


def _days_remaining(daily: np.ndarray, stock_units: int) -> int | None:
    """First forecast day on which cumulative demand exceeds usable stock (1-based); None if not within the horizon."""
    if stock_units <= 0:
        return 0
    over = np.nonzero(np.cumsum(daily) > stock_units)[0]
    return int(over[0]) if len(over) else None


# ---------------------------------------------------------------- list & models


@router.get("", response_model=ForecastOverview)
def forecast_overview(user: Reader, db: DB, days: int = Query(14, ge=1, le=MAX_DAYS)):
    mv = _active_model(db, user.hospital_id)
    if not mv:
        return ForecastOverview(model=None, horizon_days=days, is_stale=False, total_value=0.0, items=[])

    rows = db.execute(
        select(Forecast.consumable_id, Forecast.horizon, Forecast.predicted)
        .where(Forecast.model_version_id == mv.id, Forecast.horizon <= MAX_DAYS)
    ).all()
    daily: dict[int, np.ndarray] = {}
    for cid, h, p in rows:
        daily.setdefault(cid, np.zeros(MAX_DAYS))[h - 1] = p
    metrics = {m.consumable_id: m for m in db.scalars(select(ModelItemMetric).where(ModelItemMetric.model_version_id == mv.id))}
    inv = {r["consumable_id"]: r for r in stock.inventory_rows(db, user.hospital_id)}
    rates = _recent_rate(db, user.hospital_id, None, mv.data_end)
    proc = load_procedure_data(db, user.hospital_id, mv.data_end, MAX_DAYS)
    window = proc.rows
    if not window.empty:
        start = mv.data_end + timedelta(days=1)
        window = window[(window["date"] >= str(start)) & (window["date"] <= str(start + timedelta(days=days - 1)))]
    per_item = window.groupby("item")[["count", "expected"]].sum() if not window.empty else None

    items, total_value = [], 0.0
    for cid, arr in daily.items():
        r = inv.get(cid)
        if not r or not r["is_active"]:
            continue
        m = metrics.get(cid)
        pred = float(arr[:days].sum())
        lo, hi = _interval(pred, m, days)
        rate = rates.get(cid, 0.0)
        per_day_30 = float(arr.mean())
        items.append(ForecastListItem(
            consumable_id=cid, name=r["name"], sku=r["sku"], unit=r["unit"], category=r["category"],
            predicted_demand=round(pred, 1), lower=round(lo, 1), upper=round(hi, 1),
            avg_daily_last_30=round(rate, 2),
            change_vs_recent=round(pred / days / rate - 1, 4) if rate > 0 else None,
            usable_stock=r["usable_stock"],
            days_of_cover=round(r["usable_stock"] / per_day_30, 1) if per_day_30 > 0 else None,
            backtest_wape=m.wape if m else None,
            scheduled_procedures=int(per_item.loc[cid, "count"]) if per_item is not None and cid in per_item.index else 0,
            procedure_driven_demand=round(float(per_item.loc[cid, "expected"]), 1)
            if per_item is not None and cid in per_item.index else 0.0,
            expected_shortage=max(0, round(pred - r["usable_stock"])),
        ))
        total_value += pred * r["unit_cost"]
    items.sort(key=lambda x: x.name)
    return ForecastOverview(model=ModelVersionOut.model_validate(mv), horizon_days=days, is_stale=_is_stale(mv),
                            total_value=round(total_value, 2), items=items,
                            comparison=_comparison(mv, _run_versions(db, mv), proc))


@router.get("/models", response_model=list[ModelVersionOut])
def list_models(user: Reader, db: DB, limit: int = Query(30, ge=1, le=200)):
    return db.scalars(
        select(ModelVersion).where(ModelVersion.hospital_id == user.hospital_id)
        .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc()).limit(limit)
    ).all()


@router.get("/models/{model_id}", response_model=ModelDetail)
def model_detail(model_id: int, user: Reader, db: DB):
    mv = get_owned(db, ModelVersion, model_id, user.hospital_id, "Model")
    run = db.scalars(select(ModelVersion).where(ModelVersion.hospital_id == user.hospital_id,
                                                ModelVersion.run_id == mv.run_id).order_by(ModelVersion.id)).all()
    rows = db.execute(
        select(ModelItemMetric, Consumable.name, Consumable.sku, Consumable.unit)
        .join(Consumable, Consumable.id == ModelItemMetric.consumable_id)
        .where(ModelItemMetric.model_version_id == mv.id).order_by(Consumable.name)
    ).all()
    items = [ItemMetricOut(consumable_id=m.consumable_id, name=n, sku=s, unit=u, actual_total=round(m.actual_total, 2),
                           predicted_total=round(m.predicted_total, 2), mae=round(m.mae, 3), rmse=round(m.rmse, 3),
                           wape=m.wape, bias=m.bias, n_points=m.n_points) for m, n, s, u in rows]
    return ModelDetail(model=mv, run_candidates=run, items=items)


@router.post("/train", response_model=TrainResult)
def train(request: Request, db: DB, user: Trainer):
    try:
        result = run_training(db, user.hospital_id, user)
    except InsufficientDataError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    prune_old_forecasts(db, user.hospital_id)
    alerts.evaluate(db, user.hospital_id)  # V3: new forecast ⇒ recompute stockout risk + risk alerts
    audit.record(db, user, "forecast.train", "model_version", None,
                 {"run_id": result["run_id"], "active_model": result["active_model"],
                  "candidates": {k: {"wape": v["wape"], "mae": v["mae"]} for k, v in result["candidates"].items()}},
                 client_ip(request))
    db.commit()
    return result


# ---------------------------------------------------------------- item forecast


def _explain(item: Consumable, pred: float, days: int, rate: float, drivers: list[Driver], m: ModelItemMetric | None,
             mv: ModelVersion, usable: int, cover: float | None, impact: ProcedureImpact | None = None,
             comparison: ModelComparison | None = None) -> list[str]:
    unit = item.unit
    out = [f"Expected demand over the next {days} days: about {pred:,.0f} {unit} (≈{pred / days:,.1f}/day)."]
    if rate > 0:
        change = pred / days / rate - 1
        direction = "higher" if change > 0 else "lower"
        out.append(f"That is {abs(change):.0%} {direction} than the last 30 days' average of {rate:,.1f} {unit}/day.")
    ups = [d for d in drivers if d.units > 0]
    downs = [d for d in drivers if d.units < 0]
    if ups:
        out.append("Pushing the forecast up: " + ", ".join(f"{d.label.lower()} (+{d.units:,.0f})" for d in ups[:2]) + ".")
    if downs:
        out.append("Pulling it down: " + ", ".join(f"{d.label.lower()} ({d.units:,.0f})" for d in downs[:2]) + ".")
    if mv.model_type not in ("xgboost", "xgboost_procedure"):
        out.append(f"The active model is a {mv.model_type.replace('_', ' ')} baseline — it beat XGBoost on the last holdout.")
    out.extend(_procedure_sentences(item, pred, impact, comparison, mv))
    if m and m.wape is not None:
        out.append(f"On the last {m.n_points} held-out days this model was off by {m.wape:.0%} (WAPE) for this item.")
    if cover is not None:
        out.append(f"Current usable stock ({usable:,} {unit}) covers about {cover:,.0f} days of forecast demand.")
    return out


def _procedure_sentences(item: Consumable, pred: float, impact: ProcedureImpact | None,
                         comparison: ModelComparison | None, mv: ModelVersion) -> list[str]:
    """V2B explanation from actual schedule, mapping and model data — no generated text."""
    if impact is None or comparison is None or not comparison.v2b_trained:
        return []
    unit, n, q = item.unit, impact.scheduled_procedures, impact.expected_quantity
    out: list[str] = []
    if mv.model_type == "xgboost_procedure":
        share = q / pred if pred > 0 else 0.0
        if n == 0:
            out.append("No relevant procedures are scheduled, so demand is based on recent consumption patterns.")
        elif share > 1:
            out.append(f"Demand is driven mainly by scheduled procedures: {n} relevant procedures would need about "
                       f"{q:,.0f} {unit} by the configured kits — more than the forecast, because recorded use per "
                       f"procedure has been below the kit quantity.")
        elif share >= 0.4:
            out.append(f"Demand is driven mainly by scheduled procedures: {n} relevant procedures need about "
                       f"{q:,.0f} {unit} by the configured kits ({share:.0%} of the forecast).")
        elif share >= 0.1:
            out.append(f"Demand is based on recent consumption patterns and {n} scheduled procedures "
                       f"(about {q:,.0f} {unit} by kit).")
        else:
            out.append(f"Demand is mainly based on recent consumption patterns because few relevant procedures are "
                       f"scheduled ({n}, about {q:,.0f} {unit}).")
        if impact.procedure_effect is not None and abs(impact.procedure_effect) >= 1:
            direction = "higher" if impact.procedure_effect > 0 else "lower"
            if n > 0:
                out.append(f"Using the procedure schedule makes this forecast {abs(impact.procedure_effect):,.0f} {unit} "
                           f"{direction} than the consumption-only model (V2A: {impact.v2a_forecast:,.0f}).")
            else:
                # No scheduled procedure uses this item: the gap is a difference between the two fitted models,
                # not an effect of the schedule (procedure features are zero for this item).
                out.append(f"The procedure-aware model's forecast is {abs(impact.procedure_effect):,.0f} {unit} {direction} "
                           f"than the consumption-only model (V2A: {impact.v2a_forecast:,.0f}). No scheduled procedure uses "
                           f"this item, so this gap comes from the two models fitting history differently, not from the schedule.")
        if comparison.falls_back_to_v2a_from:
            out.append(f"No schedule is recorded after {comparison.schedule_through}; from "
                       f"{comparison.falls_back_to_v2a_from} the consumption-only forecast is used.")
    elif n > 0:
        out.append(f"{n} relevant procedures are scheduled (about {q:,.0f} {unit} by kit), but the procedure-aware "
                   f"model did not beat V2A on the holdout, so this forecast does not adjust for the schedule.")
    if comparison.schedule_changed_since_training:
        out.append("The procedure schedule or kit mappings changed after the last training run — retrain to update the forecast.")
    return out


@router.get("/{item_id}/procedure-impact", response_model=ProcedureImpact)
def item_procedure_impact(item_id: int, user: Reader, db: DB, days: int = Query(14, ge=1, le=MAX_DAYS)):
    """V2B: scheduled procedures that use this item in the forecast window, their kit demand and the model effect."""
    item = get_owned(db, Consumable, item_id, user.hospital_id, "Item")
    mv = _require_model(db, user.hospital_id)
    run = _run_versions(db, mv)
    served = _item_daily(db, mv, item.id)
    proc = load_procedure_data(db, user.hospital_id, mv.data_end, MAX_DAYS)
    return _procedure_impact(db, mv, run, proc, item, days, float(served[:days].sum()) if served is not None else 0.0)


@router.get("/{item_id}", response_model=ItemForecast)
def item_forecast(item_id: int, user: Reader, db: DB, days: int = Query(14, ge=1, le=MAX_DAYS)):
    item = get_owned(db, Consumable, item_id, user.hospital_id, "Item")
    mv = _require_model(db, user.hospital_id)
    fc = db.scalars(select(Forecast).where(Forecast.model_version_id == mv.id, Forecast.consumable_id == item.id)
                    .order_by(Forecast.horizon)).all()
    if not fc:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No forecast for this item in the active model (it may be new or inactive). Retrain to include it.")
    m = db.scalar(select(ModelItemMetric).where(ModelItemMetric.model_version_id == mv.id,
                                                ModelItemMetric.consumable_id == item.id))
    preds = np.array([f.predicted for f in fc])

    horizons = []
    for h in sorted({7, 14, 30, days}):
        p = float(preds[:h].sum())
        lo, hi = _interval(p, m, h)
        horizons.append(HorizonTotal(days=h, predicted=round(p, 1), lower=round(lo, 1), upper=round(hi, 1)))
    daily_out = []
    for f in fc:
        lo, hi = _interval(f.predicted, m, 1)
        daily_out.append(DailyForecast(date=f.forecast_date, predicted=round(f.predicted, 2), lower=round(lo, 2),
                                       upper=round(hi, 2)))

    # history: last 60 days up to the model's data_end, with censored flags
    panel = load_panel(db, user.hospital_id, mv.data_end, consumable_id=item.id)
    history: list[HistoryPoint] = []
    rate = 0.0
    if not panel.item_actual.empty and item.id in panel.item_actual.columns:
        s = panel.item_actual[item.id]
        for d, v in s.iloc[-60:].items():
            cens = bool(np.isnan(v))  # stocked-out day: recorded use is not true demand
            history.append(HistoryPoint(date=d.date(), actual=0.0 if cens else round(float(v), 2), censored=cens))
        known = s.iloc[-30:].dropna()
        rate = float(known.mean()) if len(known) else 0.0

    # drivers for the closest stored horizon ≥ days
    drivers: list[Driver] = []
    base = None
    dh = None
    if m and m.drivers:
        dh = min((int(k) for k in m.drivers if int(k) >= days), default=max(int(k) for k in m.drivers))
        raw_d = m.drivers[str(dh)]
        base = raw_d.get("base")
        drivers = sorted(
            (Driver(feature=k, label=FEATURE_LABELS.get(k, k), units=round(v, 1)) for k, v in raw_d.items() if k != "base"),
            key=lambda d: -abs(d.units),
        )

    total = horizons[[h.days for h in horizons].index(days)]
    inv = stock.inventory_rows(db, user.hospital_id, [item.id])[0]
    per_day_30 = float(preds[:30].mean()) if len(preds) else 0.0
    cover = round(inv["usable_stock"] / per_day_30, 1) if per_day_30 > 0 else None

    # ---- V2B: procedure impact, V2A vs V2B, stock cover against the served forecast
    run = _run_versions(db, mv)
    proc = load_procedure_data(db, user.hospital_id, mv.data_end, MAX_DAYS)
    comparison = _comparison(mv, run, proc)
    impact = _procedure_impact(db, mv, run, proc, item, days, total.predicted)
    comparison_daily: list[DailyForecast] = []
    if mv.model_type == "xgboost_procedure" and run.get("xgboost"):
        v2a_d = _item_daily(db, run["xgboost"], item.id)
        if v2a_d is not None:
            comparison_daily = [DailyForecast(date=f.forecast_date, predicted=round(float(v), 2), lower=round(float(v), 2),
                                              upper=round(float(v), 2)) for f, v in zip(fc, v2a_d, strict=False)]
    shortage = max(0, round(total.predicted - inv["usable_stock"]))

    return ItemForecast(
        item=item.name, item_id=item.id, sku=item.sku, unit=item.unit,
        horizon_days=days, predicted_demand=round(total.predicted), lower=round(total.lower), upper=round(total.upper),
        interval=INTERVAL_NOTE, model_version=mv.name, model_type=mv.model_type, trained_at=mv.trained_at,
        data_through=mv.data_end, is_stale=_is_stale(mv), horizons=horizons, daily=daily_out, history=history,
        drivers_horizon=dh, base_level=round(base, 1) if base is not None else None, drivers=drivers,
        explanation=_explain(item, total.predicted, days, rate, drivers, m, mv, inv["usable_stock"], cover, impact, comparison),
        backtest=Backtest(test_start=mv.test_start, test_end=mv.test_end, actual_total=round(m.actual_total, 1),
                          predicted_total=round(m.predicted_total, 1), mae=round(m.mae, 2), rmse=round(m.rmse, 2),
                          wape=m.wape, bias=m.bias, daily=m.daily or []) if m else None,
        usable_stock=inv["usable_stock"], avg_daily_last_30=round(rate, 2), days_of_cover=cover,
        forecast_source=_source(mv.model_type), scheduled_procedures=impact.scheduled_procedures,
        procedure_driven_demand=impact.expected_quantity, procedure_impact=impact, model_comparison=comparison,
        comparison_daily=comparison_daily, expected_shortage=shortage, stock_covers_horizon=shortage == 0,
        days_of_stock_remaining=_days_remaining(preds, inv["usable_stock"]),
    )
