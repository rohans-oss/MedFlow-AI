"""V4 — lead-time and delay prediction from order history, with a temporal backtest.

Predictors are deliberately simple and transparent (empirical quantiles and smoothed rates):
    lead time   quoted (catalogue)  ·  supplier history  ·  supplier × item history (falls back to the supplier)
    late?       hospital-wide late rate  ·  supplier late rate  ·  supplier × item late rate (each shrunk towards
                the level above it with M = 5 pseudo-orders)

Backtest (expanding window): for every delivered order after a warm-up period, predict using ONLY orders whose
outcome was already known on its order date (first delivery before that date) — no look-ahead.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from app.risk.metrics import roc_auc
from app.supplier_intel.metrics import PRIOR_STRENGTH, OrderRec, shrink

MIN_HISTORY = 3  # orders needed before a history-based prediction is used


@dataclass
class LeadTimePrediction:
    median: float
    p90: float
    basis: str  # "item" | "supplier" | "quoted"
    n: int


def _known(orders: list[OrderRec], on: date) -> list[OrderRec]:
    return [o for o in orders if o.first_delivery is not None and o.first_delivery < on]


def predict_lead_time(history: list[OrderRec], supplier_id: int, item_id: int, quoted: int) -> LeadTimePrediction:
    item = [o.lead_time for o in history if o.supplier_id == supplier_id and o.consumable_id == item_id
            and o.lead_time is not None]
    if len(item) >= MIN_HISTORY:
        return LeadTimePrediction(float(np.median(item)), float(np.quantile(item, 0.9)), "item", len(item))
    sup = [o.lead_time for o in history if o.supplier_id == supplier_id and o.lead_time is not None]
    if len(sup) >= MIN_HISTORY:
        return LeadTimePrediction(float(np.median(sup)), float(np.quantile(sup, 0.9)), "supplier", len(sup))
    return LeadTimePrediction(float(quoted), float(quoted), "quoted", 0)


def late_rates(history: list[OrderRec], supplier_id: int, item_id: int) -> dict[str, float]:
    """P(first delivery after the expected date) at three levels, each shrunk towards the level above."""
    d = [o for o in history if o.first_delivery is not None]
    late = [o.first_delivery > o.expected for o in d]
    g = (sum(late) + 1) / (len(late) + 2)  # hospital-wide, Laplace-smoothed
    s = [o.first_delivery > o.expected for o in d if o.supplier_id == supplier_id]
    ps = shrink(sum(s), len(s), g, PRIOR_STRENGTH)
    i = [o.first_delivery > o.expected for o in d if o.supplier_id == supplier_id and o.consumable_id == item_id]
    pi = shrink(sum(i), len(i), ps, PRIOR_STRENGTH)
    return {"global": g, "supplier": ps, "item": pi}


def _metrics_lead(actual: np.ndarray, pred: np.ndarray, p90: np.ndarray | None) -> dict:
    err = pred - actual
    out = {"mae": float(np.mean(np.abs(err))), "bias": float(np.mean(err)),
           "within_1_day": float(np.mean(np.abs(err) <= 1))}
    if p90 is not None:
        out["p90_coverage"] = float(np.mean(actual <= p90))
    return out


def _metrics_prob(y: np.ndarray, p: np.ndarray) -> dict:
    eps = 1e-6
    return {"brier": float(np.mean((p - y) ** 2)), "roc_auc": roc_auc(y, p),
            "log_loss": float(-np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))),
            "mean_predicted": float(np.mean(p)), "observed_rate": float(np.mean(y))}


def backtest(orders: list[OrderRec], today: date, warmup_days: int = 90, rows_out: list | None = None) -> dict:
    """Expanding-window backtest: every delivered order placed after the first `warmup_days` of history is predicted
    from the orders whose outcome was already known on its order date (no look-ahead)."""
    first = min((o.ordered for o in orders), default=today)
    start = first + timedelta(days=warmup_days)
    test = sorted((o for o in orders if o.ordered >= start and o.first_delivery is not None), key=lambda o: o.ordered)
    rows = []
    for o in test:
        hist = _known(orders, o.ordered)
        if len(hist) < 10:
            continue
        lt_s = predict_lead_time([h for h in hist if h.supplier_id == o.supplier_id], o.supplier_id, -1, o.quoted_lead)
        lt_i = predict_lead_time(hist, o.supplier_id, o.consumable_id, o.quoted_lead)
        lr = late_rates(hist, o.supplier_id, o.consumable_id)
        rows.append((o.lead_time, o.quoted_lead, lt_s.median, lt_s.p90, lt_i.median, lt_i.p90,
                     float(o.first_delivery > o.expected), lr["global"], lr["supplier"], lr["item"]))
        if rows_out is not None:
            rows_out.append((o.id, lt_s.median, lt_s.p90, lt_i.median, lt_i.p90, lr["global"], lr["supplier"], lr["item"]))
    if not rows:
        return {"n_test": 0, "test_start": start.isoformat(), "test_end": today.isoformat()}
    a = np.array(rows, dtype=float)
    y = a[:, 6]
    return {
        "n_test": len(rows), "test_start": start.isoformat(), "test_end": today.isoformat(),
        "late_rate_test": float(y.mean()),
        "lead_time": {
            "quoted": _metrics_lead(a[:, 0], a[:, 1], a[:, 1]),
            "supplier_history": _metrics_lead(a[:, 0], a[:, 2], a[:, 3]),
            "supplier_item_history": _metrics_lead(a[:, 0], a[:, 4], a[:, 5]),
        },
        "delay": {
            "hospital_rate": _metrics_prob(y, a[:, 7]),
            "supplier_rate": _metrics_prob(y, a[:, 8]),
            "supplier_item_rate": _metrics_prob(y, a[:, 9]),
        },
    }
