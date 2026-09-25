"""V10 — pilot metrics for one period (baseline or pilot), computed on demand from existing MedFlow data.

Every metric is a dict:
    {key, label, section, value, unit, n, min_n, sufficient, formula, source, note}
`sufficient` is False when there is no value or the sample is below `min_n`; callers must then show
"Insufficient data" instead of the value. `unit` is one of: count, units, pct (0–1 fraction), money, days, hours.

Two clocks: ledger-based metrics (stock, stockouts, forecasts, warnings) use complete business days only — up to
yesterday; activity metrics (syncs, decisions, views, feedback, issues, users) run up to now.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import business_today
from app.db.base import utcnow
from app.models import (
    AuditLog,
    Consumable,
    ExternalRef,
    Forecast,
    IntegrationSource,
    ModelItemMetric,
    ModelVersion,
    Pilot,
    PilotDataSource,
    PilotFeedback,
    PilotIssue,
    ProcurementRecommendation,
    RecommendationView,
    ReconciliationIssue,
    StockoutPrediction,
    SupplierOrder,
    SyncRun,
)

WARN_LEVELS = ("MEDIUM", "HIGH")  # V3 levels that count as a warning
RISK_HORIZON = 14  # V3 label horizon (app.risk.features.HORIZON)

# Minimum samples before a value is shown (below this: "Insufficient data")
MIN = {"compared_items": 5, "forecast_points": 14, "warning_rows": 20, "decided_orders": 5, "decisions": 3,
       "records": 20, "ledger_days": 7}


# ---------------------------------------------------------------- periods


@dataclass(frozen=True)
class Period:
    kind: str  # baseline | pilot
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def ledger_end(self) -> date | None:
        """Last complete business day of the period (None: the period has not started a complete day yet)."""
        e = min(self.end, business_today() - timedelta(days=1))
        return e if e >= self.start else None

    @property
    def ledger_days(self) -> int:
        return 0 if self.ledger_end is None else (self.ledger_end - self.start).days + 1

    def utc_bounds(self) -> tuple[datetime, datetime]:
        """[start 00:00, end+1 00:00) in the business timezone, as UTC — for activity metrics (capped at now)."""
        tz = ZoneInfo(settings.TIMEZONE)
        a = datetime.combine(self.start, time(), tzinfo=tz).astimezone(UTC)
        b = datetime.combine(self.end + timedelta(days=1), time(), tzinfo=tz).astimezone(UTC)
        return a, min(b, utcnow())

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


def periods(p: Pilot) -> dict[str, Period]:
    return {"baseline": Period("baseline", p.baseline_start, p.baseline_end),
            "pilot": Period("pilot", p.pilot_start, p.pilot_end)}


def _local(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo(settings.TIMEZONE)).date()


def metric(key: str, label: str, section: str, value, unit: str, *, formula: str, source: str, n: int | None = None,
           min_n: int = 0, note: str | None = None) -> dict:
    ok = value is not None and (n is None or n >= min_n)
    if isinstance(value, float):
        value = round(value, 6)
    return {"key": key, "label": label, "section": section, "value": value, "unit": unit, "n": n, "min_n": min_n,
            "sufficient": ok, "formula": formula, "source": source, "note": note}


# ---------------------------------------------------------------- ledger (V3 reconstruction)


def _history(db: Session, hid: int, period: Period, extra_after: int = 0):
    """V3 daily item histories covering the period (+ `extra_after` days, capped at yesterday)."""
    from app.risk.history import load_history

    if period.ledger_end is None:
        return None
    end = min(period.end + timedelta(days=extra_after), business_today() - timedelta(days=1))
    return load_history(db, hid, data_end=end, lookback_days=(end - period.start).days + 2 + RISK_HORIZON)


def _has_ledger(frame) -> bool:
    return bool((frame["consumed"] != 0).any() or (frame["received"] != 0).any() or (frame["usable_end"] > 0).any())


def _open_order_days(db: Session, hid: int) -> dict[int, list[tuple[date, date | None]]]:
    """item → [(ordered, closed_or_None)] of non-cancelled-before-arrival supplier orders."""
    out: dict[int, list] = defaultdict(list)
    for o in db.scalars(select(SupplierOrder).where(SupplierOrder.hospital_id == hid)):
        closed = o.cancelled_date or o.completed_date
        out[o.consumable_id].append((o.ordered_date, closed))
    return out


def ledger_metrics(db: Session, hid: int, period: Period) -> tuple[list[dict], dict]:
    """Inventory position at period end, stockouts. Returns (metrics, context reused by other sections)."""
    hist = _history(db, hid, period)
    src = "V3 ledger reconstruction (app.risk.history.load_history)"
    days = period.ledger_days
    if hist is None or not hist.items:
        none = dict(n=days, min_n=MIN["ledger_days"], source=src, note="No complete day of this period yet")
        return ([metric("usable_stock", "Usable stock at period end", "inventory", None, "units", formula="Σ usable units", **none),
                 metric("stockout_days", "Stockout item-days", "stockouts", None, "count", formula="Σ item-days", **none)],
                {"hist": None})
    items = {c.id: c for c in db.scalars(select(Consumable).where(Consumable.hospital_id == hid))}
    start, end = np.datetime64(period.start), np.datetime64(period.ledger_end)
    orders = _open_order_days(db, hid)
    usable = expiring = value = expired = 0.0
    so_days = events = emergency = 0
    shortage = 0.0
    affected: set[int] = set()
    excluded = 0
    stockout_days_by_item: dict[int, set[date]] = {}
    event_days: list[tuple[int, date]] = []
    for cid, h in hist.items.items():
        f = h.frame
        if not _has_ledger(f):
            excluded += 1
            continue
        w = f[(f.index >= start) & (f.index <= end)]
        if w.empty:
            continue
        last = w.iloc[-1]
        usable += float(last["usable_end"])
        expiring += float(last["expiring_14"])
        value += float(last["usable_end"]) * float(items[cid].unit_cost if cid in items else 0)
        so = w["stockout"].astype(bool)
        so_set = {d.date() for d, v in so.items() if v}
        stockout_days_by_item[cid] = so_set
        so_days += len(so_set)
        if so_set:
            affected.add(cid)
            normal = w.loc[~so, "consumed"]
            if len(normal):
                shortage += float(normal.mean()) * len(so_set)
        prev = f["stockout"].astype(bool).shift(1, fill_value=False)
        starts = [d.date() for d, v in (so & ~prev.reindex(so.index)).items() if v]
        for d in starts:
            events += 1
            event_days.append((cid, d))
            if not any(o <= d and (c is None or c >= d) for o, c in orders.get(cid, [])):
                emergency += 1
    # expired units on hand at period end = ledger balance at end-of-day − usable (V9 rewind helper)
    from app.integrations.entities import balance_as_of

    tz = ZoneInfo(settings.TIMEZONE)
    eod = datetime.combine(period.ledger_end + timedelta(days=1), time(), tzinfo=tz).astimezone(UTC) - timedelta(seconds=1)
    for cid, h in hist.items.items():
        if cid in items and _has_ledger(h.frame):
            w = h.frame[h.frame.index <= end]
            if len(w):
                expired += max(0.0, balance_as_of(db, items[cid], eod) - float(w.iloc[-1]["usable_end"]))
    n_items = len(stockout_days_by_item)
    note_ex = f"{excluded} item(s) without any ledger movement excluded" if excluded else None
    common = dict(n=days, min_n=MIN["ledger_days"], source=src)
    out = [
        metric("usable_stock", "Usable stock at period end", "inventory", usable, "units",
               formula="Σ over items of usable (unexpired) units at the end of the last complete day", **common),
        metric("expired_stock", "Expired stock on hand at period end", "inventory", expired, "units",
               formula="Σ (ledger balance − usable units) at the end of the last complete day", **common),
        metric("expiring_stock", "Stock expiring within 14 days (at period end)", "inventory", expiring, "units",
               formula="Σ usable units whose batch expires within 14 days", **common),
        metric("inventory_value", "Usable inventory value at period end", "inventory", round(value, 2), "money",
               formula="Σ usable units × current unit cost", note="Valued at today's unit cost", **common),
        metric("stockout_days", "Stockout item-days", "stockouts", so_days, "count",
               formula="Σ over items of days with no usable stock (V3 definition: balance reached 0, or no usable "
                       "stock at the start of the day)", note=note_ex, **common),
        metric("stockout_events", "Stockout events", "stockouts", events, "count",
               formula="number of stockout episodes that started in the period", **common),
        metric("affected_items", "Items that stocked out", "stockouts", len(affected), "count",
               formula=f"distinct items with ≥ 1 stockout day (of {n_items} items with a ledger)", **common),
        metric("shortage_estimate", "Estimated unmet demand on stockout days", "stockouts", round(shortage, 1), "units",
               formula="Σ over items of stockout days × the item's mean daily consumption on its non-stockout days in "
                       "the period", note="Estimate — demand on stockout days is not observed", **common),
        metric("emergency_stockouts", "Stockout events with no order in the pipeline", "stockouts", emergency, "count",
               formula="stockout events where no supplier order for the item was open on the first stockout day",
               **common),
    ]
    return out, {"hist": hist, "stockout_days": stockout_days_by_item, "events": event_days}


# ---------------------------------------------------------------- inventory accuracy (V9 reconciliation)


def pilot_source_ids(db: Session, pilot: Pilot) -> list[int]:
    return list(db.scalars(select(PilotDataSource.source_id).where(PilotDataSource.pilot_id == pilot.id)))


def accuracy_metrics(db: Session, pilot: Pilot, period: Period) -> list[dict]:
    src_ids = pilot_source_ids(db, pilot)
    src = "V9 inventory snapshots (external_refs) and reconciliation_issues of the pilot's data sources"
    compared: set[int] = set()
    if src_ids:
        refs = db.execute(select(ExternalRef.external_id, ExternalRef.target_type, ExternalRef.target_id)
                          .where(ExternalRef.source_id.in_(src_ids), ExternalRef.entity == "inventory")).all()
        issue_item = dict(db.execute(select(ReconciliationIssue.id, ReconciliationIssue.consumable_id)
                                     .where(ReconciliationIssue.source_id.in_(src_ids))).all())
        for ext, ttype, tid in refs:
            try:
                d = date.fromisoformat(ext.rsplit("@", 1)[1])
            except (IndexError, ValueError):
                continue
            if not period.contains(d) or ttype == "stock_movement":  # opening balances are not comparisons
                continue
            item = tid if ttype == "consumable" else issue_item.get(tid)
            if item is not None:
                compared.add(item)
    a, b = period.utc_bounds()
    discrepant: set[int] = set()
    abs_units = 0
    n_issues = 0
    if src_ids:
        for i in db.scalars(select(ReconciliationIssue).where(ReconciliationIssue.source_id.in_(src_ids),
                                                             ReconciliationIssue.created_at < b)):
            ra = i.resolved_at.replace(tzinfo=UTC) if i.resolved_at and i.resolved_at.tzinfo is None else i.resolved_at
            if ra is not None and ra < a:
                continue
            n_issues += 1
            abs_units += abs(i.difference)
            discrepant.add(i.consumable_id)
    matched = compared - discrepant
    acc = (len(matched) / len(compared)) if compared else None
    note = None if src_ids else "No V9 data source is linked to this pilot"
    return [
        metric("inventory_accuracy", "Inventory accuracy (hospital count = MedFlow ledger)", "inventory", acc, "pct",
               n=len(compared), min_n=MIN["compared_items"], source=src, note=note,
               formula="items whose count matched MedFlow (no reconciliation issue open during the period) ÷ items "
                       f"counted by the hospital system in the period = {len(matched)} ÷ {len(compared)}"),
        metric("stock_discrepancies", "Stock discrepancies (reconciliation issues open in the period)", "inventory",
               n_issues if src_ids else None, "count", source=src, note=note,
               formula="reconciliation issues of the pilot's sources open at any time during the period"),
        metric("discrepancy_units", "Absolute units in discrepancy", "inventory", abs_units if src_ids else None, "units",
               source=src, note=note, formula="Σ |hospital count − MedFlow| over those issues"),
    ]


# ---------------------------------------------------------------- forecasting (V2 evaluation logic)


def _served_versions(db: Session, hid: int) -> list[ModelVersion]:
    return list(db.scalars(select(ModelVersion).where(ModelVersion.hospital_id == hid,
                                                      ModelVersion.notes.like("Selected%"))
                           .order_by(ModelVersion.data_end, ModelVersion.id)))


def forecast_metrics_for(db: Session, hid: int, period: Period) -> list[dict]:
    from app.ml.data import load_panel
    from app.ml.metrics import forecast_metrics

    served = _served_versions(db, hid)
    models = [{"name": m.name, "type": m.model_type, "trained_at": m.trained_at.isoformat(), "data_end": m.data_end.isoformat(),
               "holdout_wape": m.metrics.get("wape")} for m in served]
    out: list[dict] = []
    le = period.ledger_end
    live_a: list[float] = []
    live_p: list[float] = []
    coverage = None
    hold_a: list[float] = []
    hold_p: list[float] = []
    if le is not None and served:
        panel = load_panel(db, hid, data_end=le)
        actual = panel.item_actual
        # live forecasts: for each (item, day) the forecast of the most recent served model trained before that day
        rows = db.execute(select(Forecast.model_version_id, Forecast.consumable_id, Forecast.forecast_date, Forecast.predicted)
                          .where(Forecast.hospital_id == hid, Forecast.model_version_id.in_([m.id for m in served]),
                                 Forecast.forecast_date >= period.start, Forecast.forecast_date <= le)).all()
        rank = {m.id: i for i, m in enumerate(served)}
        best: dict[tuple[int, date], tuple[int, float]] = {}
        for mv, cid, d, pred in rows:
            k = (cid, d)
            if k not in best or rank[mv] > best[k][0]:
                best[k] = (rank[mv], pred)
        n_possible = 0
        for d in [period.start + timedelta(days=i) for i in range(period.ledger_days)]:
            ts = np.datetime64(d)
            for cid in actual.columns:
                n_possible += 1
                if (cid, d) in best and ts in actual.index:
                    v = actual.loc[ts, cid]
                    if np.isfinite(v):
                        live_a.append(float(v))
                        live_p.append(best[(cid, d)][1])
        coverage = (len(best) / n_possible) if n_possible else None
        # holdout backtests (out-of-sample predictions stored with each served model version)
        for mim in db.scalars(select(ModelItemMetric).where(ModelItemMetric.model_version_id.in_([m.id for m in served]))):
            for pt in mim.daily or []:
                d = date.fromisoformat(pt["date"])
                if period.start <= d <= le and pt.get("actual") is not None:
                    hold_a.append(float(pt["actual"]))
                    hold_p.append(float(pt["predicted"]))
    live = forecast_metrics(live_a, live_p)
    hold = forecast_metrics(hold_a, hold_p)
    fsrc = "V2 evaluation (app.ml.metrics.forecast_metrics) on stored forecasts of the served model vs censored actuals"
    hsrc = "V2 holdout backtests stored with each served model version (model_item_metrics)"
    n_live, n_hold = live["n_points"], hold["n_points"]
    mn = MIN["forecast_points"]
    note = None if served else "No trained forecasting model"
    out += [
        metric("forecast_wape", "Forecast WAPE (live forecasts)", "forecasting", live["wape"], "pct", n=n_live, min_n=mn,
               source=fsrc, formula="Σ|forecast − actual| ÷ Σ actual over item-days (stock-out days excluded)", note=note),
        metric("forecast_mae", "Forecast MAE (live)", "forecasting", live["mae"], "units", n=n_live, min_n=mn, source=fsrc,
               formula="mean |forecast − actual| per item-day"),
        metric("forecast_rmse", "Forecast RMSE (live)", "forecasting", live["rmse"], "units", n=n_live, min_n=mn,
               source=fsrc, formula="√ mean (forecast − actual)² per item-day"),
        metric("forecast_bias", "Forecast bias (live)", "forecasting", live["bias"], "pct", n=n_live, min_n=mn, source=fsrc,
               formula="Σ(forecast − actual) ÷ Σ actual; positive = over-forecast"),
        metric("forecast_coverage", "Forecast coverage", "forecasting", coverage, "pct", n=period.ledger_days,
               min_n=1, source=fsrc, formula="item-days with a stored forecast ÷ active item-days in the period"),
        metric("holdout_wape", "Forecast WAPE (holdout backtest)", "forecasting", hold["wape"], "pct", n=n_hold, min_n=mn,
               source=hsrc, formula="the same WAPE on the out-of-sample backtest days that fall in the period"),
        metric("holdout_bias", "Forecast bias (holdout backtest)", "forecasting", hold["bias"], "pct", n=n_hold, min_n=mn,
               source=hsrc, formula="Σ(forecast − actual) ÷ Σ actual on backtest days"),
    ]
    out[0]["models"] = models
    return out


# ---------------------------------------------------------------- stockout warnings (V3.4 evaluation logic)


def warning_metrics(db: Session, hid: int, period: Period, ledger_ctx: dict) -> list[dict]:
    from app.risk.metrics import confusion, prf

    src = "V3 risk snapshots (stockout_predictions) vs V3 ledger stockouts; app.risk.metrics.confusion / prf"
    yesterday = business_today() - timedelta(days=1)
    snaps = db.execute(select(StockoutPrediction.consumable_id, StockoutPrediction.as_of, StockoutPrediction.risk_level,
                              StockoutPrediction.created_at)
                       .where(StockoutPrediction.hospital_id == hid, StockoutPrediction.as_of >= period.start,
                              StockoutPrediction.as_of <= period.end)).all()
    latest: dict[tuple[int, date], tuple[datetime, str]] = {}
    for cid, as_of, level, created in snaps:
        k = (cid, as_of)
        if k not in latest or created > latest[k][0]:
            latest[k] = (created, level)
    hist = _history(db, hid, period, extra_after=RISK_HORIZON)
    so_by_item: dict[int, set[date]] = {}
    if hist is not None:
        for cid, h in hist.items.items():
            if _has_ledger(h.frame):
                so_by_item[cid] = {d.date() for d, v in h.frame["stockout"].astype(bool).items() if v}
    y, flag, pending = [], [], 0
    for (cid, as_of), (_c, level) in latest.items():
        if as_of + timedelta(days=RISK_HORIZON) > yesterday:
            pending += 1  # outcome window not complete yet
            continue
        window = {as_of + timedelta(days=i) for i in range(1, RISK_HORIZON + 1)}
        y.append(bool(so_by_item.get(cid, set()) & window))
        flag.append(level in WARN_LEVELS)
    c = confusion(np.array(y, dtype=bool), np.array(flag, dtype=bool)) if y else {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    scores = prf(c) if y else {"precision": None, "recall": None}
    # event level: warned on some day in [e−14, e−1]?
    warned: dict[int, list[date]] = defaultdict(list)
    for (cid, as_of), (_c, level) in latest.items():
        if level in WARN_LEVELS:
            warned[cid].append(as_of)
    # only events V3 was watching: at least one snapshot of that item in the 14 days before the stockout
    snapped: dict[int, set[date]] = defaultdict(set)
    for cid, as_of in latest:
        snapped[cid].add(as_of)
    events = [(cid, e) for cid, e in ledger_ctx.get("events", [])
              if any(e - timedelta(days=RISK_HORIZON) <= d <= e - timedelta(days=1) for d in snapped.get(cid, ()))]
    uncovered = len(ledger_ctx.get("events", [])) - len(events)
    caught, leads = 0, []
    for cid, e in events:
        prior = [d for d in warned.get(cid, []) if e - timedelta(days=RISK_HORIZON) <= d <= e - timedelta(days=1)]
        if prior:
            caught += 1
            leads.append((e - min(prior)).days)
    n = len(y)
    mn = MIN["warning_rows"]
    note = (f"{pending} snapshot(s) excluded: their 14-day outcome window is not complete yet" if pending else None)
    if not latest:
        note = "No V3 risk snapshots were taken during this period"
    return [
        metric("warning_precision", "Stockout warning precision", "stockout_prediction", scores.get("precision"), "pct",
               n=n, min_n=mn, source=src, note=note,
               formula=f"warnings (MEDIUM/HIGH) followed by a stockout within 14 days ÷ all warnings = {c['tp']} ÷ {c['tp'] + c['fp']}"),
        metric("warning_recall", "Stockout warning recall", "stockout_prediction", scores.get("recall"), "pct", n=n, min_n=mn,
               source=src,
               formula=f"item-days followed by a stockout that were warned ÷ all such item-days = {c['tp']} ÷ {c['tp'] + c['fn']}"),
        metric("warning_true_positives", "True positives", "stockout_prediction", c["tp"], "count", n=n, min_n=mn, source=src,
               formula="warned item-days followed by a stockout within 14 days"),
        metric("warning_false_positives", "False positives", "stockout_prediction", c["fp"], "count", n=n, min_n=mn,
               source=src, formula="warned item-days with no stockout within 14 days"),
        metric("warning_false_negatives", "False negatives", "stockout_prediction", c["fn"], "count", n=n, min_n=mn,
               source=src, formula="unwarned item-days followed by a stockout within 14 days"),
        metric("events_warned", "Stockout events warned in advance", "stockout_prediction",
               (caught / len(events)) if events else None, "pct", n=len(events), min_n=1, source=src,
               note=f"{uncovered} stockout event(s) had no V3 snapshot in the 14 days before and are not counted" if uncovered else None,
               formula=f"events with a warning 1–14 days before ÷ events V3 was monitoring = {caught} ÷ {len(events)}"),
        metric("warning_lead_days", "Median warning lead time", "stockout_prediction",
               float(np.median(leads)) if leads else None, "days", n=len(leads), min_n=1, source=src,
               formula="median over warned events of (first stockout day − first warning day)"),
    ]


# ---------------------------------------------------------------- suppliers (V4)


def supplier_metrics(db: Session, hid: int, period: Period) -> list[dict]:
    from app.supplier_intel.metrics import summarize
    from app.supplier_intel.service import _rec

    orders = [_rec(o) for o in db.scalars(select(SupplierOrder).where(
        SupplierOrder.hospital_id == hid, SupplierOrder.ordered_date >= period.start, SupplierOrder.ordered_date <= period.end))]
    today = min(period.end, business_today())
    s = summarize(orders, today) if orders else {}
    n = s.get("decided", 0)
    mn = MIN["decided_orders"]
    src = "V4 supplier metrics (app.supplier_intel.metrics.summarize) over orders placed in the period"
    return [
        metric("supplier_orders", "Supplier orders placed", "supplier", len(orders), "count", source=src,
               formula="supplier orders with an order date in the period"),
        metric("otif_rate", "On time in full (OTIF)", "supplier", s.get("otif_rate"), "pct", n=n, min_n=mn, source=src,
               formula=f"orders delivered in full by the expected date ÷ decided orders = {s.get('otif_successes', 0)} ÷ {n}"),
        metric("on_time_rate", "On-time first delivery", "supplier", s.get("on_time_rate"), "pct", n=n, min_n=mn, source=src,
               formula="first delivery on or before the expected date ÷ orders with a known outcome"),
        metric("avg_days_late", "Average delay of late deliveries", "supplier", s.get("avg_days_late"), "days", n=n, min_n=mn,
               source=src, formula="mean (first delivery − expected date) over late deliveries"),
        metric("fill_rate", "Quantity fill rate", "supplier", s.get("fill_rate"), "pct", n=n, min_n=mn, source=src,
               formula="Σ received ÷ Σ ordered over received orders"),
        metric("cancellation_rate", "Cancellation rate", "supplier", s.get("cancellation_rate"), "pct", n=n, min_n=mn,
               source=src, formula="cancelled ÷ closed orders"),
        metric("price_changes", "Price changes > 2 %", "supplier", s.get("price_changes"), "count", n=s.get("price_pairs", 0),
               min_n=1, source=src, formula="consecutive orders of the same supplier and item whose price moved by more than 2 %"),
    ]


# ---------------------------------------------------------------- procurement decisions (V5, unchanged)


def decision_rows(db: Session, hid: int, period: Period) -> list[dict]:
    """The decision funnel per recommendation generated in the period."""
    a, b = period.utc_bounds()
    today = business_today()
    recs = list(db.scalars(select(ProcurementRecommendation).where(
        ProcurementRecommendation.hospital_id == hid, ProcurementRecommendation.created_at >= a,
        ProcurementRecommendation.created_at < b).order_by(ProcurementRecommendation.created_at)))
    views: dict[int, list] = defaultdict(list)
    if recs:
        for rid, uid, at in db.execute(select(RecommendationView.recommendation_id, RecommendationView.user_id,
                                              RecommendationView.viewed_at)
                                       .where(RecommendationView.recommendation_id.in_([r.id for r in recs]))):
            views[rid].append((uid, at))
    rows = []
    for r in recs:
        if r.status == "APPROVED":
            outcome = "modified" if r.modified else "approved"
        elif r.status == "REJECTED":
            outcome = "rejected"
        elif r.status == "SUPERSEDED" or r.as_of < today:
            outcome = "expired"
        else:
            outcome = "pending"
        created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)
        decided = r.decided_at if (r.decided_at is None or r.decided_at.tzinfo) else r.decided_at.replace(tzinfo=UTC)
        v = sorted(views.get(r.id, []), key=lambda x: x[1])
        rows.append({
            "id": r.id, "item": r.consumable.name, "sku": r.consumable.sku, "created_at": created, "as_of": r.as_of,
            "risk_level": r.risk_level, "quantity": r.quantity, "lines": r.lines, "has_order": bool(r.lines),
            "viewed": bool(v), "views": len(v), "first_viewed_at": v[0][1] if v else None, "outcome": outcome,
            "final_lines": r.final_lines, "decided_by_id": r.decided_by_id, "decided_at": decided,
            "reason": r.decision_reason,
            "hours_to_decision": round((decided - created).total_seconds() / 3600, 2) if decided else None,
        })
    return rows


def procurement_metrics(db: Session, hid: int, period: Period, ledger_ctx: dict) -> list[dict]:
    rows = decision_rows(db, hid, period)
    src = "V5 procurement_recommendations (status, decision, modification) + V10 recommendation views"
    cnt = defaultdict(int)
    for r in rows:
        cnt[r["outcome"]] += 1
    decided = cnt["approved"] + cnt["modified"] + cnt["rejected"]
    hrs = [r["hours_to_decision"] for r in rows if r["hours_to_decision"] is not None]
    mn = MIN["decisions"]
    # emergency purchases: orders not from a recommendation, placed on/just after a stockout day of the item
    so = ledger_ctx.get("stockout_days") or {}
    emergency = None
    if ledger_ctx.get("hist") is not None:
        emergency = 0
        for o in db.scalars(select(SupplierOrder).where(SupplierOrder.hospital_id == hid, SupplierOrder.is_synthetic.is_(False),
                                                        SupplierOrder.ordered_date >= period.start,
                                                        SupplierOrder.ordered_date <= period.end)):
            days = so.get(o.consumable_id, set())
            if o.recommendation_id is None and (o.ordered_date in days or o.ordered_date - timedelta(days=1) in days):
                emergency += 1
    rate = lambda k: (k / decided) if decided else None  # noqa: E731
    return [
        metric("recs_generated", "Recommendations generated", "procurement", len(rows), "count", source=src,
               formula="V5 recommendations created in the period"),
        metric("recs_viewed", "Recommendations viewed", "procurement", sum(r["viewed"] for r in rows), "count", source=src,
               formula="recommendations opened by at least one person"),
        metric("recs_approved", "Approved as recommended", "procurement", cnt["approved"], "count", source=src,
               formula="status APPROVED, not modified"),
        metric("recs_modified", "Approved with modifications", "procurement", cnt["modified"], "count", source=src,
               formula="status APPROVED, modified (quantity / supplier changed by the approver)"),
        metric("recs_rejected", "Rejected", "procurement", cnt["rejected"], "count", source=src, formula="status REJECTED"),
        metric("recs_expired", "Expired without a decision", "procurement", cnt["expired"], "count", source=src,
               formula="superseded by a newer recommendation, or from an earlier day and never decided"),
        metric("approval_rate", "Approval rate (as recommended)", "procurement", rate(cnt["approved"]), "pct", n=decided,
               min_n=mn, source=src, formula=f"approved unchanged ÷ decided = {cnt['approved']} ÷ {decided}"),
        metric("modification_rate", "Modification rate", "procurement", rate(cnt["modified"]), "pct", n=decided, min_n=mn,
               source=src, formula=f"approved with changes ÷ decided = {cnt['modified']} ÷ {decided}"),
        metric("rejection_rate", "Rejection rate", "procurement", rate(cnt["rejected"]), "pct", n=decided, min_n=mn,
               source=src, formula=f"rejected ÷ decided = {cnt['rejected']} ÷ {decided}"),
        metric("hours_to_decision", "Median time from recommendation to decision", "procurement",
               float(np.median(hrs)) if hrs else None, "hours", n=len(hrs), min_n=mn, source=src,
               formula="median (decided_at − created_at)"),
        metric("emergency_purchases", "Emergency purchases", "procurement", emergency, "count", source=src,
               note=None if emergency is not None else "No complete day of this period yet",
               formula="supplier orders not created from a recommendation, placed on a stockout day of the item or the "
                       "day after (synthetic seed history excluded)"),
    ]


# ---------------------------------------------------------------- adoption


def adoption_metrics(db: Session, pilot: Pilot, period: Period) -> list[dict]:
    from app.models import PilotParticipant

    hid = pilot.hospital_id
    a, b = period.utc_bounds()
    src = "audit_logs, recommendation_views, procurement_recommendations, pilot_feedback"
    active = db.scalar(select(func.count(func.distinct(AuditLog.user_id))).where(
        AuditLog.hospital_id == hid, AuditLog.user_id.is_not(None), AuditLog.created_at >= a, AuditLog.created_at < b)) or 0
    views = db.scalar(select(func.count(RecommendationView.id)).where(
        RecommendationView.hospital_id == hid, RecommendationView.viewed_at >= a, RecommendationView.viewed_at < b)) or 0
    decisions = db.scalar(select(func.count(ProcurementRecommendation.id)).where(
        ProcurementRecommendation.hospital_id == hid, ProcurementRecommendation.decided_at >= a,
        ProcurementRecommendation.decided_at < b)) or 0
    fb = db.execute(select(PilotFeedback.rating, func.count(PilotFeedback.id)).where(
        PilotFeedback.pilot_id == pilot.id, PilotFeedback.created_at >= a, PilotFeedback.created_at < b)
        .group_by(PilotFeedback.rating)).all()
    fbc = dict(fb)
    n_fb = sum(fbc.values())
    asks = db.scalar(select(func.count(AuditLog.id)).where(AuditLog.hospital_id == hid, AuditLog.action == "assistant.ask",
                                                           AuditLog.created_at >= a, AuditLog.created_at < b)) or 0
    participants = db.scalar(select(func.count(PilotParticipant.id)).where(PilotParticipant.pilot_id == pilot.id,
                                                                           PilotParticipant.user_id.is_not(None))) or 0
    return [
        metric("active_users", "Active users", "adoption", active, "count", source=src,
               formula=f"distinct people with any recorded action in the hospital in the period ({participants} named participants)"),
        metric("recommendation_views", "Recommendation views", "adoption", views, "count", source=src,
               formula="times a procurement recommendation was opened"),
        metric("decisions_made", "Procurement decisions made", "adoption", decisions, "count", source=src,
               formula="recommendations approved, modified or rejected in the period"),
        metric("assistant_questions", "AI assistant questions", "adoption", asks, "count", source=src,
               formula="assistant.ask audit events"),
        metric("feedback_submitted", "Feedback submitted", "adoption", n_fb, "count", source=src,
               formula="pilot feedback entries in the period"),
        metric("feedback_useful_share", "Feedback rated useful", "adoption", (fbc.get("useful", 0) / n_fb) if n_fb else None,
               "pct", n=n_fb, min_n=5, source=src,
               formula=f"'useful' ÷ all feedback = {fbc.get('useful', 0)} ÷ {n_fb} "
                       f"(somewhat useful {fbc.get('somewhat_useful', 0)}, not useful {fbc.get('not_useful', 0)})"),
    ]


# ---------------------------------------------------------------- data quality + integration reliability (V9)

REASON_LABELS = [("duplicate_record", "Duplicate records"), ("missing_field", "Missing required fields"),
                 ("unknown_item", "Unknown item codes"), ("unknown_supplier", "Unknown supplier codes"),
                 ("unknown_department", "Unknown department codes"), ("invalid_date", "Invalid dates"),
                 ("negative_quantity", "Negative quantities")]


def data_quality(db: Session, pilot: Pilot, period: Period) -> dict:
    from app.integrations.sources import freshness

    src_ids = pilot_source_ids(db, pilot)
    a, b = period.utc_bounds()
    runs = list(db.scalars(select(SyncRun).where(SyncRun.source_id.in_(src_ids), SyncRun.mode != "dry_run",
                                                 SyncRun.started_at >= a, SyncRun.started_at < b)
                           .order_by(SyncRun.started_at))) if src_ids else []
    first = [r for r in runs if r.mode != "retry"]
    received = sum(r.records_received for r in first)
    rejected_first = sum(r.records_rejected for r in first)
    accepted_first = received - rejected_first
    recovered = sum(r.records_created + r.records_updated + r.records_unchanged for r in runs if r.mode == "retry")
    reasons: dict[str, int] = defaultdict(int)
    for r in runs:
        for k, v in ((r.error_summary or {}).get("reasons") or {}).items():
            reasons[k] += v
    recon = next(m for m in accuracy_metrics(db, pilot, period) if m["key"] == "stock_discrepancies")["value"]
    pct = (accepted_first / received) if received else None
    sources = []
    for s in db.scalars(select(IntegrationSource).where(IntegrationSource.id.in_(src_ids))) if src_ids else []:
        sr = [r for r in runs if r.source_id == s.id]
        ok = [r for r in sr if r.status in ("SUCCESS", "PARTIAL")]
        failed = [r for r in sr if r.status == "FAILED"]
        durs = [(r.completed_at - r.started_at).total_seconds() for r in sr if r.completed_at is not None]
        active_days = len({_local(r.started_at) for r in sr})
        sources.append({
            "id": s.id, "name": s.name, "connector": s.connector, "system_type": s.system_type, "is_simulated": s.is_simulated,
            "enabled": s.enabled, "runs": len(sr), "successful_runs": len(ok), "failed_runs": len(failed),
            "last_success_at": max((r.completed_at or r.started_at for r in ok), default=None),
            "last_failure_at": max((r.completed_at or r.started_at for r in failed), default=None),
            "records_processed": sum(r.records_received for r in sr), "records_rejected": sum(r.records_rejected for r in sr),
            "avg_duration_s": round(float(np.mean(durs)), 2) if durs else None,
            "runs_per_active_day": round(len(sr) / active_days, 2) if active_days else None,
            "schedule_minutes": (s.config or {}).get("schedule_minutes"), "freshness": freshness(s),
        })
    note = None if src_ids else "No V9 data source is linked to this pilot"
    m = [
        metric("records_received", "Records received", "data_quality", received if src_ids else None, "count",
               source="V9 sync_runs of the pilot's sources", note=note,
               formula="Σ records received by non-retry runs (dry runs excluded)"),
        metric("records_accepted", "Records accepted (first pass)", "data_quality", accepted_first if src_ids else None,
               "count", source="V9 sync_runs", formula="received − rejected, first attempt"),
        metric("records_rejected", "Records rejected (first pass)", "data_quality", rejected_first if src_ids else None,
               "count", source="V9 sync_runs", formula="Σ rejected by non-retry runs"),
        metric("records_recovered", "Rejected records applied on retry", "data_quality", recovered if src_ids else None,
               "count", source="V9 retry runs", formula="Σ created + updated + unchanged of retry runs"),
        metric("data_quality_pct", "Data-quality score", "data_quality", pct, "pct", n=received, min_n=MIN["records"],
               source="V9 sync_runs", note=note,
               formula=f"records accepted on first attempt ÷ records received = {accepted_first} ÷ {received}"
                       " (a plain acceptance rate — no weights)"),
    ]
    for code, label in REASON_LABELS:
        extra = reasons.get("invalid_department", 0) if code == "unknown_department" else 0
        m.append(metric(f"dq_{code}", label, "data_quality", (reasons.get(code, 0) + extra) if src_ids else None, "count",
                        source="V9 rejection reasons (sync_runs.error_summary)",
                        formula=f"records rejected with reason '{code}'" + (" or 'invalid_department'" if extra else "")))
    m.append(metric("dq_reconciliation", "Reconciliation discrepancies", "data_quality", recon, "count",
                    source="V9 reconciliation_issues", formula="stock-count differences open during the period"))
    stale = [s["name"] for s in sources if s["freshness"]["stale"]]
    m.append(metric("dq_stale_sources", "Sources with stale data (now)", "data_quality", len(stale) if src_ids else None,
                    "count", source="V9 freshness", note=", ".join(stale) or None,
                    formula="linked sources whose last successful sync is older than 3× their schedule (24 h if none)"))
    return {"metrics": m, "sources": sources, "reasons": dict(reasons),
            "runs": [{"id": r.id, "source_id": r.source_id, "entity": r.entity, "status": r.status, "started_at": r.started_at,
                      "received": r.records_received, "rejected": r.records_rejected} for r in runs[-50:]]}


# ---------------------------------------------------------------- issues


def issue_counts(db: Session, pilot: Pilot) -> dict:
    rows = db.execute(select(PilotIssue.status, func.count(PilotIssue.id)).where(PilotIssue.pilot_id == pilot.id)
                      .group_by(PilotIssue.status)).all()
    c = dict(rows)
    return {k: int(c.get(k, 0)) for k in ("open", "investigating", "resolved", "ignored")}


# ---------------------------------------------------------------- all sections


def compute(db: Session, pilot: Pilot, period: Period) -> dict:
    hid = pilot.hospital_id
    ledger, ctx = ledger_metrics(db, hid, period)
    dq = data_quality(db, pilot, period)
    metrics = (accuracy_metrics(db, pilot, period) + ledger + forecast_metrics_for(db, hid, period)
               + warning_metrics(db, hid, period, ctx) + supplier_metrics(db, hid, period)
               + procurement_metrics(db, hid, period, ctx) + adoption_metrics(db, pilot, period) + dq["metrics"])
    return {"period": {"kind": period.kind, "start": period.start, "end": period.end, "days": period.days,
                       "ledger_end": period.ledger_end, "ledger_days": period.ledger_days,
                       "partial": period.end >= business_today()},
            "metrics": metrics, "integration_sources": dq["sources"], "rejection_reasons": dq["reasons"],
            "recent_runs": dq["runs"]}
