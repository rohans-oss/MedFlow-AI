from datetime import timedelta

from app.core.security import business_today
from tests.conftest import as_role


def _active(c):
    return {(a["alert_type"], a["severity"]) for a in c.get("/api/alerts").json()["items"]}


def test_alert_lifecycle(login, world):
    c = as_role(login, "inventory_manager")
    c.post("/api/alerts/evaluate")
    assert ("OUT_OF_STOCK", "CRITICAL") in _active(c)

    # 40 of reorder 100 -> LOW HIGH (≤50%)
    c.post("/api/inventory/receive", json={"consumable_id": world["gloves"].id, "quantity": 40, "lot_number": "A",
                                           "expiry_date": (business_today() + timedelta(days=10)).isoformat()})
    assert _active(c) == {("LOW_STOCK", "HIGH"), ("EXPIRING_SOON", "HIGH")}

    # to 90 -> LOW MEDIUM; OUT_OF_STOCK was auto-resolved
    c.post("/api/inventory/receive", json={"consumable_id": world["gloves"].id, "quantity": 50, "lot_number": "B"})
    assert ("LOW_STOCK", "MEDIUM") in _active(c)
    resolved = c.get("/api/alerts", params={"status": "RESOLVED"}).json()["items"]
    assert any(a["alert_type"] == "OUT_OF_STOCK" for a in resolved)

    # above reorder level -> no stock alerts, expiry alert remains
    c.post("/api/inventory/receive", json={"consumable_id": world["gloves"].id, "quantity": 500, "lot_number": "C"})
    assert _active(c) == {("EXPIRING_SOON", "HIGH")}


def test_acknowledge_and_escalation_reopens(login, world):
    c = as_role(login, "inventory_manager")
    c.post("/api/inventory/receive", json={"consumable_id": world["gloves"].id, "quantity": 90, "lot_number": "A"})
    alert = c.get("/api/alerts").json()["items"][0]
    assert alert["alert_type"] == "LOW_STOCK" and alert["severity"] == "MEDIUM"
    r = c.post(f"/api/alerts/{alert['id']}/acknowledge")
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED"
    assert c.post(f"/api/alerts/{alert['id']}/acknowledge").status_code == 409
    # drop to 30 -> escalates to HIGH and re-opens
    c.post("/api/inventory/issue", json={"consumable_id": world["gloves"].id, "quantity": 60,
                                         "department_id": world["icu"].id})
    again = c.get("/api/alerts").json()["items"][0]
    assert again["id"] == alert["id"] and again["severity"] == "HIGH" and again["status"] == "OPEN"


def test_viewer_cannot_manage_alerts(login, world):
    inv = as_role(login, "inventory_manager")
    inv.post("/api/alerts/evaluate")
    alert_id = inv.get("/api/alerts").json()["items"][0]["id"]
    v = as_role(login, "viewer")
    assert v.get("/api/alerts").status_code == 200
    assert v.post(f"/api/alerts/{alert_id}/acknowledge").status_code == 403
    assert v.post("/api/alerts/evaluate").status_code == 403


def test_inactive_item_alerts_resolve(login, world):
    c = as_role(login, "inventory_manager")
    c.post("/api/alerts/evaluate")
    assert c.get("/api/alerts").json()["total"] == 1
    c.patch(f"/api/consumables/{world['gloves'].id}", json={"is_active": False})
    assert c.get("/api/alerts").json()["total"] == 0


def test_dashboard_summary(login, world):
    c = as_role(login, "inventory_manager")
    c.post("/api/inventory/receive", json={"consumable_id": world["gloves"].id, "quantity": 300, "lot_number": "A",
                                           "unit_cost": 10})
    c.post("/api/inventory/issue", json={"consumable_id": world["gloves"].id, "quantity": 20,
                                         "department_id": world["ortho"].id})
    d = c.get("/api/dashboard/summary").json()
    assert d["items_monitored"] == 1
    assert d["status_counts"]["OK"] == 1
    assert d["stock_value"] == 2800
    assert d["daily"][-1]["issued"] == 20 and d["daily"][-1]["received"] == 300
    assert d["consumption_by_department"] == [{"name": "Orthopaedics", "value": 800.0}]
    assert d["top_consumed"][0] == {"name": "Surgical gloves 7", "value": 800.0}
    assert len(d["recent_movements"]) == 2


def test_seed_runs(db):
    from sqlalchemy import func, select

    from app.models import Alert, StockMovement
    from app.seed import seed

    h = seed(db, days=12, verbose=False)
    assert db.scalar(select(func.count(StockMovement.id))) > 100
    assert db.scalar(select(func.count(Alert.id)).where(Alert.hospital_id == h.id)) > 0
    # ledger chain: each movement's balance_after = previous balance + quantity, in time order
    prev: dict[int, int] = {}
    for m in db.scalars(select(StockMovement).order_by(StockMovement.created_at, StockMovement.id)):
        assert m.balance_after == prev.get(m.consumable_id, 0) + m.quantity
        prev[m.consumable_id] = m.balance_after
