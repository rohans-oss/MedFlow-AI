"""Baseline forecasters. Flat forecasts per series; the benchmark every ML model must beat."""

import numpy as np
import pandas as pd


def moving_average(values: pd.DataFrame, horizon: int, window: int = 7) -> np.ndarray:
    """Mean of the last `window` known (non-censored) days per series. Returns horizon × series."""
    out = []
    for col in values.columns:
        known = values[col].dropna()
        out.append(float(known.iloc[-window:].mean()) if len(known) else 0.0)
    return np.tile(np.array(out), (horizon, 1))


def historical_average(values: pd.DataFrame, horizon: int) -> np.ndarray:
    """Mean of all known days per series. Returns horizon × series."""
    means = values.mean(axis=0, skipna=True).fillna(0.0).to_numpy(dtype=float)
    return np.tile(means, (horizon, 1))
