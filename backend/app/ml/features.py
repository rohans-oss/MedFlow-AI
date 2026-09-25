"""Feature engineering. Every feature for date d uses only values strictly before d (no leakage).

Series are scaled by their own training mean so large and small items share one global model;
`item` and `department` enter as categorical features.
"""

import numpy as np
import pandas as pd

FEATURES = [
    "lag_1",          # previous-day consumption
    "lag_7",          # same weekday last week
    "lag_14",         # same weekday two weeks ago
    "roll_mean_7",    # 7-day rolling average
    "roll_mean_14",   # 14-day rolling average
    "roll_std_7",     # 7-day volatility
    "trend_7_14",     # recent consumption trend: 7-day minus 14-day average
    "day_of_week",
    "department",
    "item",
]

FEATURE_LABELS = {
    "lag_1": "Yesterday's consumption",
    "lag_7": "Same weekday last week",
    "lag_14": "Same weekday two weeks ago",
    "roll_mean_7": "7-day average",
    "roll_mean_14": "14-day average",
    "roll_std_7": "7-day volatility",
    "trend_7_14": "Recent trend (7-day vs 14-day)",
    "day_of_week": "Day of week",
    "department": "Department mix",
    "item": "Item-specific level",
    "base": "Model baseline",
    # V2B
    "procedure_count": "Scheduled procedures (department)",
    "procedure_expected_quantity": "Scheduled procedure demand (kit)",
    "procedure_type_count": "Procedure types involved",
    "procedure_department_count": "Departments with procedures",
    "procedure_demand_share": "Procedure share of demand",
    "procedure_expected_delta_7": "Procedure load vs last 7 days",
}

MIN_HISTORY = 7  # a training row needs at least a week of history
WINDOW = 15  # rows needed to compute every feature for one date


def series_scale(values: pd.DataFrame) -> np.ndarray:
    """Mean non-censored consumption per series (floor avoids divide-by-zero for very sparse series)."""
    s = values.mean(axis=0, skipna=True).to_numpy(dtype=float)
    s = np.where(np.isfinite(s) & (s > 0), s, 1.0)
    return np.maximum(s, 0.05)


def _numeric_features(y: pd.DataFrame) -> dict[str, pd.DataFrame]:
    past = y.shift(1)
    rm7 = past.rolling(7, min_periods=3).mean()
    rm14 = past.rolling(14, min_periods=5).mean()
    return {
        "lag_1": past,
        "lag_7": y.shift(7),
        "lag_14": y.shift(14),
        "roll_mean_7": rm7,
        "roll_mean_14": rm14,
        "roll_std_7": past.rolling(7, min_periods=3).std(),
        "trend_7_14": rm7 - rm14,
    }


def _procedure_features(extra: dict[str, pd.DataFrame], num: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """V2B features from same-day schedule frames (+ past only). `extra` frames are aligned to the y index."""
    e = extra["procedure_expected_quantity"]
    rm14 = num["roll_mean_14"]
    share = (e / rm14.where(rm14 > 0)).clip(upper=1.0)  # kit demand as a share of typical recent demand
    share = share.where(e > 0, 0.0)
    delta = e - e.shift(1).rolling(7, min_periods=1).mean()  # today's procedure load vs the previous week
    return {**{k: extra[k] for k in extra}, "procedure_demand_share": share, "procedure_expected_delta_7": delta.fillna(e)}


def feature_frame(
    y_scaled: pd.DataFrame,
    series: pd.DataFrame,
    item_categories: list[int],
    dept_categories: list[int],
    rows: slice | None = None,
    extra: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Long feature table: one row per (date, series) for the dates selected by `rows`.

    `y_scaled`: dates × series (scaled; NaN = censored/unknown).
    `extra` (V2B): procedure frames aligned to y_scaled.index; values for date d describe procedures ON d.
    Output columns: date, series, FEATURES..., [procedure features], target (scaled value at that date, may be NaN).
    """
    num = _numeric_features(y_scaled)
    if extra is not None:
        num = {**num, **_procedure_features({k: v.reindex(y_scaled.index).fillna(0.0) for k, v in extra.items()}, num)}
    idx = y_scaled.index if rows is None else y_scaled.index[rows]
    n_series = y_scaled.shape[1]
    n_dates = len(idx)

    out = {"date": np.repeat(idx.values, n_series), "series": np.tile(np.arange(n_series), n_dates)}
    for name, frame in num.items():
        out[name] = frame.loc[idx].to_numpy(dtype=float).reshape(-1)
    out["day_of_week"] = np.repeat(idx.dayofweek.to_numpy(), n_series)
    out["target"] = y_scaled.loc[idx].to_numpy(dtype=float).reshape(-1)
    df = pd.DataFrame(out)
    df["item"] = pd.Categorical(series["consumable_id"].to_numpy()[df["series"]], categories=item_categories)
    df["department"] = pd.Categorical(series["department_id"].to_numpy()[df["series"]], categories=dept_categories)
    return df


def training_rows(
    y_scaled: pd.DataFrame, series: pd.DataFrame, item_categories: list[int], dept_categories: list[int],
    extra: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Rows usable for training: enough history and a known (non-censored) target."""
    df = feature_frame(y_scaled, series, item_categories, dept_categories, rows=slice(MIN_HISTORY, None), extra=extra)
    return df[np.isfinite(df["target"])].reset_index(drop=True)
