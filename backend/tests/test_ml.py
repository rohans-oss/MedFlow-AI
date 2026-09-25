"""ML unit + regression tests: metrics, features (no leakage), baselines, XGBoost quality, censoring, pipeline."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from app.core.security import business_today
from app.db.base import utcnow
from app.ml import baselines
from app.ml.data import load_panel
from app.ml.features import FEATURES, feature_frame
from app.ml.metrics import forecast_metrics
from app.ml.pipeline import HORIZON, InsufficientDataError, run_training
from app.ml.xgb_model import XGBForecaster
from app.models import Forecast, ModelItemMetric, ModelVersion, MovementType, StockMovement

# ---------------------------------------------------------------- metrics


def test_metrics_known_values():
    m = forecast_metrics([10, 20, np.nan, 30], [12, 18, 99, 33])  # NaN actual (censored) is ignored
    assert m["n_points"] == 3
    assert m["mae"] == pytest.approx((2 + 2 + 3) / 3)
    assert m["rmse"] == pytest.approx(np.sqrt((4 + 4 + 9) / 3))
    assert m["wape"] == pytest.approx(7 / 60)
    assert m["bias"] == pytest.approx(3 / 60)
    assert forecast_metrics([0, 0], [1, 1])["wape"] is None  # undefined when nothing was consumed


# ---------------------------------------------------------------- features


def _frame(n_days=30, n_series=2):
    idx = pd.date_range("2026-01-01", periods=n_days, freq="D")
    y = pd.DataFrame({s: np.arange(n_days, dtype=float) + 100 * s for s in range(n_series)}, index=idx)
    series = pd.DataFrame({"consumable_id": [1, 2][:n_series], "department_id": [10, 10][:n_series]})
    return y, series


def test_features_use_only_the_past():
    y, series = _frame()
    f = feature_frame(y, series, [1, 2], [10]).set_index(["date", "series"])
    d = y.index[20]
    row = f.loc[(d, 0)]
    assert row["lag_1"] == y.loc[y.index[19], 0]
    assert row["lag_7"] == y.loc[y.index[13], 0]
    assert row["lag_14"] == y.loc[y.index[6], 0]
    assert row["roll_mean_7"] == pytest.approx(y[0].iloc[13:20].mean())
    assert row["roll_mean_14"] == pytest.approx(y[0].iloc[6:20].mean())
    assert row["day_of_week"] == d.dayofweek
    # changing today's value must not change today's features
    y2 = y.copy()
    y2.loc[d, 0] = 9999
    f2 = feature_frame(y2, series, [1, 2], [10]).set_index(["date", "series"])
    pd.testing.assert_series_equal(f.loc[(d, 0)][FEATURES[:7]], f2.loc[(d, 0)][FEATURES[:7]])


def test_censored_history_is_nan_not_zero():
    y, series = _frame()
    y.iloc[19, 0] = np.nan
    f = feature_frame(y, series, [1, 2], [10]).set_index(["date", "series"])
    assert np.isnan(f.loc[(y.index[20], 0)]["lag_1"])
    assert np.isfinite(f.loc[(y.index[20], 0)]["roll_mean_7"])  # rolling stats skip the gap


# ---------------------------------------------------------------- baselines


def test_baselines():
    y, _ = _frame(n_days=10, n_series=1)
    y.iloc[-1, 0] = np.nan
    ma = baselines.moving_average(y, horizon=3, window=7)
    assert ma.shape == (3, 1)
    assert ma[0, 0] == pytest.approx(np.mean(np.arange(2, 9)))  # last 7 known values (NaN skipped)
    ha = baselines.historical_average(y, horizon=2)
    assert ha[0, 0] == pytest.approx(np.mean(np.arange(9)))


# ---------------------------------------------------------------- model regression test


def _weekday_panel(n_days=84, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-05", periods=n_days, freq="D")
    weekday = np.array([1.2, 1.15, 1.1, 1.1, 1.0, 0.6, 0.45])
    levels = [80, 40, 15, 5, 120, 60]
    cols = {}
    for s, level in enumerate(levels):
        lam = level * weekday[idx.dayofweek] * (1 + 0.1 * np.sin(np.arange(n_days) / 9))
        cols[s] = rng.poisson(lam).astype(float)
    values = pd.DataFrame(cols, index=idx)
    series = pd.DataFrame({"consumable_id": [1, 1, 2, 2, 3, 3], "department_id": [10, 11, 10, 11, 10, 11]})
    return values, series


def test_xgboost_beats_moving_average_on_weekly_pattern():
    """Guards model quality: on data with a strong weekly cycle the ML model must beat the MA-7 baseline."""
    values, series = _weekday_panel()
    train, test = values.iloc[:-14], values.iloc[-14:]
    model = XGBForecaster.fit(train, series)
    pred, contrib = model.forecast(train, series, 14, explain=True)
    xgb_wape = forecast_metrics(test.to_numpy().ravel(), pred.ravel())["wape"]
    ma_wape = forecast_metrics(test.to_numpy().ravel(), baselines.moving_average(train, 14).ravel())["wape"]
    assert xgb_wape < ma_wape * 0.9, (xgb_wape, ma_wape)
    # SHAP contributions (units) add up to the prediction wherever it was not clipped at zero
    np.testing.assert_allclose(contrib.sum(axis=2), pred, rtol=1e-3, atol=1e-2)
    assert model.feature_importance()["day_of_week"] > 0


def test_xgboost_is_deterministic():
    values, series = _weekday_panel(n_days=60)
    p1, _ = XGBForecaster.fit(values, series).forecast(values, series, 5)
    p2, _ = XGBForecaster.fit(values, series).forecast(values, series, 5)
    np.testing.assert_allclose(p1, p2)


# ---------------------------------------------------------------- DB-backed: panel, censoring, pipeline


def _add_history(db, world, days=45, stockout_days=()):
    """Synthetic issue history straight into the ledger (fast; bypasses FEFO)."""
    h, item = world["hospital"], world["gloves"]
    today = business_today()
    rng = np.random.default_rng(1)
    rows = []
    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        ts = utcnow().replace(year=day.year, month=day.month, day=day.day, hour=6)  # 11:30 IST
        out = back in stockout_days
        for dept, level in ((world["ortho"], 40), (world["icu"], 15)):
            qty = int(rng.poisson(level * (0.5 if day.weekday() >= 5 else 1.1)))
            if qty:
                rows.append(StockMovement(hospital_id=h.id, consumable_id=item.id, movement_type=MovementType.ISSUE,
                                          quantity=-qty, balance_after=0 if out else 5000,
                                          department_id=dept.id, created_at=ts))
    db.add_all(rows)
    db.commit()


def test_panel_marks_stockout_days_censored(db, world):
    _add_history(db, world, days=30, stockout_days={5, 6})
    p = load_panel(db, world["hospital"].id)
    s = p.item_actual[world["gloves"].id]
    today = business_today()
    censored = {d.date() for d, v in s.items() if np.isnan(v)}
    assert today - timedelta(days=5) in censored and today - timedelta(days=6) in censored
    assert today - timedelta(days=10) not in censored
    assert p.data_end == today - timedelta(days=1)  # today (partial) excluded
    assert set(p.series["department_id"]) == {world["ortho"].id, world["icu"].id}


def test_pipeline_trains_selects_and_stores(db, world):
    _add_history(db, world, days=60)
    result = run_training(db, world["hospital"].id)
    db.commit()
    versions = db.scalars(select(ModelVersion)).all()
    assert {v.model_type for v in versions} == {"xgboost", "moving_average_7", "historical_average"}
    active = [v for v in versions if v.is_active]
    assert len(active) == 1 and active[0].name == result["active_model"]
    best_wape = min(v.metrics["wape"] for v in versions)
    assert active[0].metrics["wape"] == best_wape
    for v in versions:
        assert set(v.metrics) >= {"mae", "rmse", "wape", "bias", "n_points"} and v.trained_at and v.dataset_hash
    xgb = next(v for v in versions if v.model_type == "xgboost")
    assert xgb.artifact and xgb.feature_importance and xgb.features == FEATURES
    # every active item gets HORIZON daily forecasts
    n_items = db.scalar(select(func.count(func.distinct(Forecast.consumable_id))))
    assert db.scalar(select(func.count(Forecast.id))) == n_items * HORIZON
    assert db.scalar(select(func.count(ModelItemMetric.id))) == n_items * 3

    # retraining creates v2 and moves the active flag
    run_training(db, world["hospital"].id)
    db.commit()
    names = {v.name for v in db.scalars(select(ModelVersion))}
    assert {"xgb_v1", "xgb_v2", "ma7_v2"} <= names
    assert db.scalar(select(func.count(ModelVersion.id)).where(ModelVersion.is_active.is_(True))) == 1


def test_pipeline_refuses_tiny_history(db, world):
    _add_history(db, world, days=10)
    with pytest.raises(InsufficientDataError):
        run_training(db, world["hospital"].id)


def test_pipeline_on_demo_seed(db):
    """End-to-end on the realistic demo simulation (short window to keep the suite fast)."""
    from app.seed import seed

    h = seed(db, days=40, verbose=False)
    result = run_training(db, h.id)
    db.commit()
    assert result["n_items"] == 42
    for m in result["candidates"].values():
        assert m["wape"] is not None and 0 <= m["wape"] < 1
