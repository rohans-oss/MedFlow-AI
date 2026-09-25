import pytest

from tests.conftest import as_role
from tests.test_ml import _add_history


def test_no_model_yet(login, world):
    c = as_role(login, "viewer")
    assert c.get("/api/forecasts").json()["model"] is None
    r = c.get(f"/api/forecasts/{world['gloves'].id}")
    assert r.status_code == 404 and "trained" in r.json()["detail"]


@pytest.mark.parametrize("role,expected", [
    ("admin", 200), ("procurement_manager", 200), ("inventory_manager", 200),
    ("department_manager", 403), ("viewer", 403),
])
def test_train_permissions(login, world, db, role, expected):
    _add_history(db, world, days=45)
    assert as_role(login, role).post("/api/forecasts/train").status_code == expected


def test_train_with_too_little_data_is_422(login, world, db):
    _add_history(db, world, days=10)
    r = as_role(login, "procurement_manager").post("/api/forecasts/train")
    assert r.status_code == 422 and "days of consumption history" in r.json()["detail"]


def test_forecast_endpoints_after_training(login, world, db):
    _add_history(db, world, days=60)
    c = as_role(login, "procurement_manager")
    train = c.post("/api/forecasts/train").json()
    assert set(train["candidates"]) == {"xgb_v1", "ma7_v1", "hist_avg_v1"}
    gid = world["gloves"].id

    f = c.get(f"/api/forecasts/{gid}", params={"days": 14}).json()
    # the documented contract
    assert f["item"] == "Surgical gloves 7" and f["horizon_days"] == 14
    assert isinstance(f["predicted_demand"], int) and f["predicted_demand"] > 0
    assert f["model_version"] == train["active_model"]
    assert f["lower"] <= f["predicted_demand"] <= f["upper"]
    # horizons are consistent with the daily series
    h = {x["days"]: x["predicted"] for x in f["horizons"]}
    assert set(h) == {7, 14, 30}
    assert h[14] == pytest.approx(sum(d["predicted"] for d in f["daily"][:14]), abs=0.5)
    assert h[7] < h[14] < h[30]
    assert len(f["daily"]) == 30 and len(f["history"]) == 60
    assert f["explanation"] and f["backtest"]["test_end"] == f["data_through"]
    if f["model_type"] == "xgboost":
        assert f["drivers"] and f["base_level"] is not None

    assert c.get(f"/api/forecasts/{gid}", params={"days": 7}).json()["horizon_days"] == 7
    assert c.get(f"/api/forecasts/{gid}", params={"days": 31}).status_code == 422
    assert c.get(f"/api/forecasts/{gid}", params={"days": 0}).status_code == 422

    overview = c.get("/api/forecasts", params={"days": 14}).json()
    assert overview["model"]["name"] == train["active_model"]
    row = next(i for i in overview["items"] if i["consumable_id"] == gid)
    assert row["predicted_demand"] == pytest.approx(h[14], abs=0.5)

    models = c.get("/api/forecasts/models").json()
    assert len(models) == 3 and sum(m["is_active"] for m in models) == 1
    detail = c.get(f"/api/forecasts/models/{models[0]['id']}").json()
    assert len(detail["run_candidates"]) == 3
    assert {i["sku"] for i in detail["items"]} >= {"GLV-7"}
    assert "artifact" not in models[0]

    # viewers can read forecasts; audit trail records training
    v = as_role(login, "viewer")
    assert v.get(f"/api/forecasts/{gid}").status_code == 200
    admin = as_role(login, "admin")
    assert any(a["action"] == "forecast.train" for a in admin.get("/api/audit-logs").json()["items"])


def test_forecast_hospital_isolation(login, world, db):
    _add_history(db, world, days=45)
    c = as_role(login, "admin")
    c.post("/api/forecasts/train")
    assert c.get(f"/api/forecasts/{world['other_item'].id}").status_code == 404
    other = login("admin@other.demo")
    assert other.get("/api/forecasts").json()["model"] is None
    mid = as_role(login, "admin").get("/api/forecasts/models").json()[0]["id"]
    assert login("admin@other.demo").get(f"/api/forecasts/models/{mid}").status_code == 404
