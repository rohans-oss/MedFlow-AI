"""Training run: backtest every candidate on the same holdout, pick the best, refit, store forecasts.

    data ─► split (train | last TEST_DAYS days) ─► fit candidates on train ─► forecast TEST_DAYS
         ─► score item-level daily totals (MAE, RMSE, WAPE, bias) ─► select lowest WAPE
         ─► refit selected model on ALL data ─► forecast HORIZON days ─► persist

Candidates: historical average, 7-day moving average, V2A XGBoost (consumption) and — when procedure data
exists — V2B XGBoost (consumption + procedure features). V2B is served only if its holdout WAPE is strictly
lower than every other candidate's; otherwise V2A (or a baseline) stays active. Without procedure data the
V2B candidate is skipped and the run is identical to V2A.
"""

import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.ml import baselines
from app.ml.data import Panel, load_panel
from app.ml.features import FEATURES, MIN_HISTORY
from app.ml.metrics import forecast_metrics, residual_std
from app.ml.procedures import PROC_FEATURES, ProcedureData, load_procedure_data
from app.ml.xgb_model import NUM_BOOST_ROUND, PARAMS, XGBForecaster
from app.models import Forecast, ModelItemMetric, ModelVersion, User

TEST_DAYS = 14
HORIZON = 30
MIN_DAYS = MIN_HISTORY + TEST_DAYS + 7  # 28 days of history before we train anything
DRIVER_HORIZONS = (7, 14, 30)

CANDIDATES = {
    "xgboost": "xgb",
    "moving_average_7": "ma7",
    "historical_average": "hist_avg",
    "xgboost_procedure": "v2b_xgb",  # V2B
}
XGB_TYPES = ("xgboost", "xgboost_procedure")
# Tie-break order: on equal WAPE the simpler/established model wins; V2B must be strictly better to be served.
PRIORITY = {"xgboost": 0, "moving_average_7": 1, "historical_average": 2, "xgboost_procedure": 3}


VALIDATION_FOLDS = 3  # V2B must also win on earlier 14-day windows, not just the latest one


def consistent_improvement(folds: list[dict]) -> bool:
    """V2B is eligible only if it beats V2A on the latest holdout AND on every earlier validation window.

    One 14-day window is noisy: in experiments with deliberately uninformative procedure data, V2B "won" a single
    holdout often. Requiring a win on every window makes a spurious switch much less likely (not impossible).
    """
    return bool(folds) and all(f["v2b_wape"] < f["v2a_wape"] for f in folds)


def select_best(metrics_by_type: dict[str, dict]) -> str:
    """Lowest holdout WAPE wins. Never forced: V2B wins only with a strictly lower WAPE."""
    def key(t: str):
        w = metrics_by_type[t].get("wape")
        return (float("inf") if w is None else round(w, 6), PRIORITY.get(t, 9))

    return min(metrics_by_type, key=key)


class InsufficientDataError(ValueError):
    pass


@dataclass
class CandidateResult:
    model_type: str
    item_pred: pd.DataFrame  # test dates × consumable_id (units)
    metrics: dict
    model: XGBForecaster | None = None


def _to_items(pred: np.ndarray, dates: pd.DatetimeIndex, panel: Panel) -> pd.DataFrame:
    """horizon × series → horizon × item (sum over departments); items without series get 0."""
    df = pd.DataFrame(pred, index=dates, columns=panel.series["consumable_id"].to_numpy())
    df = df.T.groupby(level=0).sum().T
    return df.reindex(columns=panel.item_actual.columns, fill_value=0.0)


def _predict(model_type: str, values: pd.DataFrame, panel: Panel, horizon: int, model: XGBForecaster | None,
             explain: bool = False):
    if model_type in XGB_TYPES:
        return model.forecast(values, panel.series, horizon, explain=explain)
    if model_type == "moving_average_7":
        return baselines.moving_average(values, horizon, 7), None
    return baselines.historical_average(values, horizon), None


def _dataset_hash(panel: Panel) -> str:
    h = hashlib.sha256()
    h.update(str(panel.dates[0]).encode() + str(panel.dates[-1]).encode())
    h.update(panel.series.to_numpy().tobytes())
    h.update(np.nan_to_num(panel.values.to_numpy(), nan=-1.0).tobytes())
    return h.hexdigest()


def run_training(db: Session, hospital_id: int, user: User | None = None, data_end=None) -> dict:
    started = time.perf_counter()
    panel = load_panel(db, hospital_id, data_end)
    if len(panel.dates) < MIN_DAYS or panel.values.shape[1] == 0:
        raise InsufficientDataError(
            f"Need at least {MIN_DAYS} days of consumption history to train; have {len(panel.dates)}."
        )

    # ---------------- V2B procedure data (optional) ----------------
    proc = load_procedure_data(db, hospital_id, panel.data_end, HORIZON)
    train_end = panel.dates[-TEST_DAYS - 1].date()
    use_proc = proc.has_signal(train_end)
    proc_reason = None if use_proc else (
        "No procedure schedule or item mappings" if proc.is_empty() else "Less than 14 days of procedure history")

    # ---------------- backtest ----------------
    train_values = panel.values.iloc[:-TEST_DAYS]
    test_dates = panel.dates[-TEST_DAYS:]
    actual = panel.item_actual.loc[test_dates]

    results: dict[str, CandidateResult] = {}
    for model_type in CANDIDATES:
        if model_type == "xgboost_procedure" and not use_proc:
            continue
        model = None
        try:
            if model_type == "xgboost":
                model = XGBForecaster.fit(train_values, panel.series)
            elif model_type == "xgboost_procedure":
                model = XGBForecaster.fit(train_values, panel.series, proc=proc)
            pred, _ = _predict(model_type, train_values, panel, TEST_DAYS, model)
        except ValueError:
            continue  # e.g. not enough rows for XGBoost; baselines still run
        item_pred = _to_items(pred, test_dates, panel)
        results[model_type] = CandidateResult(
            model_type, item_pred, forecast_metrics(actual.to_numpy().ravel(), item_pred.to_numpy().ravel()), model
        )

    # ---- V2B robustness check: a single 14-day holdout is noisy, so V2B must ALSO beat V2A on every earlier
    # rolling-origin window (the latest window is the official holdout above). Otherwise it is not eligible.
    folds: list[dict] = []
    v2b_eligible = "xgboost_procedure" in results
    if "xgboost_procedure" in results and "xgboost" in results:
        folds = _validation_folds(panel, proc, results)
        v2b_eligible = consistent_improvement(folds)
    eligible = {t: r.metrics for t, r in results.items() if t != "xgboost_procedure" or v2b_eligible}
    best_type = select_best(eligible)

    # ---------------- refit on all data & forecast ----------------
    future_dates = pd.date_range(panel.dates[-1] + pd.Timedelta(days=1), periods=HORIZON, freq="D")
    finals: dict[str, XGBForecaster] = {}
    outputs: dict[str, tuple[np.ndarray, np.ndarray | None]] = {}
    for t in XGB_TYPES:
        if t in results:
            finals[t] = XGBForecaster.fit(panel.values, panel.series, proc=proc if t == "xgboost_procedure" else None)
            outputs[t] = _predict(t, panel.values, panel, HORIZON, finals[t], explain=True)
    if best_type not in outputs:
        outputs[best_type] = _predict(best_type, panel.values, panel, HORIZON, None, explain=True)

    # V2B beyond the last scheduled date: procedures are unknown there → use the V2A forecast for those days
    fallback_from = None
    if "xgboost_procedure" in outputs and "xgboost" in outputs:
        known_until = proc.schedule_end if proc.schedule_end and proc.schedule_end > panel.data_end else panel.data_end
        mask = np.array([d.date() > known_until for d in future_dates])
        if mask.any():
            fallback_from = future_dates[mask][0].date()
            v2b_pred, v2b_c = outputs["xgboost_procedure"]
            v2a_pred, v2a_c = outputs["xgboost"]
            pred = np.where(mask[:, None], v2a_pred, v2b_pred)
            contrib = v2b_c.copy()
            padded = np.zeros_like(v2b_c)
            padded[:, :, :len(FEATURES)] = v2a_c[:, :, :len(FEATURES)]
            padded[:, :, -1] = v2a_c[:, :, -1]
            contrib[mask] = padded[mask]
            outputs["xgboost_procedure"] = (pred, contrib)

    # ---------------- persist ----------------
    run_no = (db.scalar(select(func.count(func.distinct(ModelVersion.run_id))).where(
        ModelVersion.hospital_id == hospital_id)) or 0) + 1
    run_id = uuid.uuid4().hex
    ds_hash = _dataset_hash(panel)
    proc_hash = hashlib.sha256((ds_hash + proc.fingerprint).encode()).hexdigest()
    db.execute(update(ModelVersion).where(ModelVersion.hospital_id == hospital_id).values(is_active=False))

    v2a_w = results["xgboost"].metrics["wape"] if "xgboost" in results else None
    v2b_w = results["xgboost_procedure"].metrics["wape"] if "xgboost_procedure" in results else None
    improved = v2a_w is not None and v2b_w is not None and best_type == "xgboost_procedure"

    versions: dict[str, ModelVersion] = {}
    for model_type, r in results.items():
        is_best = model_type == best_type
        artifact_model = finals.get(model_type) or r.model
        is_proc = model_type == "xgboost_procedure"
        params: dict = {}
        if model_type in XGB_TYPES:
            params = {"xgboost": PARAMS, "num_boost_round": NUM_BOOST_ROUND}
        elif model_type == "moving_average_7":
            params = {"window_days": 7}
        if is_proc:
            params["procedure_data"] = {
                "schedule_through": proc.schedule_end.isoformat() if proc.schedule_end else None,
                "schedule_rows": proc.n_schedule_rows, "mappings": proc.n_mappings,
                "fingerprint": proc.fingerprint,
                "falls_back_to_v2a_from": fallback_from.isoformat() if fallback_from else None,
            }
            params["validation_folds"] = folds
            params["consistent_improvement"] = v2b_eligible
        mv = ModelVersion(
            hospital_id=hospital_id,
            run_id=run_id,
            name=f"{CANDIDATES[model_type]}_v{run_no}",
            model_type=model_type,
            trained_at=utcnow(),
            trained_by_id=user.id if user else None,
            data_start=panel.data_start,
            data_end=panel.data_end,
            test_start=test_dates[0].date(),
            test_end=test_dates[-1].date(),
            horizon_days=HORIZON,
            n_series=int(panel.values.shape[1]),
            n_train_rows=r.model.n_train_rows if r.model else int(train_values.notna().to_numpy().sum()),
            dataset_hash=proc_hash if is_proc else ds_hash,
            features=(artifact_model.features if artifact_model else FEATURES) if model_type in XGB_TYPES
            else ["consumption history"],
            params=params,
            metrics={k: r.metrics[k] for k in ("mae", "rmse", "wape", "bias", "n_points", "actual_total",
                                                "predicted_total")},
            feature_importance=artifact_model.feature_importance() if artifact_model else None,
            is_active=is_best,
            artifact=artifact_model.to_bytes() if artifact_model else None,
            notes=_note(model_type, best_type, v2a_w, v2b_w, v2b_eligible, folds),
        )
        db.add(mv)
        versions[model_type] = mv
    db.flush()

    # per-item backtest metrics for every candidate; drivers for every model whose forecasts we store
    drivers = {t: _drivers(c, panel, _feature_names(t)) for t, (_, c) in outputs.items() if c is not None}
    for model_type, r in results.items():
        mv = versions[model_type]
        for item in panel.item_actual.columns:
            a = actual[item].to_numpy()
            p = r.item_pred[item].to_numpy()
            m = forecast_metrics(a, p)
            db.add(ModelItemMetric(
                model_version_id=mv.id,
                consumable_id=int(item),
                actual_total=m["actual_total"],
                predicted_total=m["predicted_total"],
                mae=m["mae"] or 0.0,
                rmse=m["rmse"] or 0.0,
                wape=m["wape"],
                bias=m["bias"],
                residual_std=residual_std(a, p),
                n_points=m["n_points"],
                daily=[{"date": d.date().isoformat(), "actual": None if np.isnan(av) else round(float(av), 2),
                        "predicted": round(float(pv), 2)} for d, av, pv in zip(test_dates, a, p, strict=True)],
                drivers=drivers.get(model_type, {}).get(int(item)),
            ))

    # stored forecasts: the active model, plus the other XGBoost variant for V2A-vs-V2B comparison
    for model_type, (pred, _) in outputs.items():
        items = _to_items(pred, future_dates, panel)
        mv = versions[model_type]
        db.add_all([
            Forecast(hospital_id=hospital_id, model_version_id=mv.id, consumable_id=int(item),
                     forecast_date=d.date(), horizon=h + 1, predicted=round(float(v), 3))
            for item in items.columns
            for h, (d, v) in enumerate(zip(future_dates, items[item].to_numpy(), strict=True))
        ])
    db.flush()

    active = versions[best_type]
    return {
        "run_id": run_id,
        "active_model": active.name,
        "model_type": active.model_type,
        "data_start": panel.data_start.isoformat(),
        "data_end": panel.data_end.isoformat(),
        "test_start": test_dates[0].date().isoformat(),
        "test_end": test_dates[-1].date().isoformat(),
        "n_series": int(panel.values.shape[1]),
        "n_items": int(len(panel.item_actual.columns)),
        "candidates": {versions[k].name: results[k].metrics for k in results},
        "procedure_model": {
            "trained": "xgboost_procedure" in results,
            "consistent_improvement": v2b_eligible if "xgboost_procedure" in results else False,
            "validation_folds": folds,
            "skipped_reason": proc_reason,
            "v2a_wape": v2a_w,
            "v2b_wape": v2b_w,
            "improved": improved,
            "schedule_through": proc.schedule_end.isoformat() if proc.schedule_end else None,
        },
        "seconds": round(time.perf_counter() - started, 2),
    }


def _note(model_type: str, best_type: str, v2a_w: float | None, v2b_w: float | None, v2b_eligible: bool = True,
          folds: list[dict] | None = None) -> str | None:
    pct = lambda w: f"{w * 100:.1f}%"  # noqa: E731
    wins = f"{sum(f['v2b_wape'] < f['v2a_wape'] for f in folds)}/{len(folds)} folds" if folds else ""
    if model_type == best_type:
        if model_type == "xgboost_procedure" and v2a_w is not None:
            return f"Selected: V2B improved holdout WAPE from {pct(v2a_w)} (V2A) to {pct(v2b_w)}; better on {wins}"
        if model_type == "xgboost" and v2b_w is not None:
            if not v2b_eligible and v2b_w < v2a_w:
                return (f"Selected: V2B was better on the latest holdout ({pct(v2b_w)} vs {pct(v2a_w)}) but only on {wins} "
                        f"— not a consistent improvement, V2A kept")
            return f"Selected: lowest holdout WAPE — V2B ({pct(v2b_w)}) did not improve on V2A ({pct(v2a_w)})"
        return "Selected: lowest holdout WAPE"
    if model_type == "xgboost_procedure" and v2b_w is not None and v2a_w is not None:
        return f"Not selected: WAPE {pct(v2b_w)} vs V2A {pct(v2a_w)}; better on {wins}"
    return None


def _validation_folds(panel: Panel, proc: ProcedureData, results: dict[str, "CandidateResult"]) -> list[dict]:
    """V2A vs V2B WAPE on up to VALIDATION_FOLDS consecutive 14-day windows ending at data_end (fold 0 = holdout)."""
    folds = [{
        "test_start": panel.dates[-TEST_DAYS].date().isoformat(), "test_end": panel.dates[-1].date().isoformat(),
        "v2a_wape": results["xgboost"].metrics["wape"], "v2b_wape": results["xgboost_procedure"].metrics["wape"],
    }]
    for k in range(1, VALIDATION_FOLDS):
        end = len(panel.dates) - k * TEST_DAYS
        start = end - TEST_DAYS
        if start < MIN_DAYS - TEST_DAYS:  # keep at least the minimum training history
            break
        train_v = panel.values.iloc[:start]
        test_dates = panel.dates[start:end]
        actual = panel.item_actual.loc[test_dates].to_numpy().ravel()
        fold = {"test_start": test_dates[0].date().isoformat(), "test_end": test_dates[-1].date().isoformat()}
        try:
            for t, key in (("xgboost", "v2a_wape"), ("xgboost_procedure", "v2b_wape")):
                model = XGBForecaster.fit(train_v, panel.series, proc=proc if t == "xgboost_procedure" else None)
                pred, _ = model.forecast(train_v, panel.series, TEST_DAYS)
                fold[key] = forecast_metrics(actual, _to_items(pred, test_dates, panel).to_numpy().ravel())["wape"]
        except ValueError:
            break
        if fold.get("v2a_wape") is None or fold.get("v2b_wape") is None:
            continue
        folds.append(fold)
    return folds


def _feature_names(model_type: str) -> list[str]:
    return [*FEATURES, *PROC_FEATURES] if model_type == "xgboost_procedure" else FEATURES


def _drivers(contribs: np.ndarray, panel: Panel, features: list[str]) -> dict[int, dict]:
    """Sum feature contributions (units) over departments and horizon days, per item and horizon."""
    names = [*features, "base"]
    items = panel.series["consumable_id"].to_numpy()
    out: dict[int, dict] = {}
    for item in np.unique(items):
        cols = items == item
        per = {}
        for h in DRIVER_HORIZONS:
            tot = contribs[:h, cols, :].sum(axis=(0, 1))
            per[str(h)] = {n: round(float(v), 2) for n, v in zip(names, tot, strict=True)}
        out[int(item)] = per
    return out


def prune_old_forecasts(db: Session, hospital_id: int, keep_runs: int = 10) -> None:
    """Keep forecasts only for the most recent runs (model versions and metrics are kept forever)."""
    recent = db.scalars(
        select(ModelVersion.id).where(ModelVersion.hospital_id == hospital_id)
        .order_by(ModelVersion.trained_at.desc()).limit(keep_runs * len(CANDIDATES))
    ).all()
    if recent:
        db.execute(delete(Forecast).where(Forecast.hospital_id == hospital_id, Forecast.model_version_id.not_in(recent)))


def has_active_model(db: Session, hospital_id: int) -> bool:
    return bool(db.scalar(select(ModelVersion.id).where(ModelVersion.hospital_id == hospital_id,
                                                          ModelVersion.is_active.is_(True))))


__all__ = ["run_training", "select_best", "consistent_improvement", "InsufficientDataError", "has_active_model", "prune_old_forecasts",
           "TEST_DAYS", "HORIZON", "ProcedureData", "timedelta"]
