"""V3.2 + V3.4 — train the stockout-risk model and evaluate it on the hospital's own ledger history.

    ledger history ──(first 28 days)──► simulator parameters ──► synthetic histories ──► features ──► XGBoost
                                                                  (train + validation replicates)     │
    ledger history ──(every later day)──► point-in-time features ──► backtest: reorder_rule vs cover_rule vs XGBoost
                                                                       precision / recall / F1 / PR-AUC / FP / FN /
                                                                       Brier / event recall / lead-time-aware recall
Serving rule: the XGBoost model is served unless it is WORSE than the deterministic cover rule on this hospital's
own history (PR-AUC); then the cover rule is served. The V1 reorder rule is a baseline only.
Thresholds are chosen on the synthetic validation replicate, never on the ledger backtest: MEDIUM ("warn") is the
highest probability that still catches TARGET_RECALL of validation stockouts (missing a stockout costs more than an
extra warning); HIGH is where validation precision reaches HIGH_PRECISION (at least 0.5).
"""

import hashlib
import time
import uuid
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import RiskModelVersion
from app.risk import metrics as M
from app.risk.features import FEATURE_LABELS, FEATURES, HORIZON, MIN_HISTORY, ItemStatic, build
from app.risk.history import LedgerHistory, load_history
from app.risk.model import RiskXGB, cover_probability, fit_cover_rates, reorder_score
from app.risk.simulate import SimConfig, estimate_params, simulate_item

PARAM_DAYS = MIN_HISTORY  # simulator parameters come only from these first days (before the backtest window)
MIN_DAYS = MIN_HISTORY + HORIZON + 7
FLOOR_DAYS = 2  # lead-time floor: MEDIUM when cover exceeds the lead time by ≤ this many days, HIGH when it is shorter
TARGET_RECALL = 0.8  # warn threshold catches 80 % of validation stockouts
HIGH_PRECISION = 0.6  # HIGH risk: probability where validation precision reaches 60 % (at least 0.5)
DEFAULT_LEAD = 7
THRESHOLD_RULE = f"warn: recall {TARGET_RECALL:.0%} on synthetic validation; high: precision {HIGH_PRECISION:.0%} (min 0.5)"


def _static(it) -> ItemStatic:
    return ItemStatic(float(it.reorder_level), it.lead_time_days, it.department_id)


def simulated_dataset(lh: LedgerHistory, cfg: SimConfig, seed_offset: int, replicates: int) -> pd.DataFrame:
    frames = []
    for idx, it in enumerate(sorted(lh.items.values(), key=lambda i: i.sku)):
        p = estimate_params(it, PARAM_DAYS)
        if p is None:
            continue
        for r in range(replicates):
            frame = simulate_item(p, cfg, seed=seed_offset + 1000 * idx + r)
            origins = np.arange(cfg.burn_in, cfg.burn_in + cfg.days)
            f = build(frame, _static(it), origins=origins)
            f = f[f["available"] & f["label"].notna()]
            f["consumable_id"] = it.consumable_id
            frames.append(f)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[*FEATURES, "label"])


def ledger_dataset(lh: LedgerHistory) -> pd.DataFrame:
    """Point-in-time features for every item and every origin with enough history (all origins; labels may be NaN)."""
    frames = []
    for it in lh.items.values():
        if len(it.frame) < MIN_HISTORY + 1:
            continue
        f = build(it.frame, _static(it), kit_ahead=it.kit_future)
        f["consumable_id"] = it.consumable_id
        f["origin"] = f.index
        frames.append(f.reset_index(drop=True))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def stockout_events(lh: LedgerHistory, first_origin: pd.Timestamp) -> list[tuple[int, pd.Timestamp]]:
    """(item, first day of each stockout episode) that the backtest could have warned about."""
    out = []
    for it in lh.items.values():
        s = it.frame["stockout"].to_numpy(dtype=bool)
        idx = it.frame.index
        for p in range(1, len(s)):
            if s[p] and not s[p - 1] and idx[p] > first_origin:
                out.append((it.consumable_id, idx[p]))
    return out


def event_outcomes(events, ds: pd.DataFrame, flag: np.ndarray, leads: dict[int, int | None]) -> list[M.Event]:
    out = []
    d = ds.assign(flag=flag)
    d = d[d["available"]]
    for cid, e in events:
        w = d[(d["consumable_id"] == cid) & (d["origin"] >= e - pd.Timedelta(days=HORIZON)) & (d["origin"] < e)]
        warned = w[w["flag"]]
        lead = leads.get(cid)
        need = lead if lead is not None else DEFAULT_LEAD
        first = warned["origin"].min() if len(warned) else None
        days = int((e - first).days) if first is not None else None
        out.append(M.Event(cid, e.date(), lead, first.date() if first is not None else None, days,
                           caught=len(warned) > 0, caught_in_time=days is not None and days >= need))
    return out


def lead_floor(X: pd.DataFrame) -> np.ndarray:
    """Deterministic floor used at serving time: little or no cover beyond the supplier lead time."""
    gap = X["cover_minus_lead_time"].to_numpy(dtype=float)
    return np.nan_to_num(gap, nan=np.inf) <= FLOOR_DAYS


def _events_json(events: list[M.Event]) -> list[dict]:
    return [{"consumable_id": e.consumable_id, "date": str(e.date), "lead_time": e.lead_time,
             "first_warning": str(e.first_warning) if e.first_warning else None,
             "warning_days": e.warning_days, "caught": e.caught, "caught_in_time": e.caught_in_time} for e in events]


def _json(v):
    if isinstance(v, float | np.floating):
        return None if np.isnan(v) else round(float(v), 4)
    return v


def _clean(d: dict) -> dict:
    return {k: (_clean(v) if isinstance(v, dict) else _json(v)) for k, v in d.items()}


def run_training(db: Session, hospital_id: int, user_id: int | None = None, cfg: SimConfig | None = None) -> dict:
    started = time.perf_counter()
    cfg = cfg or SimConfig()
    lh = load_history(db, hospital_id)
    if lh.data_start is None or (lh.data_end - lh.data_start).days + 1 < MIN_DAYS:
        raise ValueError(f"Need at least {MIN_DAYS} days of stock history to train the stockout-risk model.")

    # --- training data: synthetic histories (train + separate validation replicate)
    per_rep = max(len(lh.items), 1) * cfg.days
    reps = min(max(cfg.replicates, -(-cfg.min_rows // per_rep)), cfg.max_replicates)
    train = simulated_dataset(lh, cfg, seed_offset=0, replicates=reps)
    val = simulated_dataset(lh, cfg, seed_offset=500_000, replicates=max(1, reps // 3))
    if train.empty or train["label"].sum() < 20 or val["label"].sum() < 5:
        raise ValueError("Not enough simulated stockouts to train the risk model (check reorder levels and demand).")
    y_tr, y_val = train["label"].to_numpy(), val["label"].to_numpy()
    model = RiskXGB.fit(train, y_tr)
    rates = fit_cover_rates(train, y_tr)

    p_val = {"xgboost_classifier": model.predict(val), "cover_rule": cover_probability(val, rates),
             "reorder_rule": reorder_score(val)}
    thresholds = {}
    for k, p in p_val.items():
        if k == "reorder_rule":
            thresholds[k] = (0.5, 1 / 1.5)  # warn at the reorder level, HIGH at half of it (V1 semantics)
            continue
        warn = M.threshold_for_recall(y_val, p, TARGET_RECALL)
        high = max(M.threshold_for_precision(y_val, p, HIGH_PRECISION, floor=0.5), warn)
        thresholds[k] = (warn, high)
    validation = {k: M.summarize(y_val, p, p >= thresholds[k][0], [], prob=None if k == "reorder_rule" else p)
                  for k, p in p_val.items()}

    # --- backtest on the hospital's own ledger history (never used for training or thresholds)
    ds = ledger_dataset(lh)
    rows = ds[ds["available"] & ds["label"].notna()].reset_index(drop=True)
    y_bt = rows["label"].to_numpy()
    first_origin = ds["origin"].min() if not ds.empty else pd.Timestamp(lh.data_end)
    events = stockout_events(lh, first_origin)
    leads = {it.consumable_id: it.lead_time_days for it in lh.items.values()}
    scorers = {"xgboost_classifier": model.predict, "cover_rule": lambda X: cover_probability(X, rates),
               "reorder_rule": reorder_score}
    backtest, event_detail, curves, policy, policy_events = {}, {}, {}, {}, {}
    for k, fn in scorers.items():
        s_rows = fn(rows) if len(rows) else np.array([])
        s_all = fn(ds) if len(ds) else np.array([])
        ev = event_outcomes(events, ds, s_all >= thresholds[k][0], leads)
        backtest[k] = M.summarize(y_bt, s_rows, s_rows >= thresholds[k][0], ev,
                                  prob=None if k == "reorder_rule" else s_rows)
        if k != "reorder_rule":  # the served warning policy = probability threshold OR the lead-time floor
            f_rows = (s_rows >= thresholds[k][0]) | lead_floor(rows)
            f_all = (s_all >= thresholds[k][0]) | lead_floor(ds)
            policy_events[k] = event_outcomes(events, ds, f_all, leads)
            policy[k] = M.summarize(y_bt, s_rows, f_rows, policy_events[k], prob=s_rows)
        event_detail[k] = ev
        curves[k] = M.pr_curve(y_bt, s_rows) if len(rows) and y_bt.sum() else []

    ml_ap, rule_ap = backtest["xgboost_classifier"]["pr_auc"], backtest["cover_rule"]["pr_auc"]
    if ml_ap is not None and rule_ap is not None and ml_ap < rule_ap:
        served = "cover_rule"
        reason = (f"Cover rule served: XGBoost PR-AUC {ml_ap:.3f} was below the cover rule's {rule_ap:.3f} on this "
                  f"hospital's history.")
    else:
        served = "xgboost_classifier"
        reason = ("XGBoost served: PR-AUC " + (f"{ml_ap:.3f} vs cover rule {rule_ap:.3f}" if ml_ap is not None and rule_ap is not None
                                               else "not measurable on this history (no stockouts in the backtest window); "
                                                    "selected on synthetic validation") + " on this hospital's history.")

    # --- registry
    n_runs = db.scalar(select(func.count(func.distinct(RiskModelVersion.run_id)))
                       .where(RiskModelVersion.hospital_id == hospital_id)) or 0
    version = n_runs + 1
    run_id = uuid.uuid4().hex[:12]
    h = hashlib.sha256()
    for it in sorted(lh.items.values(), key=lambda i: i.consumable_id):
        h.update(str(it.consumable_id).encode())
        h.update(it.frame[["consumed", "usable_end", "received"]].to_numpy(dtype=float).round(3).tobytes())
    h.update(repr(cfg).encode())
    dataset_hash = h.hexdigest()
    names = {"xgboost_classifier": "stockout_xgb", "cover_rule": "cover_rule", "reorder_rule": "reorder_rule"}
    calib = M.calibration(y_bt, model.predict(rows)) if len(rows) else []
    db.execute(update(RiskModelVersion).where(RiskModelVersion.hospital_id == hospital_id).values(is_active=False))
    common = dict(
        hospital_id=hospital_id, run_id=run_id, trained_by_id=user_id, horizon_days=HORIZON,
        data_start=lh.data_start, data_end=lh.data_end,
        eval_start=first_origin.date() if not ds.empty else None,
        eval_end=(lh.data_end - timedelta(days=HORIZON)), n_train_rows=len(train), n_positive_train=int(y_tr.sum()),
        dataset_hash=dataset_hash,
    )
    stored = {}
    for k in scorers:
        mv = RiskModelVersion(
            **common, name=f"{names[k]}_v{version}", model_type=k,
            features=FEATURES if k == "xgboost_classifier" else (["projected_cover_days"] if k == "cover_rule" else ["stock_to_reorder"]),
            params={
                "simulation": {**cfg.__dict__, "param_days": PARAM_DAYS, "synthetic": True},
                "horizon_days": HORIZON, "min_history_days": MIN_HISTORY, "threshold_rule": THRESHOLD_RULE,
                **({"xgboost": PARAMS_DUMP} if k == "xgboost_classifier" else {}),
                **({"cover_rates": [round(r, 4) for r in rates]} if k == "cover_rule" else {}),
            },
            metrics={"backtest": _clean(backtest[k]), "validation": _clean(validation[k]),
                     **({"backtest_with_floor": _clean(policy[k])} if k in policy else {})},
            evaluation={
                "events": _events_json(event_detail[k]),
                "events_with_floor": _events_json(policy_events[k]) if k in policy_events else [],
                "pr_curve": [_clean(p) for p in curves[k]],
                "calibration": [_clean(c) for c in calib] if k == "xgboost_classifier" else [],
                "selection": reason,
            },
            feature_importance=model.importance() if k == "xgboost_classifier" else None,
            warn_threshold=float(thresholds[k][0]), high_threshold=float(thresholds[k][1]),
            is_active=(k == served), artifact=model.to_bytes() if k == "xgboost_classifier" else None,
            notes=("Serving — " + reason) if k == served else ("Baseline (V1 alert rule)" if k == "reorder_rule" else "Not served"),
        )
        db.add(mv)
        stored[k] = mv
    db.flush()
    return {
        "run_id": run_id, "active_model": stored[served].name, "model_type": served, "selection": reason,
        "data_start": lh.data_start, "data_end": lh.data_end, "n_items": len(lh.items),
        "n_train_rows": len(train), "n_positive_train": int(y_tr.sum()), "n_backtest_rows": int(len(rows)),
        "n_backtest_positive": int(y_bt.sum()) if len(rows) else 0, "n_events": len(events),
        "backtest": {k: _clean(v) for k, v in backtest.items()},
        "backtest_with_floor": {k: _clean(v) for k, v in policy.items()},
        "thresholds": {k: {"warn": round(v[0], 4), "high": round(v[1], 4)} for k, v in thresholds.items()},
        "seconds": round(time.perf_counter() - started, 2),
    }


PARAMS_DUMP = {"rounds": 300, "eta": 0.05, "max_depth": 4, "min_child_weight": 5, "subsample": 0.8, "colsample_bytree": 0.8}
FEATURE_LABELS = FEATURE_LABELS  # re-export for the API
