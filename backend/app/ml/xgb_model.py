"""Global XGBoost model over all (item, department) series, forecasting recursively day by day.

V2A: consumption features only.  V2B: the same model class with `proc` set — procedure features are added
(see app/ml/procedures.py). Everything else (scaling, recursion, SHAP explanations) is shared.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import xgboost as xgb

from app.ml.features import FEATURES, WINDOW, feature_frame, series_scale, training_rows
from app.ml.procedures import PROC_FEATURES, ProcedureData

PARAMS = {
    "objective": "reg:squarederror",
    "eta": 0.05,
    "max_depth": 5,
    "min_child_weight": 3,
    "subsample": 0.8,
    "colsample_bytree": 0.9,
    "lambda": 1.0,
    "tree_method": "hist",
    "max_cat_to_onehot": 16,
    "seed": 42,
    "nthread": 2,
}
NUM_BOOST_ROUND = 300


@dataclass
class XGBForecaster:
    booster: xgb.Booster
    scale: np.ndarray  # per-series scale used for training
    item_categories: list[int]
    dept_categories: list[int]
    n_train_rows: int
    proc: ProcedureData | None = None  # V2B: procedure data (history + future schedule)

    @property
    def features(self) -> list[str]:
        return [*FEATURES, *PROC_FEATURES] if self.proc is not None else FEATURES

    def _extra(self, index: pd.DatetimeIndex, series: pd.DataFrame) -> dict[str, pd.DataFrame] | None:
        return self.proc.frames(index, series, self.scale) if self.proc is not None else None

    # ---------- training ----------

    @classmethod
    def fit(cls, values: pd.DataFrame, series: pd.DataFrame, proc: ProcedureData | None = None) -> "XGBForecaster":
        scale = series_scale(values)
        y = values / scale
        items = sorted(series["consumable_id"].unique().tolist())
        depts = sorted(series["department_id"].unique().tolist())
        model = cls(None, scale, items, depts, 0, proc)  # type: ignore[arg-type]
        rows = training_rows(y, series, items, depts, extra=model._extra(y.index, series))
        if len(rows) < 50:
            raise ValueError(f"Not enough training rows for XGBoost ({len(rows)})")
        dtrain = xgb.DMatrix(rows[model.features], label=rows["target"], enable_categorical=True)
        model.booster = xgb.train(PARAMS, dtrain, num_boost_round=NUM_BOOST_ROUND)
        model.n_train_rows = len(rows)
        return model

    # ---------- inference ----------

    def forecast(self, values: pd.DataFrame, series: pd.DataFrame, horizon: int, explain: bool = False):
        """Recursive multi-step forecast.

        Returns (pred, contrib): pred is horizon × series in units; contrib (if explain) is
        horizon × series × (len(self.features)+1) in units, last column = bias ("base").
        V2B: procedure features for each forecast day come from the schedule (known in advance).
        """
        y = (values / self.scale).copy()
        feats_names = self.features
        preds = np.zeros((horizon, y.shape[1]))
        contribs = np.zeros((horizon, y.shape[1], len(feats_names) + 1)) if explain else None
        future_index = pd.date_range(y.index[0], y.index[-1] + pd.Timedelta(days=horizon), freq="D")
        extra_all = self._extra(future_index, series)
        for h in range(horizon):
            next_day = y.index[-1] + pd.Timedelta(days=1)
            y.loc[next_day] = np.nan
            tail = y.iloc[-WINDOW:]
            extra = {k: v.reindex(tail.index) for k, v in extra_all.items()} if extra_all is not None else None
            feats = feature_frame(tail, series, self.item_categories, self.dept_categories, rows=slice(-1, None), extra=extra)
            dm = xgb.DMatrix(feats[feats_names], enable_categorical=True)
            p = np.clip(self.booster.predict(dm), 0.0, None)
            y.loc[next_day] = p
            preds[h] = p * self.scale
            if explain:
                c = self.booster.predict(dm, pred_contribs=True)
                contribs[h] = c * self.scale[:, None]
        return preds, contribs

    def feature_importance(self) -> dict[str, float]:
        gain = self.booster.get_score(importance_type="total_gain")
        total = sum(gain.values()) or 1.0
        return {f: round(gain.get(f, 0.0) / total, 4) for f in self.features}

    def to_bytes(self) -> bytes:
        return bytes(self.booster.save_raw("json"))
