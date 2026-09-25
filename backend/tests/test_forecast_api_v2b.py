"""V2B — forecast API: backwards compatibility, procedure impact, comparison, explanation, stock cover."""

from datetime import timedelta

from app.core.security import business_today
from tests.conftest import as_role
from tests.test_ml import _add_history
from tests.test_ml_procedures import QTY, _procedure_world

V2A_KEYS = {
    "item", "item_id", "sku", "unit", "horizon_days", "predicted_demand", "lower", "upper", "interval", "model_version",
    "model_type", "trained_at", "data_through", "is_stale", "horizons", "daily", "history", "drivers_horizon",
    "base_level", "drivers", "explanation", "backtest", "usable_stock", "avg_daily_last_30", "days_of_cover",
}
V2B_KEYS = {"forecast_source", "scheduled_procedures", "procedure_driven_demand", "procedure_impact", "model_comparison",
            "comparison_daily", "expected_shortage", "stock_covers_horizon", "days_of_stock_remaining"}


def test_forecast_with_procedures(login, world, db):
    _procedure_world(db, world, days=70, driven=True)
    c = as_role(login, "procurement_manager")
    train = c.post("/api/forecasts/train").json()
    assert train["procedure_model"]["trained"] and train["procedure_model"]["improved"]
    gid = world["gloves"].id

    f = c.get(f"/api/forecasts/{gid}", params={"days": 14}).json()
    assert V2A_KEYS <= set(f) and V2B_KEYS <= set(f)  # V2A contract intact, V2B fields added
    assert f["model_version"].startswith("v2b_xgb_v") and f["forecast_source"] == "procedure_aware"
    assert f["predicted_demand"] > 0

    impact = f["procedure_impact"]
    start = business_today()  # data_through = yesterday → horizon day 1 = today
    assert impact["start"] == start.isoformat()
    total_count = sum(t["count"] for t in impact["types"])
    assert f["scheduled_procedures"] == impact["scheduled_procedures"] == total_count > 0
    assert f["procedure_driven_demand"] == impact["expected_quantity"] == total_count * QTY
    assert impact["departments"] == ["Orthopaedics"]
    assert impact["v2a_forecast"] is not None and impact["v2b_forecast"] is not None
    assert impact["procedure_effect"] == round(impact["v2b_forecast"] - impact["v2a_forecast"], 1)
    assert len(f["comparison_daily"]) == 30  # V2A line for the chart

    cmp = f["model_comparison"]
    assert cmp["improved"] and cmp["v2b_wape"] < cmp["v2a_wape"] and cmp["summary"].startswith("V2B improved WAPE")
    assert any("procedure" in e.lower() for e in f["explanation"])
    assert any(d["feature"].startswith("procedure_") for d in f["drivers"])

    # stand-alone procedure-impact endpoint agrees
    pi = c.get(f"/api/forecasts/{gid}/procedure-impact", params={"days": 14}).json()
    assert pi["scheduled_procedures"] == impact["scheduled_procedures"] and pi["expected_quantity"] == impact["expected_quantity"]
    assert c.get(f"/api/forecasts/{gid}/procedure-impact", params={"days": 40}).status_code == 422

    # stock cover: the test hospital has no gloves in stock → the whole forecast is a shortage
    assert f["usable_stock"] == 0 and f["expected_shortage"] == f["predicted_demand"]
    assert f["stock_covers_horizon"] is False and f["days_of_stock_remaining"] == 0

    overview = c.get("/api/forecasts", params={"days": 14}).json()
    assert overview["comparison"]["active_source"] == "procedure_aware"
    row = next(i for i in overview["items"] if i["consumable_id"] == gid)
    assert row["scheduled_procedures"] == impact["scheduled_procedures"]
    assert row["expected_shortage"] > 0


def test_schedule_change_after_training_is_flagged(login, world, db):
    _procedure_world(db, world, days=70, driven=True)
    admin = as_role(login, "admin")
    admin.post("/api/forecasts/train")
    gid = world["gloves"].id
    before = admin.get(f"/api/forecasts/{gid}", params={"days": 7}).json()
    assert before["model_comparison"]["schedule_changed_since_training"] is False
    t = admin.get("/api/procedures/types").json()[0]
    day = (business_today() + timedelta(days=45)).isoformat()  # outside the window: no effect on impact
    admin.post("/api/procedures/schedule", json={"procedure_type_id": t["id"], "scheduled_date": day, "count": 3})
    rows = admin.get("/api/procedures/schedule", params={"status": "SCHEDULED", "date_from": business_today().isoformat(),
                                                         "date_to": (business_today() + timedelta(days=6)).isoformat()}).json()
    admin.patch(f"/api/procedures/schedule/{rows['items'][0]['id']}", json={"count": rows["items"][0]["count"] + 20})
    after = admin.get(f"/api/forecasts/{gid}", params={"days": 7}).json()
    assert after["procedure_impact"]["scheduled_procedures"] == before["procedure_impact"]["scheduled_procedures"] + 20
    assert after["model_comparison"]["schedule_changed_since_training"] is True
    assert any("retrain" in e for e in after["explanation"])
    assert after["predicted_demand"] == before["predicted_demand"]  # stored forecast changes only after retraining


def test_forecast_without_procedures_uses_v2a(login, world, db):
    _add_history(db, world, days=60)
    c = as_role(login, "viewer")
    as_role(login, "admin").post("/api/forecasts/train")
    c = as_role(login, "viewer")
    f = c.get(f"/api/forecasts/{world['gloves'].id}", params={"days": 14}).json()
    assert V2A_KEYS <= set(f)
    assert f["forecast_source"] == "consumption" and f["model_version"].startswith("xgb_v")
    assert f["scheduled_procedures"] == 0 and f["procedure_driven_demand"] == 0
    assert f["model_comparison"]["v2b_trained"] is False and f["comparison_daily"] == []
    assert not any("procedure" in e.lower() for e in f["explanation"])
    assert c.get(f"/api/forecasts/{world['gloves'].id}/procedure-impact").json()["scheduled_procedures"] == 0
