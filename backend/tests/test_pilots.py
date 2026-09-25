"""V10 — pilot management and business-validation metrics.

A hand-built ledger with known outcomes (the numbers below are derived by hand in the comments), so every metric is
checked for correctness, plus lifecycle, isolation, insufficient-data handling, V9 data reuse, feedback, issues,
readiness, reports and the synthetic/observed labelling."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import business_today
from app.integrations import credentials
from app.integrations import sources as isrc
from app.main import app
from app.models import (
    AuditLog,
    Consumable,
    Forecast,
    ModelItemMetric,
    ModelVersion,
    Pilot,
    ProcurementRecommendation,
    RecommendationView,
    StockMovement,
    StockoutPrediction,
)
from app.pilots import metrics as pm
from app.services import stock
from app.services import supplier_orders as so
from tests.conftest import as_role

TZ = ZoneInfo(settings.TIMEZONE)


def at(d, hour=12):
    return datetime.combine(d, time(hour), tzinfo=TZ).astimezone(UTC)


def _rec(db, world, status, created, as_of, decided_hours=None, modified=False, reason=None):
    r = ProcurementRecommendation(
        hospital_id=world["hospital"].id, consumable_id=world["gloves"].id, created_at=created, as_of=as_of, run_id="t",
        status=status, scenario_key="s", lines=[{"supplier_id": world["supplier"].id, "quantity": 100}], quantity=100,
        cost_breakdown={}, metrics={}, scenarios=[], replenishment={}, in_transit=[], explanation=[], settings_snapshot={},
        solver={}, modified=modified, decision_reason=reason,
        decided_by_id=world["users"]["procurement_manager"].id if decided_hours is not None else None,
        decided_at=created + timedelta(hours=decided_hours) if decided_hours is not None else None,
        final_lines=[{"supplier_id": world["supplier"].id, "quantity": 150}] if modified else None)
    db.add(r)
    return r


@pytest.fixture()
def ledger(db, world):
    """Gloves (GLV-7) + 6 other items. T = today. Baseline = T−40…T−21 (20 days), pilot = T−20…T+9 (30 days).

    Baseline: receive 100 at T−45; issue 10/day T−40…T−31 → 0 during T−31 → stockout T−31…T−25 (7 days, 1 event);
    receive 200 at T−25 08:00. A supplier order placed T−33 (expected T−30) is open during the event → not "emergency";
    it arrives T−25 (5 days late). 5 more baseline orders on time → OTIF 5/6.
    Pilot: issue 20/day T−20…T−11 → 0 during T−11 → stockout T−11…T−1 (11 days, 1 event, no order open → emergency).
    Pilot orders: 5 on time + 1 placed T−10 (a stockout day, not from a recommendation → emergency purchase), on time.
    """
    T = business_today()
    h, g, sup = world["hospital"], world["gloves"], world["supplier"]
    im = world["users"]["inventory_manager"]
    ortho = world["ortho"]
    stock.receive(db, im, g, 100, "L0", T + timedelta(days=400), sup, 40, "R0", None, at=at(T - timedelta(days=45)))
    for k in range(40, 30, -1):
        d = T - timedelta(days=k)
        stock.issue(db, im, g, 10, ortho, f"B{k}", None, at=at(d), today=d)
    stock.receive(db, im, g, 200, "L1", T + timedelta(days=400), sup, 40, "R1", None, at=at(T - timedelta(days=25), 8))
    for k in range(20, 10, -1):
        d = T - timedelta(days=k)
        stock.issue(db, im, g, 20, ortho, f"P{k}", None, at=at(d), today=d)
    late = so.create_order(db, None, h.id, sup, g, 200, 40, T - timedelta(days=33), T - timedelta(days=30), reference="BL-LATE")
    so.record_delivery(db, late, T - timedelta(days=25), 200)
    for i in range(5):
        o = so.create_order(db, None, h.id, sup, g, 50, 40, T - timedelta(days=39 - i), T - timedelta(days=36 - i),
                            reference=f"BL-{i}")
        so.record_delivery(db, o, T - timedelta(days=37 - i), 50)
        o = so.create_order(db, None, h.id, sup, g, 50, 40, T - timedelta(days=19 - i), T - timedelta(days=16 - i),
                            reference=f"PI-{i}")
        so.record_delivery(db, o, T - timedelta(days=17 - i), 50)
    em = so.create_order(db, None, h.id, sup, g, 60, 40, T - timedelta(days=10), T - timedelta(days=8), reference="PI-EMERG")
    so.record_delivery(db, em, T - timedelta(days=8), 60)
    others = []
    for i in range(6):
        c = Consumable(hospital_id=h.id, sku=f"ITM-{i}", name=f"Item {i}", unit="piece", unit_cost=10, reorder_level=5)
        db.add(c)
        db.flush()
        stock.receive(db, im, c, 50 + i, f"LX{i}", T + timedelta(days=300), sup, 10, None, None, at=at(T - timedelta(days=44)))
        others.append(c)
    db.commit()
    return {"T": T, "others": others}


def _pilot(client, login, T, **kw):
    a = as_role(login, "admin")
    body = {"name": "Ward pilot", "baseline_start": str(T - timedelta(days=40)), "baseline_end": str(T - timedelta(days=21)),
            "pilot_start": str(T - timedelta(days=20)), "pilot_end": str(T + timedelta(days=9)), **kw}
    r = a.post("/api/pilots", json=body)
    assert r.status_code == 201, r.text
    return a, r.json()


def _m(period: dict, key: str) -> dict:
    return next(m for m in period["metrics"] if m["key"] == key)


# ---------------------------------------------------------------- management, lifecycle, validation


def test_pilot_creation_validation_and_lifecycle(login, db, world):
    T = business_today()
    a, p = _pilot(None, login, T, department_ids=[world["ortho"].id], user_ids=[world["users"]["procurement_manager"].id])
    assert p["status"] == "planned" and p["data_classification"] == "observed"  # the test hospital is not a demo hospital
    assert p["hospital_id"] == world["hospital"].id and p["organization_id"] == world["org"].id
    assert [d["name"] for d in p["departments"]] == ["Orthopaedics"] and len(p["users"]) == 1
    base = {"name": "X pilot", "baseline_start": str(T), "baseline_end": str(T + timedelta(days=5)),
            "pilot_start": str(T + timedelta(days=3)), "pilot_end": str(T + timedelta(days=20))}
    r = a.post("/api/pilots", json=base)
    assert r.status_code == 422 and "never mixed" in r.json()["detail"]  # overlapping periods
    assert a.post("/api/pilots", json={**base, "pilot_start": str(T + timedelta(days=6)), "pilot_end": str(T)}).status_code == 422
    assert a.post("/api/pilots", json={**base, "baseline_start": str(T - timedelta(days=9)), "baseline_end": str(T - timedelta(days=1)),
                                       "pilot_start": str(T), "hospital_id": world["other"].id}).status_code == 422  # no hospital in body
    assert a.post("/api/pilots", json={**base, "name": "ward PILOT", "baseline_start": str(T - timedelta(days=9)),
                                       "baseline_end": str(T - timedelta(days=1)), "pilot_start": str(T)}).status_code == 409
    assert a.post("/api/pilots", json={**base, "baseline_start": str(T - timedelta(days=9)), "baseline_end": str(T - timedelta(days=1)),
                                       "pilot_start": str(T), "department_ids": [world["other_dept"].id]}).status_code == 404
    assert a.post("/api/pilots", json={**base, "baseline_start": str(T - timedelta(days=9)), "baseline_end": str(T - timedelta(days=1)),
                                       "pilot_start": str(T), "user_ids": [world["users"]["other_admin"].id]}).status_code == 404
    # lifecycle
    url = f"/api/pilots/{p['id']}"
    assert a.patch(url, json={"status": "completed"}).status_code == 409  # planned → completed not allowed
    for s in ("active", "paused", "active"):
        assert a.patch(url, json={"status": s}).json()["status"] == s
    second = a.post("/api/pilots", json={**base, "name": "Second", "baseline_start": str(T - timedelta(days=9)),
                                         "baseline_end": str(T - timedelta(days=1)), "pilot_start": str(T)}).json()
    assert a.patch(f"/api/pilots/{second['id']}", json={"status": "active"}).status_code == 409  # one active pilot per hospital
    done = a.patch(url, json={"status": "completed"}).json()
    assert done["status"] == "completed" and done["actual_end"] == str(T)
    assert a.patch(url, json={"status": "active"}).status_code == 409  # terminal
    assert a.patch(url, json={"pilot_end": str(T + timedelta(days=90))}).status_code == 409
    assert a.patch(url, json={"notes": "Wrapped up"}).json()["notes"] == "Wrapped up"
    # permissions
    assert as_role(login, "procurement_manager").post("/api/pilots", json=base).status_code == 403
    assert as_role(login, "viewer").get(url).status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action.like("pilot.%"))) >= 6


# ---------------------------------------------------------------- metric correctness


def test_ledger_supplier_and_emergency_metrics_are_computed_from_existing_data(login, db, world, ledger):
    T = ledger["T"]
    a, p = _pilot(None, login, T)
    base = a.get(f"/api/pilots/{p['id']}/baseline").json()
    pil = a.get(f"/api/pilots/{p['id']}/metrics").json()
    assert base["period"]["days"] == 20 and pil["period"]["days"] == 30 and pil["period"]["partial"] is True
    assert pil["period"]["ledger_days"] == 20  # complete days only: T−20 … T−1
    assert (_m(base, "stockout_days")["value"], _m(base, "stockout_events")["value"]) == (7, 1)
    assert (_m(pil, "stockout_days")["value"], _m(pil, "stockout_events")["value"]) == (11, 1)
    assert _m(base, "affected_items")["value"] == 1 and _m(pil, "affected_items")["value"] == 1
    assert _m(base, "emergency_stockouts")["value"] == 0  # an order was open when it ran out
    assert _m(pil, "emergency_stockouts")["value"] == 1
    # baseline usable at T−21: 200 gloves + 6 items (50…55 = 315) = 515; value 200×40 + 315×10 = 11,150
    assert _m(base, "usable_stock")["value"] == 515 and _m(base, "inventory_value")["value"] == 11150
    assert _m(pil, "usable_stock")["value"] == 315  # gloves at 0
    # shortage estimate, baseline: normal days T−40…T−32 (10/day) and T−24…T−21 (0) → mean 90/13 × 7 stockout days
    assert _m(base, "shortage_estimate")["value"] == pytest.approx(90 / 13 * 7, abs=0.1)
    # V4: baseline 6 decided orders, 5 OTIF (the late one arrived 5 days after expected); pilot 6 of 6
    assert _m(base, "otif_rate")["value"] == pytest.approx(5 / 6, abs=1e-3)
    assert _m(base, "avg_days_late")["value"] == 5
    assert _m(pil, "otif_rate")["value"] == 1.0 and _m(pil, "fill_rate")["value"] == 1.0
    assert _m(pil, "emergency_purchases")["value"] == 1 and _m(base, "emergency_purchases")["value"] == 0
    # the formula is shown with its numbers
    assert "5 ÷ 6" in _m(base, "otif_rate")["formula"]


def test_forecast_and_warning_metrics_reuse_v2_and_v3_evaluation(login, db, world, ledger):
    T, g, other = ledger["T"], world["gloves"], ledger["others"][0]
    h = world["hospital"]
    mv = ModelVersion(hospital_id=h.id, run_id="r1", name="xgb_v1", model_type="xgboost", data_start=T - timedelta(days=60),
                      data_end=T - timedelta(days=21), test_start=T - timedelta(days=34), test_end=T - timedelta(days=21),
                      horizon_days=30, n_series=2, n_train_rows=10, dataset_hash="x", metrics={"wape": 0.2},
                      notes="Selected: lowest holdout WAPE", is_active=True)
    loser = ModelVersion(hospital_id=h.id, run_id="r1", name="ma7_v1", model_type="moving_average_7", data_start=T,
                         data_end=T - timedelta(days=21), test_start=T, test_end=T, horizon_days=30, n_series=2,
                         n_train_rows=1, dataset_hash="x", metrics={"wape": 0.9}, notes=None)
    db.add_all([mv, loser])
    db.flush()
    for k in range(20, 0, -1):
        d = T - timedelta(days=k)
        db.add(Forecast(hospital_id=h.id, model_version_id=mv.id, consumable_id=g.id, forecast_date=d, horizon=21 - k, predicted=25))
        db.add(Forecast(hospital_id=h.id, model_version_id=mv.id, consumable_id=other.id, forecast_date=d, horizon=21 - k, predicted=0))
        db.add(Forecast(hospital_id=h.id, model_version_id=loser.id, consumable_id=g.id, forecast_date=d, horizon=21 - k,
                        predicted=999))  # not served → ignored
    # holdout backtest days of the served model that fall in the baseline (T−34 … T−21)
    db.add(ModelItemMetric(model_version_id=mv.id, consumable_id=g.id, actual_total=0, predicted_total=0, mae=0, rmse=0, residual_std=0,
                           n_points=14, daily=[{"date": str(T - timedelta(days=k)), "actual": 10.0, "predicted": 12.0}
                                               for k in range(34, 20, -1)]))
    # V3 snapshots: gloves HIGH on T−20…T−15 (a stockout follows on T−11 → 6 TP), item 1 MEDIUM on T−20 (no stockout → FP),
    # items 2–5 LOW on T−20…T−16 (5 × 4 = 20 TN); a snapshot on T−3 has no complete outcome window yet
    def snap(c, d, level):
        db.add(StockoutPrediction(hospital_id=h.id, consumable_id=c.id, as_of=d, horizon_days=14, usable_stock=0, forecast_7=0,
                                  forecast_14=0, forecast_30=0, probability=0.5, risk_level=level, probability_source="t"))
    for k in range(20, 14, -1):
        snap(g, T - timedelta(days=k), "HIGH")
    snap(ledger["others"][1], T - timedelta(days=20), "MEDIUM")
    for c in ledger["others"][2:6]:
        for k in range(20, 15, -1):
            snap(c, T - timedelta(days=k), "LOW")
    snap(g, T - timedelta(days=3), "HIGH")
    db.commit()
    a, p = _pilot(None, login, T)
    pil = a.get(f"/api/pilots/{p['id']}/metrics").json()
    base = a.get(f"/api/pilots/{p['id']}/baseline").json()
    # live: gloves T−20…T−12 actual 20 vs 25 (9 points; T−11 onwards censored), other item 0 vs 0 on 20 days → 29 points
    w = _m(pil, "forecast_wape")
    assert w["n"] == 29 and w["sufficient"] and w["value"] == pytest.approx(45 / 180)
    assert _m(pil, "forecast_bias")["value"] == pytest.approx(45 / 180)
    assert _m(pil, "forecast_mae")["value"] == pytest.approx(45 / 29, abs=1e-3)
    assert w["models"][0]["name"] == "xgb_v1" and len(w["models"]) == 1
    assert _m(base, "forecast_wape")["sufficient"] is False  # no live forecast existed in the baseline
    hw = _m(base, "holdout_wape")  # but the served model's backtest days did: |12 − 10| × 14 ÷ 140
    assert hw["n"] == 14 and hw["value"] == pytest.approx(0.2)
    prec, rec = _m(pil, "warning_precision"), _m(pil, "warning_recall")
    assert prec["n"] == 27 and prec["value"] == pytest.approx(6 / 7) and rec["value"] == 1.0
    assert (_m(pil, "warning_true_positives")["value"], _m(pil, "warning_false_positives")["value"],
            _m(pil, "warning_false_negatives")["value"]) == (6, 1, 0)
    assert "1 snapshot(s) excluded" in prec["note"]
    assert _m(pil, "events_warned")["value"] == 1.0 and _m(pil, "warning_lead_days")["value"] == 9  # first warning T−20
    assert _m(base, "warning_precision")["sufficient"] is False


def test_decision_funnel_views_and_rates(login, db, world, ledger):
    T = ledger["T"]
    c0 = at(T - timedelta(days=5), 9)
    d5 = T - timedelta(days=5)
    recs = [_rec(db, world, "APPROVED", c0, d5, 2), _rec(db, world, "APPROVED", c0, d5, 4, True, "MOQ"),
            _rec(db, world, "REJECTED", c0, T - timedelta(days=5), 6, reason="Enough stock"),
            _rec(db, world, "SUPERSEDED", c0, T - timedelta(days=5)), _rec(db, world, "PENDING", at(T, 0), T)]
    db.commit()
    a, p = _pilot(None, login, T)
    v = as_role(login, "viewer")
    for r in recs[:2]:
        assert v.post(f"/api/pilot-tracking/recommendations/{r.id}/view").status_code == 201
    v.post(f"/api/pilot-tracking/recommendations/{recs[0].id}/view")
    assert v.post(f"/api/pilot-tracking/recommendations/{recs[0].id + 999}/view").status_code == 404
    rows = a.get(f"/api/pilots/{p['id']}/decisions").json()
    assert [r["outcome"] for r in rows] == ["approved", "modified", "rejected", "expired", "pending"]
    assert rows[0]["views"] == 2 and rows[1]["viewed"] and not rows[2]["viewed"]
    assert rows[1]["final_lines"][0]["quantity"] == 150 and rows[1]["reason"] == "MOQ"
    assert rows[2]["decided_by"]["name"] == "Procurement_Manager" and rows[2]["hours_to_decision"] == 6
    pil = a.get(f"/api/pilots/{p['id']}/metrics").json()
    assert _m(pil, "recs_generated")["value"] == 5 and _m(pil, "recs_viewed")["value"] == 2
    for k, v_ in (("approval_rate", 1 / 3), ("modification_rate", 1 / 3), ("rejection_rate", 1 / 3)):
        assert _m(pil, k)["value"] == pytest.approx(v_)
    assert _m(pil, "hours_to_decision")["value"] == 4 and _m(pil, "recs_expired")["value"] == 1
    db.expire_all()
    assert db.scalar(select(func.count(RecommendationView.id))) == 3


# ---------------------------------------------------------------- V9 reuse: data quality, reliability, accuracy


def test_v9_imported_data_drives_data_quality_accuracy_and_reliability(client, login, db, world, ledger):
    T = ledger["T"]
    src = isrc.create_source(db, world["hospital"].id, None, "Pilot ERP", "erp", "api_push", ["items", "inventory"], {})
    _c, key = credentials.create(db, src, "k", None)
    db.commit()
    a, p = _pilot(None, login, T, source_ids=[src.id])
    c = TestClient(app)
    items = [{"code": f"NEW-{i}", "name": f"New {i}", "unit": "piece"} for i in range(20)]
    items += [{"code": f"BAD-{i}", "unit": "piece"} for i in range(4)] + [{"code": "NEW-0", "name": "dup", "unit": "piece"}]
    r = c.post("/api/ingest/v1/items", json={"records": items}, headers={"Authorization": f"Bearer {key}"})
    assert r.json()["rejected"] == 5
    counts = [{"item_code": "GLV-7", "quantity": 0}] + [{"item_code": f"ITM-{i}", "quantity": 50 + i} for i in range(5)]
    counts.append({"item_code": "ITM-5", "quantity": 99})  # MedFlow has 55 → one discrepancy
    c.post("/api/ingest/v1/inventory", json={"records": counts}, headers={"Authorization": f"Bearer {key}"})
    pil = a.get(f"/api/pilots/{p['id']}/metrics").json()
    # 25 + 7 received, 5 rejected on first pass (4 missing names + 1 duplicate) → 27 / 32
    dq = _m(pil, "data_quality_pct")
    assert (dq["n"], dq["value"]) == (32, pytest.approx(27 / 32)) and "27 ÷ 32" in dq["formula"]
    assert _m(pil, "dq_missing_field")["value"] == 4 and _m(pil, "dq_duplicate_record")["value"] == 1
    acc = _m(pil, "inventory_accuracy")
    assert (acc["n"], acc["value"]) == (7, pytest.approx(6 / 7)) and acc["sufficient"]
    assert _m(pil, "stock_discrepancies")["value"] == 1 and _m(pil, "discrepancy_units")["value"] == 44
    rel = pil["integration_sources"][0]
    assert (rel["name"], rel["runs"], rel["failed_runs"], rel["records_processed"], rel["records_rejected"]) == ("Pilot ERP", 2, 0, 32, 5)
    base = a.get(f"/api/pilots/{p['id']}/baseline").json()
    assert _m(base, "data_quality_pct")["sufficient"] is False and _m(base, "records_received")["value"] == 0


def test_pilot_without_sources_reports_missing_integration_data(login, db, world, ledger):
    a, p = _pilot(None, login, ledger["T"])
    pil = a.get(f"/api/pilots/{p['id']}/metrics").json()
    for key in ("inventory_accuracy", "data_quality_pct"):
        m = _m(pil, key)
        assert m["sufficient"] is False and "No V9 data source" in m["note"]
    assert pil["integration_sources"] == []


def test_empty_hospital_and_future_periods_are_insufficient_not_zero(login, db, world):
    T = business_today()
    a, p = _pilot(None, login, T, name="Future pilot", baseline_start=str(T + timedelta(days=1)),
                  baseline_end=str(T + timedelta(days=10)), pilot_start=str(T + timedelta(days=11)),
                  pilot_end=str(T + timedelta(days=40)))
    cmp = a.get(f"/api/pilots/{p['id']}/comparison").json()
    ledger_rows = [r for r in cmp["rows"] if r["section"] in ("inventory", "stockouts", "forecasting", "stockout_prediction")]
    assert ledger_rows and all(r["status"] == "insufficient_data" for r in ledger_rows if r["key"] != "stock_discrepancies")
    pil = a.get(f"/api/pilots/{p['id']}/metrics").json()
    assert pil["period"]["ledger_end"] is None and _m(pil, "stockout_days")["value"] is None


# ---------------------------------------------------------------- comparison


def test_comparison_is_descriptive_normalised_and_uses_pp(login, db, world, ledger):
    a, p = _pilot(None, login, ledger["T"])
    a.post(f"/api/pilots/{p['id']}/external-factors", json={"factor": "seasonal_demand", "note": "Monsoon admissions", "period": "pilot"})
    cmp = a.get(f"/api/pilots/{p['id']}/comparison").json()
    assert cmp["label"] == "Descriptive baseline vs pilot comparison" and "does not show that MedFlow caused" in cmp["causality_statement"]
    assert cmp["classification_label"] == "Observed pilot result"
    rows = {r["key"]: r for r in cmp["rows"]}
    # periods differ (20 vs 20 complete ledger days here — equal) → stockout days compared raw: 7 → 11
    sd = rows["stockout_days"]
    assert (sd["baseline"], sd["pilot"], sd["difference"], sd["normalized"]) == (7, 11, 4, None)
    assert sd["relative_change"] == pytest.approx(4 / 7, abs=1e-3)
    otif = rows["otif_rate"]
    assert otif["difference_pp"] == pytest.approx(16.67, abs=0.01) and otif["difference"] is None
    assert rows["forecast_wape"]["status"] == "insufficient_data"
    assert cmp["external_factors"][0]["note"] == "Monsoon admissions"
    assert any("differ in length" in n for n in cmp["notes"])  # 20 vs 30 calendar days
    # activity counts (different elapsed days) are normalised per 30 days
    rs = rows["supplier_orders"]
    assert rs["normalized"] == "per 30 days"


# ---------------------------------------------------------------- issues, feedback, readiness, report


def test_issues_feedback_and_readiness(login, db, world, ledger):
    T = ledger["T"]
    src = isrc.create_source(db, world["hospital"].id, None, "Upload", "spreadsheet", "upload", ["items"], {})
    db.commit()
    a, p = _pilot(None, login, T, source_ids=[src.id], department_ids=[world["ortho"].id],
                  user_ids=[world["users"]["inventory_manager"].id])
    url = f"/api/pilots/{p['id']}"
    inv = as_role(login, "inventory_manager")
    body = {"category": "inventory_mismatch", "severity": "high", "title": "Ward count differs", "impact": "Reorder delayed"}
    i = inv.post(f"{url}/issues", json=body)
    assert i.status_code == 201 and i.json()["status"] == "open"
    iid = i.json()["id"]
    assert inv.post(f"{url}/issues", json={**body, "category": "weather"}).status_code == 422
    assert inv.post(f"{url}/issues", json={**body, "source": "sync_run", "source_ref": "sync_run:999999"}).status_code == 404
    assert as_role(login, "viewer").post(f"{url}/issues", json=body).status_code == 403
    inv = as_role(login, "inventory_manager")  # the test client is shared: sign back in
    r = inv.patch(f"/api/pilot-issues/{iid}", json={"status": "investigating", "assigned_to_id": world["users"]["admin"].id})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "investigating"
    assert inv.patch(f"/api/pilot-issues/{iid}", json={"status": "resolved"}).status_code == 422  # resolution required
    done = inv.patch(f"/api/pilot-issues/{iid}", json={"status": "resolved", "resolution": "Recounted; ERP mapping fixed"}).json()
    assert done["resolved_by"]["name"] and done["resolved_at"]
    db.expire_all()
    assert db.scalar(select(func.count(StockMovement.id)).where(StockMovement.movement_type == "ADJUSTMENT")) == 0  # no auto-correction
    # feedback only while the pilot runs
    fb = {"target_type": "forecast", "rating": "somewhat_useful", "reasons": ["timing_problem"], "comment": "Late on Mondays"}
    assert inv.post(f"{url}/feedback", json=fb).status_code == 409
    assert as_role(login, "admin").patch(url, json={"status": "active"}).status_code == 200
    inv = as_role(login, "inventory_manager")
    assert inv.post(f"{url}/feedback", json=fb).status_code == 201
    assert inv.post(f"{url}/feedback", json={**fb, "target_type": "recommendation"}).status_code == 422
    assert inv.post(f"{url}/feedback", json={**fb, "reasons": ["vibes"]}).status_code == 422
    rec = _rec(db, world, "PENDING", at(T, 0), T)
    db.commit()
    assert inv.post(f"{url}/feedback", json={**fb, "target_type": "recommendation", "recommendation_id": rec.id,
                                             "rating": "useful", "reasons": ["correct_recommendation"]}).status_code == 201
    a = as_role(login, "admin")
    s = a.get(f"{url}/feedback").json()
    assert s["by_rating"] == {"somewhat_useful": 1, "useful": 1} and s["by_reason"]["timing_problem"] == 1
    # readiness: automatic evidence + manual confirmations
    rd = a.get(f"{url}/readiness").json()
    items = {i["key"]: i for g in rd["groups"] for i in g["items"]}
    assert items["baseline_configured"]["done"] and items["users_assigned"]["done"] and items["audit_enabled"]["done"]
    assert not items["initial_sync"]["done"] and not items["no_patient_data"]["done"]
    assert "not a claim" in rd["statement"]
    assert a.put(f"{url}/readiness/audit_enabled", json={"confirmed": True}).status_code == 422  # automatic item
    assert a.put(f"{url}/readiness/nonsense", json={"confirmed": True}).status_code == 404
    rd2 = a.put(f"{url}/readiness/no_patient_data", json={"confirmed": True, "note": "Only item codes and counts"}).json()
    assert rd2["done"] == rd["done"] + 1
    assert as_role(login, "inventory_manager").put(f"{url}/readiness/no_patient_data", json={"confirmed": True}).status_code == 403


def test_report_is_factual_labelled_and_snapshotted(login, db, world, ledger):
    a, p = _pilot(None, login, ledger["T"])
    r = a.get(f"/api/pilots/{p['id']}/report").json()
    assert r["classification"] == "observed" and r["banner"] is None and r["result_label"] == "Observed pilot result"
    titles = [s["title"] for s in r["sections"]]
    assert titles[0] == "2. Data quality" and "8. User adoption" in titles
    md = r["markdown"]
    for h in ("## 1. Pilot overview", "## 9. Issues encountered", "## 10. Limitations", "## 11. Conclusion",
              "Descriptive baseline vs pilot comparison", "does not show that MedFlow caused"):
        assert h in md
    assert "Insufficient data" in md
    for banned in ("reduced stockouts", "saved ₹", "improved procurement", "validated in real hospitals"):
        assert banned not in md.lower()
    assert any("Observed pilot results (descriptive, not causal)" in c for c in r["conclusion"])
    txt = a.get(f"/api/pilots/{p['id']}/report", params={"format": "markdown"})
    assert txt.headers["content-type"].startswith("text/markdown") and txt.text.startswith("# MedFlow pilot report")
    snap = a.post(f"/api/pilots/{p['id']}/report/snapshots")
    assert snap.status_code == 201
    assert a.get(f"/api/pilot-reports/{snap.json()['id']}").text.startswith("# MedFlow pilot report")
    assert len(a.get(f"/api/pilots/{p['id']}/report/snapshots").json()) == 1
    assert as_role(login, "procurement_manager").post(f"/api/pilots/{p['id']}/report/snapshots").status_code == 403


def test_demo_hospital_pilots_are_synthetic_and_never_claim_outcomes(login, db, world, ledger):
    world["hospital"].is_demo = True
    db.commit()
    a = as_role(login, "admin")
    sb = a.post("/api/pilots/sandbox")
    assert sb.status_code == 201, sb.text
    p = sb.json()
    assert p["data_classification"] == "synthetic" and p["status"] == "active" and "SYNTHETIC" in p["name"]
    assert p["departments"] and p["users"]
    T = ledger["T"]
    assert (p["baseline_start"], p["pilot_start"]) == (str(T - timedelta(days=60)), str(T - timedelta(days=30)))
    assert a.post("/api/pilots/sandbox").json()["name"].endswith("(2)")  # a second one; stays planned (one active)
    r = a.get(f"/api/pilots/{p['id']}/report").json()
    assert r["banner"] == "DEMO / SYNTHETIC DATA — NOT REAL HOSPITAL DATA" and r["result_label"] == "Synthetic/demo result"
    assert r["conclusion"][0].startswith("No real-world outcome claim can be made yet.")
    assert "synthetic/demo result" in r["markdown"]
    assert a.get(f"/api/pilots/{p['id']}/dashboard").json()["classification_label"].startswith("Synthetic/demo result")
    # a normal (non-demo) hospital cannot create a sandbox
    world["hospital"].is_demo = False
    db.commit()
    assert a.post("/api/pilots/sandbox").status_code == 409


# ---------------------------------------------------------------- isolation


def test_pilots_are_isolated_per_hospital_and_organization(client, login, db, world, ledger):
    T = ledger["T"]
    other = world["other"]
    bp = Pilot(hospital_id=other.id, organization_id=world["other_org"].id, name="ZZB pilot", status="active",
               data_classification="observed", baseline_start=T - timedelta(days=40), baseline_end=T - timedelta(days=21),
               pilot_start=T - timedelta(days=20), pilot_end=T + timedelta(days=9), external_factors=[])
    db.add(bp)
    db.commit()
    a, p = _pilot(None, login, T)
    assert [x["id"] for x in a.get("/api/pilots").json()] == [p["id"]]
    for path in ("", "/metrics", "/baseline", "/comparison", "/dashboard", "/issues", "/feedback", "/readiness", "/report",
                 "/decisions", "/report/snapshots"):
        assert a.get(f"/api/pilots/{bp.id}{path}").status_code == 404, path
    assert a.patch(f"/api/pilots/{bp.id}", json={"notes": "x"}).status_code == 404
    assert a.post(f"/api/pilots/{bp.id}/issues", json={"category": "other", "title": "xxx"}).status_code == 404
    assert "ZZB" not in a.get("/api/pilots/active").text
    # organization view: org admins of the pilot's organization only; no operational metrics in it
    from app.models import OrganizationMembership, OrgRole

    db.add(OrganizationMembership(user_id=world["users"]["viewer"].id, organization_id=world["org"].id, role=OrgRole.ORG_ADMIN))
    db.commit()
    v = as_role(login, "viewer")
    rows = v.get(f"/api/organizations/{world['org'].id}/pilots").json()
    assert [r["name"] for r in rows] == ["Ward pilot"] and set(rows[0]) >= {"status", "open_issues"} and "metrics" not in rows[0]
    assert v.get(f"/api/organizations/{world['other_org'].id}/pilots").status_code == 404
    assert as_role(login, "admin").get(f"/api/organizations/{world['org'].id}/pilots").status_code == 404


def test_assistant_has_no_pilot_access_to_other_hospitals(login, world):
    r = as_role(login, "admin").post("/api/assistant/ask", json={"question": "Show me the pilot report of Other Hospital"})
    assert r.status_code == 200
    assert "ZZB" not in r.text


def test_metric_period_boundaries(db, world, ledger):
    """A stockout day exactly on a boundary belongs to the period containing it — never to both."""
    T = ledger["T"]
    hid = world["hospital"].id
    a = pm.Period("baseline", T - timedelta(days=40), T - timedelta(days=31))
    b = pm.Period("pilot", T - timedelta(days=30), T - timedelta(days=21))
    ma, _ = pm.ledger_metrics(db, hid, a)
    mb, _ = pm.ledger_metrics(db, hid, b)
    get = lambda ms, k: next(m for m in ms if m["key"] == k)["value"]  # noqa: E731
    assert get(ma, "stockout_days") == 1 and get(mb, "stockout_days") == 6  # T−31 | T−30…T−25
    assert get(ma, "stockout_events") == 1 and get(mb, "stockout_events") == 0  # the episode started in a
