"""V2B — procedure-aware forecasting: features, leakage, cancellations, fallback, selection, fingerprint."""

from datetime import timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.core.security import business_today
from app.db.base import utcnow
from app.ml.data import load_panel
from app.ml.features import feature_frame
from app.ml.pipeline import consistent_improvement, run_training, select_best
from app.ml.procedures import PROC_FEATURES, load_procedure_data
from app.models import (
    Forecast,
    ModelVersion,
    MovementType,
    ProcedureItemMapping,
    ProcedureSchedule,
    ProcedureStatus,
    ProcedureType,
    StockMovement,
)

QTY = 4  # kit: gloves per procedure


def _proc_type(db, world, code="HERN", active=True):
    t = ProcedureType(hospital_id=world["hospital"].id, department_id=world["ortho"].id, code=code, name=code,
                      is_active=active)
    db.add(t)
    db.flush()
    db.add(ProcedureItemMapping(hospital_id=world["hospital"].id, procedure_type_id=t.id,
                                consumable_id=world["gloves"].id, quantity_per_procedure=Decimal(QTY)))
    db.flush()
    return t


def _schedule(db, world, t, day, count, status=None):
    today = business_today()
    status = status or (ProcedureStatus.COMPLETED if day < today else ProcedureStatus.SCHEDULED)
    row = ProcedureSchedule(hospital_id=world["hospital"].id, procedure_type_id=t.id, department_id=world["ortho"].id,
                            scheduled_date=day, count=count, status=status)
    db.add(row)
    db.flush()
    return row


def _procedure_world(db, world, days=70, driven=True, future_days=30, seed=3):
    """Gloves consumption in ORTHO = procedures × ~QTY (+ noise) when `driven`; ICU independent background use.

    With driven=False, procedure counts carry no information about consumption (both have their own randomness)."""
    rng = np.random.default_rng(seed)
    t = _proc_type(db, world)
    today = business_today()
    week_factor = {w: rng.choice([0.4, 1.0, 1.0, 1.6]) for w in range(-20, 10)}
    rows = []
    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        # driven: fewer procedures on Sundays and consumption follows procedures;
        # not driven: counts are pure noise — independent of weekday and of consumption
        sunday = 0.3 if driven and day.weekday() == 6 else 1
        count = int(rng.poisson(5 * week_factor[(day.toordinal() // 7) % 30 - 20] * sunday))
        if count:
            _schedule(db, world, t, day, count)
        weekday = 0.5 if day.weekday() >= 5 else 1.1
        ortho = int(rng.poisson(count * QTY * 0.9 + 3)) if driven else int(rng.poisson(20 * weekday))
        ts = utcnow().replace(year=day.year, month=day.month, day=day.day, hour=6)
        for dept, qty in ((world["ortho"], ortho), (world["icu"], int(rng.poisson(15 * weekday)))):
            if qty:
                rows.append(StockMovement(hospital_id=world["hospital"].id, consumable_id=world["gloves"].id,
                                          movement_type=MovementType.ISSUE, quantity=-qty, balance_after=5000,
                                          department_id=dept.id, created_at=ts))
    for ahead in range(0, future_days):
        day = today + timedelta(days=ahead)
        count = int(rng.poisson(5 * week_factor[(day.toordinal() // 7) % 30 - 20]))
        if count:
            _schedule(db, world, t, day, count, ProcedureStatus.SCHEDULED)
    db.add_all(rows)
    db.commit()
    return t


# ---------------------------------------------------------------- procedure features


def test_procedure_frames_counts_quantities_and_exclusions(db, world):
    t = _proc_type(db, world)
    t2 = _proc_type(db, world, code="LAPC")
    today = business_today()
    d1, d2 = today - timedelta(days=3), today + timedelta(days=2)
    _schedule(db, world, t, d1, 5)
    _schedule(db, world, t2, d1, 2)
    _schedule(db, world, t, d1, 50, ProcedureStatus.CANCELLED)  # never counts
    _schedule(db, world, t, d2, 7)
    db.commit()
    proc = load_procedure_data(db, world["hospital"].id, today - timedelta(days=1), 30)
    series = pd.DataFrame({"consumable_id": [world["gloves"].id, world["gloves"].id],
                           "department_id": [world["ortho"].id, world["icu"].id]})
    idx = pd.date_range(today - timedelta(days=5), today + timedelta(days=5))
    f = proc.frames(idx, series, scale=None)
    r1 = idx.get_loc(pd.Timestamp(d1))
    assert f["procedure_count"].iloc[r1, 0] == 7          # 5 + 2, cancelled excluded
    assert f["procedure_expected_quantity"].iloc[r1, 0] == 7 * QTY
    assert f["procedure_type_count"].iloc[r1, 0] == 2
    assert f["procedure_department_count"].iloc[r1, 0] == 1
    assert f["procedure_count"].iloc[r1, 1] == 0          # ICU series: no ICU procedures
    assert f["procedure_type_count"].iloc[r1, 1] == 2     # item-level feature is shared across the item's series
    assert f["procedure_expected_quantity"].iloc[idx.get_loc(pd.Timestamp(d2)), 0] == 7 * QTY  # future SCHEDULED used
    assert proc.schedule_end == d2


def test_inactive_type_and_mapping_rules(db, world):
    today = business_today()
    t = _proc_type(db, world, active=False)
    _schedule(db, world, t, today - timedelta(days=2), 3)   # history of an inactive type still happened
    _schedule(db, world, t, today + timedelta(days=2), 9)   # but it won't be performed in future
    db.commit()
    proc = load_procedure_data(db, world["hospital"].id, today - timedelta(days=1), 30)
    dates = set(proc.rows["date"].dt.date)
    assert today - timedelta(days=2) in dates and today + timedelta(days=2) not in dates
    # a deactivated mapping stops contributing
    m = db.scalar(select(ProcedureItemMapping))
    m.is_active = False
    db.commit()
    assert load_procedure_data(db, world["hospital"].id, today - timedelta(days=1), 30).is_empty()


def test_procedure_without_mapping_contributes_nothing(db, world):
    t = ProcedureType(hospital_id=world["hospital"].id, department_id=world["ortho"].id, code="NOMAP", name="No map")
    db.add(t)
    db.flush()
    _schedule(db, world, t, business_today() + timedelta(days=1), 10)
    db.commit()
    assert load_procedure_data(db, world["hospital"].id, business_today(), 30).is_empty()


def test_no_future_procedure_leakage_into_historical_features(db, world):
    """MANDATORY: changing a future procedure must not change any feature of an earlier date."""
    _procedure_world(db, world, days=40, future_days=0)
    hid = world["hospital"].id
    panel = load_panel(db, hid)
    series = panel.series
    y = panel.values / panel.values.mean().to_numpy()
    items = sorted(series["consumable_id"].unique())
    depts = sorted(series["department_id"].unique())

    def features():
        proc = load_procedure_data(db, hid, panel.data_end, 30)
        extra = proc.frames(y.index, series, scale=panel.values.mean().to_numpy())
        return feature_frame(y, series, items, depts, extra=extra).set_index(["date", "series"])

    before = features()
    training_date = panel.dates[-11]  # a historical training date
    t = db.scalar(select(ProcedureType))
    # a procedure 10 days after the training date (still in history) and one in the future
    later = (training_date + pd.Timedelta(days=10)).date()
    existing = db.scalar(select(ProcedureSchedule).where(ProcedureSchedule.scheduled_date == later))
    if existing:
        existing.count += 40
    else:
        _schedule(db, world, t, later, 40)
    _schedule(db, world, t, business_today() + timedelta(days=5), 99)
    db.commit()
    after = features()

    cols = [c for c in before.columns if c not in ("item", "department")]
    upto = before.index.get_level_values("date") <= training_date
    pd.testing.assert_frame_equal(before.loc[upto, cols], after.loc[upto, cols])
    # sanity: the change IS visible on its own date
    changed = before.index.get_level_values("date") == pd.Timestamp(later)
    assert not before.loc[changed, "procedure_expected_quantity"].equals(after.loc[changed, "procedure_expected_quantity"])


def test_future_schedule_does_not_change_backtest(db, world):
    """Adding procedures after data_end must not alter training or holdout scoring (only the future forecast)."""
    t = _procedure_world(db, world, days=60, future_days=0)
    r1 = run_training(db, world["hospital"].id)
    db.commit()
    for ahead in range(1, 8):
        _schedule(db, world, t, business_today() + timedelta(days=ahead), 30)
    db.commit()
    r2 = run_training(db, world["hospital"].id)
    db.commit()
    assert r1["procedure_model"]["trained"] and r2["procedure_model"]["trained"]
    m1 = next(v for k, v in r1["candidates"].items() if k.startswith("v2b_xgb"))
    m2 = next(v for k, v in r2["candidates"].items() if k.startswith("v2b_xgb"))
    assert m1 == m2


# ---------------------------------------------------------------- model training & selection


def test_select_best_never_forces_v2b():
    m = lambda w: {"wape": w}  # noqa: E731
    assert select_best({"xgboost": m(0.10), "xgboost_procedure": m(0.12), "moving_average_7": m(0.2)}) == "xgboost"
    assert select_best({"xgboost": m(0.10), "xgboost_procedure": m(0.10)}) == "xgboost"  # tie → V2A
    assert select_best({"xgboost": m(0.10), "xgboost_procedure": m(0.09)}) == "xgboost_procedure"
    assert select_best({"xgboost": m(0.15), "xgboost_procedure": m(0.14), "moving_average_7": m(0.13)}) == "moving_average_7"
    assert select_best({"xgboost": m(0.15), "xgboost_procedure": m(None)}) == "xgboost"


def test_v2b_trains_and_wins_when_procedures_drive_demand(db, world):
    _procedure_world(db, world, days=70, driven=True)
    result = run_training(db, world["hospital"].id)
    db.commit()
    pm = result["procedure_model"]
    assert pm["trained"] and pm["v2b_wape"] < pm["v2a_wape"] and pm["improved"]
    v2b = db.scalar(select(ModelVersion).where(ModelVersion.model_type == "xgboost_procedure"))
    v2a = db.scalar(select(ModelVersion).where(ModelVersion.model_type == "xgboost"))
    assert v2b.is_active and not v2a.is_active and v2b.name == "v2b_xgb_v1"
    assert set(PROC_FEATURES) <= set(v2b.features) and set(PROC_FEATURES) <= set(v2b.feature_importance)
    assert sum(v2b.feature_importance[f] for f in PROC_FEATURES) > 0
    assert v2b.artifact and v2b.dataset_hash != v2a.dataset_hash
    assert set(v2b.metrics) >= {"mae", "rmse", "wape", "bias"}
    assert "improved" in v2b.notes
    # both XGBoost variants store forecasts (served + comparison)
    for mv in (v2a, v2b):
        assert db.scalar(select(Forecast.id).where(Forecast.model_version_id == mv.id)) is not None


def test_consistent_improvement_rule():
    f = lambda a, b: {"v2a_wape": a, "v2b_wape": b}  # noqa: E731
    assert consistent_improvement([f(0.2, 0.1), f(0.2, 0.15), f(0.3, 0.25)])
    assert not consistent_improvement([f(0.2, 0.1), f(0.2, 0.25)])      # better on the holdout only
    assert not consistent_improvement([f(0.2, 0.2), f(0.2, 0.1)])       # tie on the holdout
    assert not consistent_improvement([])


@pytest.mark.usefixtures("frozen_day")  # calendar-sensitive fixture (see conftest.frozen_day)
def test_v2a_stays_active_when_v2b_is_worse(db, world):
    """CRITICAL: uninformative procedure data, V2B worse on the holdout → V2A remains the active model."""
    _procedure_world(db, world, days=84, driven=False, seed=1)
    result = run_training(db, world["hospital"].id)
    db.commit()
    pm = result["procedure_model"]
    assert pm["trained"] and pm["v2b_wape"] > pm["v2a_wape"]
    active = db.scalar(select(ModelVersion).where(ModelVersion.is_active.is_(True)))
    assert active.model_type == "xgboost" and not pm["improved"]
    v2b = db.scalar(select(ModelVersion).where(ModelVersion.model_type == "xgboost_procedure"))
    assert v2b.notes.startswith("Not selected") and not v2b.params["consistent_improvement"]
    assert "did not improve" in active.notes


@pytest.mark.usefixtures("frozen_day")  # calendar-sensitive fixture (see conftest.frozen_day)
def test_v2b_lucky_on_holdout_but_inconsistent_is_not_served(db, world):
    """Uninformative procedures that happen to win the latest 14 days must not replace V2A."""
    _procedure_world(db, world, days=84, driven=False, seed=7)
    result = run_training(db, world["hospital"].id)
    db.commit()
    pm = result["procedure_model"]
    assert pm["v2b_wape"] < pm["v2a_wape"], "fixture: V2B is better on the latest holdout by chance"
    assert not pm["consistent_improvement"] and len(pm["validation_folds"]) >= 2
    active = db.scalar(select(ModelVersion).where(ModelVersion.is_active.is_(True)))
    assert active.model_type == "xgboost"
    assert "not a consistent improvement" in active.notes


def test_no_procedure_data_falls_back_to_v2a(db, world):
    from tests.test_ml import _add_history

    _add_history(db, world, days=60)
    result = run_training(db, world["hospital"].id)
    db.commit()
    assert result["procedure_model"]["trained"] is False
    assert result["procedure_model"]["skipped_reason"]
    assert {v.model_type for v in db.scalars(select(ModelVersion))} == {"xgboost", "moving_average_7", "historical_average"}


@pytest.mark.usefixtures("frozen_day")  # calendar-sensitive fixture (see conftest.frozen_day; V0–V10 audit T-1)
def test_future_schedule_changes_v2b_forecast_and_falls_back_after_schedule(db, world):
    t = _procedure_world(db, world, days=70, future_days=6)  # schedule known only 6 days ahead
    hid = world["hospital"].id
    run_training(db, hid)
    db.commit()
    v2b = db.scalar(select(ModelVersion).where(ModelVersion.model_type == "xgboost_procedure", ModelVersion.is_active.is_(True)))
    v2a = db.scalar(select(ModelVersion).where(ModelVersion.model_type == "xgboost", ModelVersion.run_id == v2b.run_id))
    fb = v2b.params["procedure_data"]["falls_back_to_v2a_from"]
    last_scheduled = max(r.scheduled_date for r in db.scalars(select(ProcedureSchedule)))
    assert last_scheduled <= business_today() + timedelta(days=5)
    assert fb == (last_scheduled + timedelta(days=1)).isoformat()

    def daily(mv):
        return {f.forecast_date: f.predicted for f in db.scalars(select(Forecast).where(
            Forecast.model_version_id == mv.id, Forecast.consumable_id == world["gloves"].id))}

    d_b, d_a = daily(v2b), daily(v2a)
    after = [d for d in d_b if d.isoformat() >= fb]
    assert after and all(d_b[d] == pytest.approx(d_a[d]) for d in after)  # unknown schedule → V2A values
    before_total = sum(v for d, v in d_b.items() if d.isoformat() < fb)

    # scheduling many more procedures for those days must raise the procedure-aware forecast
    for row in db.scalars(select(ProcedureSchedule).where(ProcedureSchedule.status == ProcedureStatus.SCHEDULED)):
        row.count += 12
    db.commit()
    run_training(db, hid)
    db.commit()
    v2b2 = db.scalar(select(ModelVersion).where(ModelVersion.name == "v2b_xgb_v2"))
    d_b2 = daily(v2b2)
    assert sum(v for d, v in d_b2.items() if d.isoformat() < fb) > before_total * 1.2
    del t


def test_dataset_fingerprint_tracks_consumption_procedures_and_mappings(db, world):
    _procedure_world(db, world, days=50, future_days=5)
    hid = world["hospital"].id

    def v2b_hash():
        run_training(db, hid)
        db.commit()
        return db.scalars(select(ModelVersion.dataset_hash).where(ModelVersion.model_type == "xgboost_procedure")
                          .order_by(ModelVersion.id.desc())).first()

    h1 = v2b_hash()
    assert v2b_hash() == h1  # reproducible for unchanged data
    m = db.scalar(select(ProcedureItemMapping))
    m.quantity_per_procedure = Decimal("5")
    db.commit()
    h2 = v2b_hash()
    row = db.scalar(select(ProcedureSchedule).where(ProcedureSchedule.status == ProcedureStatus.COMPLETED))
    row.count += 1
    db.commit()
    h3 = v2b_hash()
    ts = utcnow().replace(hour=6) - timedelta(days=3)
    db.add(StockMovement(hospital_id=hid, consumable_id=world["gloves"].id, movement_type=MovementType.ISSUE,
                         quantity=-7, balance_after=4000, department_id=world["icu"].id, created_at=ts))
    db.commit()
    h4 = v2b_hash()
    assert len({h1, h2, h3, h4}) == 4
