"""V3 — stockout risk: projection, metrics, features (no leakage), simulator, training/backtest, API, alerts."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import business_today
from app.ml.pipeline import run_training as run_forecast_training
from app.models import (
    Alert,
    AlertStatus,
    AlertType,
    Consumable,
    RiskModelVersion,
    StockoutPrediction,
    SupplierProduct,
)
from app.risk import engine
from app.risk import metrics as M
from app.risk.features import AHEAD, FEATURES, HORIZON, ItemStatic, build
from app.risk.history import load_history
from app.risk.pipeline import event_outcomes, run_training
from app.risk.projection import Batch, project, replenishment
from app.risk.simulate import SimConfig, estimate_params, simulate_item
from app.services import alerts, stock
from tests.conftest import as_role

TZ = ZoneInfo(settings.TIMEZONE)
FAST = SimConfig(replicates=2, days=160, min_rows=6000)


# ---------------------------------------------------------------- V3.1 projection (pure)


def test_projection_counts_days_and_shortage():
    start = date(2026, 1, 1)
    p = project([Batch(100, None)], [10.0] * 30, start)
    assert p.days_of_stock_remaining == 10 and p.stockout_date == date(2026, 1, 11)
    assert p.shortage(14) == pytest.approx(40)  # days 11–14 unmet
    assert p.days[9].stock_end == 0 and p.days[10].unmet == 10


def test_projection_uses_fefo_and_loses_expired_stock():
    start = date(2026, 1, 1)
    # 50 units expire on day 3 (usable through Jan 3); demand 10/day only uses 30 of them first
    p = project([Batch(50, date(2026, 1, 3)), Batch(40, None)], [10.0] * 30, start)
    assert p.expired() == pytest.approx(20)
    assert p.days_of_stock_remaining == 7  # 30 used from the short-dated lot + 40 from the other
    assert project([Batch(100, None)], [0.0] * 30, start).stockout_date is None


def test_replenishment_order_by_and_in_time():
    today = date(2026, 1, 1)
    p = project([Batch(100, None)], [10.0] * 30, today)  # stockout Jan 11
    r = replenishment(p, today, 5)
    assert r.order_by_date == date(2026, 1, 6) and r.can_replenish_in_time and not r.order_overdue
    r = replenishment(p, today, 14)
    assert r.can_replenish_in_time is False and r.order_overdue
    r = replenishment(project([Batch(1000, None)], [1.0] * 30, today), today, 5)
    assert r.can_replenish_in_time is None and r.order_by_date is None
    assert replenishment(p, today, None).lead_time_days is None


# ---------------------------------------------------------------- V3.4 metrics (pure)


def test_metrics_known_values():
    y = np.array([1, 0, 1, 0])
    s = np.array([0.9, 0.8, 0.7, 0.1])
    assert M.average_precision(y, s) == pytest.approx(0.5 * 1 + 0.5 * 2 / 3)
    assert M.roc_auc(y, s) == pytest.approx(0.75)
    c = M.confusion(y, s >= 0.75)
    assert c == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert M.prf(c)["precision"] == 0.5 and M.prf(c)["recall"] == 0.5
    assert M.threshold_for_recall(y, s, 1.0) == pytest.approx(0.7)  # catches both positives
    assert M.threshold_for_recall(y, s, 0.5) == pytest.approx(0.9)
    assert M.average_precision(np.zeros(3), np.ones(3)) is None


def test_event_recall_is_lead_time_aware():
    e = pd.Timestamp("2026-01-20")
    ds = pd.DataFrame({"consumable_id": [1, 1, 1], "origin": [e - pd.Timedelta(days=d) for d in (5, 2, 1)],
                       "available": [True, True, True]})
    # warned only 2 days ahead; lead time 3 → caught but not in time
    ev = event_outcomes([(1, e)], ds, np.array([False, True, True]), {1: 3})[0]
    assert ev.caught and not ev.caught_in_time and ev.warning_days == 2
    ev = event_outcomes([(1, e)], ds, np.array([True, False, False]), {1: 3})[0]
    assert ev.caught_in_time and ev.warning_days == 5
    ev = event_outcomes([(1, e)], ds, np.array([False, False, False]), {1: 3})[0]
    assert not ev.caught and ev.first_warning is None
    s = M.summarize(np.array([1.0]), np.array([0.2]), np.array([False]), [ev])
    assert s["fn"] == 1 and s["event_recall"] == 0 and s["lead_time_recall"] == 0


# ---------------------------------------------------------------- V3.2 features (pure)


def _frame(days=80, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    usable = np.maximum(1000 - np.arange(days) * 15, 0).astype(float)  # runs out on day 67
    f = pd.DataFrame({"consumed": rng.poisson(12, days).astype(float), "usable_end": usable,
                      "expiring_14": np.zeros(days), "received": np.zeros(days), "stockout": usable <= 0,
                      "kit": np.zeros(days)}, index=idx)
    f.iloc[10, f.columns.get_loc("received")] = 500.0
    return f


def test_features_use_only_data_up_to_the_origin():
    """MANDATORY: changing anything after the origin (consumption, stock, receipts, stockouts) cannot change its features."""
    f = _frame()
    st = ItemStatic(100, 3, 1)
    t = 50
    before = build(f, st, origins=np.array([t]))
    g = f.copy()
    g.iloc[t + 1:, g.columns.get_loc("consumed")] = 999.0
    g.iloc[t + 1:, g.columns.get_loc("usable_end")] = 0.0
    g.iloc[t + 1:, g.columns.get_loc("received")] = 777.0
    g.iloc[t + 1:, g.columns.get_loc("stockout")] = True
    after = build(g, st, origins=np.array([t]))
    pd.testing.assert_frame_equal(before[FEATURES], after[FEATURES])
    assert before["label"].iloc[0] == 0 and after["label"].iloc[0] == 1  # only the label sees the future
    # the procedure plan after t is known in advance and may change features (as in V2B)
    h = f.copy()
    h.iloc[t + 1:t + 8, h.columns.get_loc("kit")] = 50.0
    planned = build(h, st, origins=np.array([t]))
    assert planned["procedure_demand_14"].iloc[0] == pytest.approx(350)
    assert planned["forecast_7"].iloc[0] > before["forecast_7"].iloc[0]


def test_labels_and_availability():
    f = _frame()
    st = ItemStatic(100, 3, 1)
    first_out = int(np.flatnonzero(f["stockout"].to_numpy())[0])
    r = build(f, st, origins=np.array([first_out - 3, first_out - HORIZON - 1, first_out]))
    assert r["label"].tolist()[:2] == [1.0, 0.0]
    assert r["first_stockout_offset"].iloc[0] == 3
    assert bool(r["available"].iloc[2]) is False  # already out of stock: not a prediction row
    assert r["projected_cover_days"].iloc[1] <= AHEAD + 1


def test_simulator_is_deterministic_and_produces_stockouts(db, world):
    _ledger_world(db, world)
    it = next(iter(load_history(db, world["hospital"].id).items.values()))
    p = estimate_params(it, 28)
    a = simulate_item(p, SimConfig(days=200), seed=7)
    b = simulate_item(p, SimConfig(days=200), seed=7)
    pd.testing.assert_frame_equal(a, b)
    assert a["stockout"].sum() > 0 and a["received"].sum() > 0
    assert (a["usable_end"] >= 0).all()


# ---------------------------------------------------------------- ledger world


def _at(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour, 0), tzinfo=TZ)


def _ledger_world(db, world, days=70, end_stock=60):
    """Real ledger via the stock service: daily issues, reorder at the reorder level with a 3-day lead time,
    every third delivery 4 days late (→ stockouts), no reorders in the last 9 days (→ low stock today)."""
    user, gloves, sup = world["users"]["admin"], world["gloves"], world["supplier"]
    gloves.reorder_level, gloves.max_level = 200, 800
    masks = Consumable(hospital_id=world["hospital"].id, sku="MSK", name="Masks", unit="piece", reorder_level=50,
                       max_level=2000)
    db.add(masks)
    db.flush()
    db.add_all([SupplierProduct(supplier_id=sup.id, consumable_id=gloves.id, unit_price=40, lead_time_days=3, moq=1,
                                is_preferred=True),
                SupplierProduct(supplier_id=sup.id, consumable_id=masks.id, unit_price=5, lead_time_days=3, moq=1,
                                is_preferred=True)])
    today = business_today()
    start = today - timedelta(days=days)
    rng = np.random.default_rng(3)
    exp = today + timedelta(days=700)
    stock.receive(db, user, gloves, 600, "G0", exp, sup, 40, None, None, at=_at(start, 7))
    stock.receive(db, user, masks, 1500, "M0", exp, sup, 5, None, None, at=_at(start, 7))
    pending, orders = None, 0
    for d in range(1, days):
        day = start + timedelta(days=d)
        if pending and pending[0] <= day:
            stock.receive(db, user, gloves, pending[1], f"G{d}", exp, sup, 40, None, None, at=_at(day, 7))
            pending = None
        usable = stock.stock_levels(db, gloves.hospital_id, [gloves.id]).get(gloves.id)
        u = usable.usable if usable else 0
        q = min(int(rng.poisson(40 if day.weekday() < 5 else 24)), u)
        if q:
            stock.issue(db, user, gloves, q, world["ortho"], None, None, at=_at(day, 10), today=day)
        m = int(rng.poisson(4))
        if m:
            stock.issue(db, user, masks, m, world["icu"], None, None, at=_at(day, 11), today=day)
        u -= q
        if u <= gloves.reorder_level and pending is None and d < days - 9:
            orders += 1
            lead = 3 + (4 if orders % 3 == 0 else 0)
            pending = (day + timedelta(days=lead), gloves.max_level - u)
    u = stock.stock_levels(db, gloves.hospital_id, [gloves.id])[gloves.id].usable
    if u < end_stock:
        stock.receive(db, user, gloves, end_stock - u, "TOPUP", exp, sup, 40, None, None,
                      at=_at(today - timedelta(days=1), 18))
    db.commit()
    return gloves, masks


def _trained(db, world):
    gloves, masks = _ledger_world(db, world)
    run_forecast_training(db, world["hospital"].id)
    run_training(db, world["hospital"].id, cfg=FAST)
    alerts.evaluate(db, world["hospital"].id)
    db.commit()
    return gloves, masks


def test_history_rebuilds_usable_stock_and_stockouts(db, world):
    gloves, _ = _ledger_world(db, world)
    lh = load_history(db, world["hospital"].id)
    f = lh.items[gloves.id].frame
    assert f["stockout"].sum() > 0  # the late deliveries caused real stockouts
    assert lh.items[gloves.id].lead_time_days == 3
    # end-of-yesterday usable stock matches the batches (nothing moved today)
    assert f["usable_end"].iloc[-1] == stock.stock_levels(db, world["hospital"].id, [gloves.id])[gloves.id].usable
    assert (f.loc[f["stockout"], "usable_end"].min()) == 0


@pytest.mark.usefixtures("frozen_day")  # calendar-sensitive fixture (see conftest.frozen_day; V0–V10 audit T-1)
def test_training_backtest_and_registry(db, world):
    gloves, masks = _trained(db, world)
    runs = db.scalars(select(RiskModelVersion)).all()
    assert {m.model_type for m in runs} == {"xgboost_classifier", "cover_rule", "reorder_rule"}
    assert len({m.run_id for m in runs}) == 1 and sum(m.is_active for m in runs) == 1
    for m in runs:
        bt = m.metrics["backtest"]
        for k in ("precision", "recall", "f1", "pr_auc", "fp", "fn", "event_recall", "lead_time_recall", "n_events"):
            assert k in bt
        assert m.warn_threshold <= m.high_threshold
        assert m.evaluation["events"] and all("caught_in_time" in e for e in m.evaluation["events"])
    xgb = next(m for m in runs if m.model_type == "xgboost_classifier")
    assert xgb.artifact and set(xgb.feature_importance) == set(FEATURES)
    assert xgb.params["simulation"]["synthetic"] is True
    # served model follows the guard: ML unless its PR-AUC is below the cover rule's
    rule = next(m for m in runs if m.model_type == "cover_rule")
    ml_ap, rule_ap = xgb.metrics["backtest"]["pr_auc"], rule.metrics["backtest"]["pr_auc"]
    assert (xgb.is_active) == (ml_ap is None or rule_ap is None or ml_ap >= rule_ap)

    preds = engine.latest(db, world["hospital"].id)
    g, m = preds[gloves.id], preds[masks.id]
    assert g.usable_stock <= 80 and g.expected_stockout_date is not None and g.days_of_stock_remaining <= 3
    assert g.shortage_quantity > 0 and g.lead_time_days == 3 and len(g.projection) == AHEAD
    assert g.risk_level in ("HIGH", "MEDIUM") and g.probability > m.probability
    assert m.risk_level == "LOW" and m.expected_stockout_date is None
    assert any("run out on" in r for r in g.reasons)


def test_ml_worse_than_rule_serves_the_rule(db, world, monkeypatch):
    _ledger_world(db, world)
    run_forecast_training(db, world["hospital"].id)
    from app.risk import pipeline
    real = pipeline.RiskXGB.predict
    monkeypatch.setattr(pipeline.RiskXGB, "predict", lambda self, X: 1 - real(self, X))  # anti-model
    r = run_training(db, world["hospital"].id, cfg=FAST)
    assert r["model_type"] == "cover_rule" and "Cover rule served" in r["selection"]
    active = db.scalar(select(RiskModelVersion).where(RiskModelVersion.is_active.is_(True)))
    assert active.model_type == "cover_rule" and active.params["cover_rates"]
    monkeypatch.undo()
    preds = engine.refresh(db, world["hospital"].id)
    assert all(p.probability_source == "cover_rule" for p in preds)


def test_insufficient_history_is_rejected(db, world):
    _ledger_world(db, world, days=30)
    with pytest.raises(ValueError, match="at least"):
        run_training(db, world["hospital"].id, cfg=FAST)


# ---------------------------------------------------------------- API + alerts


def test_api_before_training_explains_and_train_permissions(login, world, db):
    _ledger_world(db, world)
    r = as_role(login, "viewer").get("/api/stockout-risks").json()
    assert r["items"] == [] and "forecasting" in r["message"]
    for role in ("viewer", "department_manager"):
        assert as_role(login, role).post("/api/stockout-risks/train").status_code == 403
    c = as_role(login, "procurement_manager")
    c.post("/api/forecasts/train")
    assert "risk model" in c.get("/api/stockout-risks").json()["message"]
    assert c.post("/api/stockout-risks/refresh").status_code == 404


def test_api_overview_detail_isolation_and_alert_lifecycle(login, world, db):
    gloves, masks = _ledger_world(db, world)
    c = as_role(login, "inventory_manager")
    c.post("/api/forecasts/train")
    t = c.post("/api/stockout-risks/train")
    assert t.status_code == 200, t.text
    assert t.json()["n_events"] >= 1 and set(t.json()["backtest"]) == {"xgboost_classifier", "cover_rule", "reorder_rule"}

    ov = as_role(login, "viewer").get("/api/stockout-risks").json()
    assert ov["counts"]["total"] == 2 and ov["model"]["is_active"]
    top = ov["items"][0]
    assert top["consumable_id"] == gloves.id and top["risk_level"] in ("HIGH", "MEDIUM") and top["main_reason"]
    d = as_role(login, "viewer").get(f"/api/stockout-risks/{gloves.id}").json()
    assert len(d["projection"]) == AHEAD and d["reasons"] and d["history"] and d["risk_model"].startswith(("stockout_xgb_", "cover_rule_"))
    assert d["expected_stockout_date"] and d["order_by_date"]

    # risk alert raised for the in-stock, at-risk item; V1 alerts untouched
    active = db.scalars(select(Alert).where(Alert.status != AlertStatus.RESOLVED)).all()
    risk_alerts = [a for a in active if a.alert_type == AlertType.STOCKOUT_RISK]
    assert [a.consumable_id for a in risk_alerts] == [gloves.id]
    assert "probability of a stockout" in risk_alerts[0].message
    assert any(a.alert_type == AlertType.LOW_STOCK and a.consumable_id == gloves.id for a in active)

    # isolation
    other = login("admin@other.demo")
    assert other.get(f"/api/stockout-risks/{gloves.id}").status_code == 404
    assert other.get("/api/stockout-risks").json()["items"] == []
    assert other.get("/api/stockout-risks/models").json() == []

    # a large delivery lowers the risk immediately and auto-resolves the risk alert
    c = as_role(login, "inventory_manager")
    r = c.post("/api/inventory/receive", json={"consumable_id": gloves.id, "quantity": 3000, "lot_number": "BIG",
                                               "expiry_date": (business_today() + timedelta(days=700)).isoformat(),
                                               "supplier_id": world["supplier"].id})
    assert r.status_code in (200, 201), r.text
    after = c.get(f"/api/stockout-risks/{gloves.id}").json()
    assert after["risk_level"] == "LOW" and after["probability"] < d["probability"]
    assert after["expected_stockout_date"] is None and after["shortage_quantity"] == 0
    db.expire_all()
    ra = db.scalars(select(Alert).where(Alert.alert_type == AlertType.STOCKOUT_RISK)).all()
    assert ra and all(a.status == AlertStatus.RESOLVED for a in ra)
    n = db.scalar(select(StockoutPrediction.id).where(StockoutPrediction.consumable_id == gloves.id)
                  .order_by(StockoutPrediction.id.desc()).limit(1))
    assert n is not None
    models = c.get("/api/stockout-risks/models").json()
    detail = c.get(f"/api/stockout-risks/models/{models[0]['id']}").json()
    assert len(detail["run_candidates"]) == 3 and str(gloves.id) in {str(k) for k in detail["item_names"]}


def test_out_of_stock_item_is_high_with_v1_alert_only(db, world):
    gloves, _ = _trained(db, world)
    b = db.scalars(select(stock.StockBatch).where(stock.StockBatch.consumable_id == gloves.id,
                                                  stock.StockBatch.quantity > 0)).all()
    for batch in b:
        stock.wastage(db, world["users"]["admin"], batch, gloves, batch.quantity, "test")
    alerts.evaluate(db, world["hospital"].id, [gloves.id])
    db.commit()
    p = engine.latest(db, world["hospital"].id, [gloves.id])[gloves.id]
    assert p.usable_stock == 0 and p.probability == 1.0 and p.risk_level == "HIGH"
    assert p.reasons[0].startswith("Out of stock now")
    active = {a.alert_type for a in db.scalars(select(Alert).where(Alert.consumable_id == gloves.id,
                                                                   Alert.status != AlertStatus.RESOLVED))}
    assert AlertType.OUT_OF_STOCK in active and AlertType.STOCKOUT_RISK not in active


def test_lead_time_floor_never_lets_an_unreplenishable_item_show_low():
    today = date(2026, 1, 1)
    p = project([Batch(40, None)], [10.0] * 30, today)  # runs out on day 5 (4 days covered)
    assert engine.lead_time_floor(p.days_of_stock_remaining, replenishment(p, today, 7), today) == "HIGH"
    assert engine.lead_time_floor(p.days_of_stock_remaining, replenishment(p, today, 3), today) == "MEDIUM"  # order by Jan 2
    big = project([Batch(1000, None)], [10.0] * 30, today)
    assert engine.lead_time_floor(big.days_of_stock_remaining, replenishment(big, today, 3), today) is None
    assert engine.lead_time_floor(p.days_of_stock_remaining, replenishment(p, today, None), today) is None


def test_floor_policy_is_measured_in_the_backtest(db, world):
    _trained(db, world)
    xgb = db.scalar(select(RiskModelVersion).where(RiskModelVersion.model_type == "xgboost_classifier"))
    fl = xgb.metrics["backtest_with_floor"]
    bt = xgb.metrics["backtest"]
    assert fl["tp"] + fl["fn"] == bt["tp"] + bt["fn"]  # same rows
    assert fl["recall"] >= bt["recall"] and fl["fp"] >= bt["fp"]  # floor only adds warnings
