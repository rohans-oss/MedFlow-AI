"""Forecast error metrics. NaN actuals (censored days) are ignored."""

import numpy as np


def forecast_metrics(actual, predicted) -> dict:
    a = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    mask = np.isfinite(a) & np.isfinite(p)
    a, p = a[mask], p[mask]
    n = int(a.size)
    if n == 0:
        return {"mae": None, "rmse": None, "wape": None, "bias": None, "n_points": 0, "actual_total": 0.0,
                "predicted_total": 0.0}
    err = p - a
    total = float(a.sum())
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err**2))),
        # WAPE = Σ|error| / Σ actual — scale-free, robust to zero days (unlike MAPE)
        "wape": float(np.abs(err).sum() / total) if total > 0 else None,
        # bias = Σ(pred − actual) / Σ actual; positive = over-forecasting
        "bias": float(err.sum() / total) if total > 0 else None,
        "n_points": n,
        "actual_total": total,
        "predicted_total": float(p.sum()),
    }


def residual_std(actual, predicted) -> float:
    a = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    mask = np.isfinite(a) & np.isfinite(p)
    if mask.sum() < 2:
        return 0.0
    return float(np.std(p[mask] - a[mask], ddof=1))
