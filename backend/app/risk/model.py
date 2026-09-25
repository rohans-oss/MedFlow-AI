"""Stockout-risk scorers: XGBoost classifier (V3.2) and two rule baselines.

    reorder_rule  what V1 already does: warn when usable stock ≤ reorder level (score = 1 / (1 + stock/reorder))
    cover_rule    V3.1 deterministic: warn when projected cover (point-in-time demand, no deliveries) is short;
                  its probability is the observed stockout rate for items with that cover in the training data
    xgboost       gradient-boosted classifier on all point-in-time features
"""

import numpy as np
import pandas as pd
import xgboost as xgb

from app.risk.features import FEATURES, HORIZON

PARAMS = {
    "objective": "binary:logistic",
    "eval_metric": "aucpr",
    "eta": 0.05,
    "max_depth": 4,
    "min_child_weight": 5,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "lambda": 1.0,
    "tree_method": "hist",
    "seed": 42,
    "nthread": 2,
}
ROUNDS = 300
COVER_BUCKETS = [0, 2, 4, 7, 10, 14, 21, 31, 1000]  # projected cover (days) bucket edges


class RiskXGB:
    def __init__(self, booster: xgb.Booster):
        self.booster = booster

    @classmethod
    def fit(cls, X: pd.DataFrame, y: np.ndarray) -> "RiskXGB":
        d = xgb.DMatrix(X[FEATURES].to_numpy(dtype=float), label=y, feature_names=FEATURES, missing=np.nan)
        return cls(xgb.train(PARAMS, d, num_boost_round=ROUNDS))

    def _dm(self, X: pd.DataFrame) -> xgb.DMatrix:
        return xgb.DMatrix(X[FEATURES].to_numpy(dtype=float), feature_names=FEATURES, missing=np.nan)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(self._dm(X)) if len(X) else np.array([])

    def contributions(self, X: pd.DataFrame) -> np.ndarray:
        """SHAP values in log-odds (last column = bias)."""
        return self.booster.predict(self._dm(X), pred_contribs=True)

    def importance(self) -> dict[str, float]:
        gain = self.booster.get_score(importance_type="total_gain")
        total = sum(gain.values()) or 1.0
        return {f: round(gain.get(f, 0.0) / total, 4) for f in FEATURES}

    def to_bytes(self) -> bytes:
        return bytes(self.booster.save_raw("json"))

    @classmethod
    def from_bytes(cls, raw: bytes) -> "RiskXGB":
        b = xgb.Booster()
        b.load_model(bytearray(raw))
        return cls(b)


def reorder_score(X: pd.DataFrame) -> np.ndarray:
    """V1 rule as a score: ≥ 0.5 exactly when usable stock ≤ reorder level."""
    ratio = X["stock_to_reorder"].to_numpy(dtype=float)
    return 1.0 / (1.0 + ratio)


def cover_buckets(cover: np.ndarray) -> np.ndarray:
    return np.clip(np.searchsorted(COVER_BUCKETS, cover, side="right") - 1, 0, len(COVER_BUCKETS) - 2)


def fit_cover_rates(X: pd.DataFrame, y: np.ndarray) -> list[float]:
    """Observed stockout rate per projected-cover bucket (monotone non-increasing, smoothed with a +1/+2 prior)."""
    b = cover_buckets(X["projected_cover_days"].to_numpy(dtype=float))
    rates = []
    for k in range(len(COVER_BUCKETS) - 1):
        m = b == k
        rates.append((float(y[m].sum()) + 1.0) / (float(m.sum()) + 2.0))
    for k in range(1, len(rates)):  # more cover can never mean more risk
        rates[k] = min(rates[k], rates[k - 1])
    return rates


def cover_probability(X: pd.DataFrame, rates: list[float]) -> np.ndarray:
    return np.asarray(rates)[cover_buckets(X["projected_cover_days"].to_numpy(dtype=float))]


def cover_rule_label(k: int) -> str:
    lo, hi = COVER_BUCKETS[k], COVER_BUCKETS[k + 1]
    return f"{lo}–{hi - 1} days" if hi < 1000 else f"≥ {lo} days"


__all__ = ["RiskXGB", "reorder_score", "fit_cover_rates", "cover_probability", "HORIZON"]
