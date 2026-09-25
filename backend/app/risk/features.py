"""V3.2 — point-in-time stockout-risk features and labels.

An *origin* t is the end of business day t. Features use only data on or before t, plus the procedure schedule for
the following days (a plan that is known in advance, as in V2B). The label is "the item was available at t and
stocked out on at least one of the next H days".

The same code builds features for (a) the hospital's ledger history (backtest), (b) simulated histories (training)
and (c) the current day (serving), so there is no training/serving skew in how features are computed.

Demand in the features comes from a *point-in-time* estimate (recent non-procedure level × weekday profile + scheduled
procedure kits). The V2 forecasting model is not re-trained for every historical day, so it cannot be used here
without leaking; the displayed stockout date/shortage use the served V2A/V2B forecast instead (see engine.py).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

HORIZON = 14  # label: stockout within the next 14 days
AHEAD = 30  # days of demand estimated per origin
MIN_HISTORY = 28  # days of history needed before an origin is usable
COVER_CAP = AHEAD + 1  # "covers more than the projection"

FEATURES = [
    "usable_stock",
    "forecast_7",
    "forecast_14",
    "forecast_30",
    "stock_to_forecast_7",
    "stock_to_forecast_14",
    "stock_to_forecast_30",
    "recent_mean_7",
    "recent_mean_28",
    "trend_7_28",
    "volatility_28",
    "procedure_demand_14",
    "procedure_share_14",
    "lead_time_days",
    "stock_to_reorder",
    "projected_cover_days",
    "cover_minus_lead_time",
    "expiring_14",
    "expiring_share_14",
    "stockout_days_60",
    "days_since_stockout",
    "days_since_receipt",
    "department",
]

FEATURE_LABELS = {
    "usable_stock": "Current usable stock",
    "forecast_7": "7-day demand estimate",
    "forecast_14": "14-day demand estimate",
    "forecast_30": "30-day demand estimate",
    "stock_to_forecast_7": "Stock vs 7-day demand",
    "stock_to_forecast_14": "Stock vs 14-day demand",
    "stock_to_forecast_30": "Stock vs 30-day demand",
    "recent_mean_7": "Recent consumption (7-day avg)",
    "recent_mean_28": "Consumption (28-day avg)",
    "trend_7_28": "Consumption trend (7 vs 28 days)",
    "volatility_28": "Demand volatility",
    "procedure_demand_14": "Procedure-driven demand (14 d)",
    "procedure_share_14": "Procedure share of demand",
    "lead_time_days": "Supplier lead time",
    "stock_to_reorder": "Stock vs reorder level",
    "projected_cover_days": "Projected days of cover",
    "cover_minus_lead_time": "Cover minus lead time",
    "expiring_14": "Stock expiring within 14 days",
    "expiring_share_14": "Share of stock expiring soon",
    "stockout_days_60": "Stockout days in last 60 days",
    "days_since_stockout": "Days since last stockout",
    "days_since_receipt": "Days since last delivery",
    "department": "Main department",
}

@dataclass
class ItemStatic:
    reorder_level: float
    lead_time_days: float | None
    department: float | None


def _since(flag: np.ndarray, cap: int) -> np.ndarray:
    """Days since the flag was last true (0 on a flagged day), capped."""
    out = np.empty(len(flag))
    last = None
    for i, f in enumerate(flag):
        if f:
            last = i
        out[i] = cap if last is None else min(i - last, cap)
    return out


def weekday_factors(nonproc: pd.Series) -> np.ndarray:
    """(n_days, 7) multiplicative weekday profile from the previous 56 days (1.0 where unknown)."""
    overall = nonproc.rolling(56, min_periods=14).mean()
    wd = nonproc.index.dayofweek
    out = np.ones((len(nonproc), 7))
    for w in range(7):
        s = nonproc.where(wd == w)
        mean_w = s.rolling(56, min_periods=1).mean()
        cnt = s.notna().astype(float).rolling(56, min_periods=1).sum()
        f = (mean_w / overall).where((cnt >= 2) & (overall > 0))
        out[:, w] = f.clip(0.3, 2.5).fillna(1.0).to_numpy()
    return out


def build(frame: pd.DataFrame, static: ItemStatic, kit_ahead: pd.Series | None = None,
          origins: np.ndarray | None = None, horizon: int = HORIZON,
          usable_override: float | None = None, expiring_override: float | None = None,
          known_until: pd.Timestamp | None = None) -> pd.DataFrame:
    """Features (+ `label`, `first_stockout_offset`) for origins (positions into `frame`).

    `kit_ahead`: kit units for dates after the frame end; `known_until`: last date the schedule is known (days up to
    it without a kit entry are 0; later days are unknown and use the recent kit average).
    Default origins: every position with ≥ MIN_HISTORY days of history.
    """
    n = len(frame)
    if n == 0:
        return pd.DataFrame(columns=[*FEATURES, "label", "first_stockout_offset", "available"])
    idx = frame.index
    stock = frame["stockout"].to_numpy(dtype=bool)
    consumed = frame["consumed"].astype(float)
    kit = frame["kit"].astype(float)
    c = consumed.where(~frame["stockout"])  # censored: consumption on stockout days is not demand
    kit_nc = kit.where(~frame["stockout"])

    m7 = c.rolling(7, min_periods=3).mean()
    m28 = c.rolling(28, min_periods=7).mean()
    sd28 = c.rolling(28, min_periods=7).std()
    km28 = kit_nc.rolling(28, min_periods=7).mean().fillna(0.0)
    level = (m28 - km28).clip(lower=0).fillna(0.0)
    fw = weekday_factors((c - kit_nc).clip(lower=0))

    if origins is None:
        origins = np.arange(MIN_HISTORY - 1, n)
    origins = np.asarray(origins, dtype=int)
    if len(origins) == 0:
        return pd.DataFrame(columns=[*FEATURES, "label", "first_stockout_offset", "available"])

    # kit for days after each origin: in-frame days are known history/plan; after the frame use kit_ahead
    full_kit = pd.concat([kit, kit_ahead if kit_ahead is not None else pd.Series(dtype=float)])
    full_kit = full_kit[~full_kit.index.duplicated(keep="first")]
    last_known = max(full_kit.index.max(), known_until) if known_until is not None else full_kit.index.max()
    span = pd.date_range(idx[0], idx[-1] + pd.Timedelta(days=AHEAD), freq="D")
    kit_arr = full_kit.reindex(span).to_numpy(dtype=float, copy=True)
    kit_arr[np.asarray(span > last_known)] = np.nan  # unknown schedule
    if kit_ahead is not None or known_until is not None:
        in_future = np.asarray(span > idx[-1]) & np.isnan(kit_arr) & np.asarray(span <= last_known)
        kit_arr[in_future] = 0.0  # known schedule, nothing planned that day

    ks = origins[:, None] + np.arange(1, AHEAD + 1)[None, :]
    K = kit_arr[ks]
    K = np.where(np.isnan(K), km28.to_numpy()[origins][:, None], K)
    wd0 = idx.dayofweek.to_numpy()[origins]
    wds = (wd0[:, None] + np.arange(1, AHEAD + 1)[None, :]) % 7
    F = fw[origins][np.arange(len(origins))[:, None], wds]
    daily = level.to_numpy()[origins][:, None] * F + K
    f7, f14, f30 = daily[:, :7].sum(1), daily[:, :14].sum(1), daily.sum(1)

    usable = frame["usable_end"].to_numpy(dtype=float)[origins].copy()
    expiring = frame["expiring_14"].to_numpy(dtype=float)[origins].copy()
    if usable_override is not None:
        usable[-1] = usable_override
    if expiring_override is not None:
        expiring[-1] = expiring_override
    loss = np.clip(expiring - f7, 0, None)  # FEFO uses expiring stock first; the rest may be lost
    eff = np.clip(usable - loss, 0, None)
    cover = (np.cumsum(daily, axis=1) <= eff[:, None] + 1e-9).sum(1).astype(float)
    cover = np.where(cover >= AHEAD, COVER_CAP, cover)

    def ratio(a, b, cap):
        return np.clip(np.where(b > 0.5, a / np.maximum(b, 0.5), cap), 0, cap)

    lead = np.nan if static.lead_time_days is None else float(static.lead_time_days)
    so60 = frame["stockout"].astype(float).rolling(60, min_periods=1).sum().to_numpy()
    since_so = _since(stock, 90)
    since_rec = _since(frame["received"].to_numpy() > 0, 60)
    m7o, m28o = m7.to_numpy()[origins], m28.to_numpy()[origins]
    proc14 = K[:, :14].sum(1)
    out = pd.DataFrame({
        "usable_stock": usable,
        "forecast_7": f7,
        "forecast_14": f14,
        "forecast_30": f30,
        "stock_to_forecast_7": ratio(usable, f7, 99.0),
        "stock_to_forecast_14": ratio(usable, f14, 99.0),
        "stock_to_forecast_30": ratio(usable, f30, 99.0),
        "recent_mean_7": m7o,
        "recent_mean_28": m28o,
        "trend_7_28": np.clip(np.where(m28o > 0, m7o / np.where(m28o > 0, m28o, 1), 1.0), 0, 5),
        "volatility_28": np.clip(np.where(m28o > 0, sd28.to_numpy()[origins] / np.where(m28o > 0, m28o, 1), 0.0), 0, 5),
        "procedure_demand_14": proc14,
        "procedure_share_14": np.clip(np.where(f14 > 0, proc14 / np.where(f14 > 0, f14, 1), 0.0), 0, 1),
        "lead_time_days": lead,
        "stock_to_reorder": ratio(usable, np.full(len(origins), max(static.reorder_level, 1.0)), 20.0),
        "projected_cover_days": cover,
        "cover_minus_lead_time": cover - lead,
        "expiring_14": expiring,
        "expiring_share_14": np.clip(np.where(usable > 0, expiring / np.where(usable > 0, usable, 1), 0.0), 0, 1),
        "stockout_days_60": so60[origins],
        "days_since_stockout": since_so[origins],
        "days_since_receipt": since_rec[origins],
        "department": np.nan if static.department is None else float(static.department),
    }, index=idx[origins])

    # labels: available at t and a stockout on one of the next `horizon` days (only when fully observed)
    label = np.full(len(origins), np.nan)
    first = np.full(len(origins), np.nan)
    for j, p in enumerate(origins):
        window = stock[p + 1:p + 1 + horizon]
        hit = np.flatnonzero(window)
        if len(hit):
            first[j] = hit[0] + 1
        if p + horizon <= n - 1:
            label[j] = float(len(hit) > 0)
        elif len(hit):
            label[j] = 1.0  # observed stockout even though the window is incomplete
    out["available"] = (~stock[origins]) & (usable > 0)
    out["label"] = label
    out["first_stockout_offset"] = first
    out["daily_demand"] = list(daily)
    return out
