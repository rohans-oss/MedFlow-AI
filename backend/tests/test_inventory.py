from datetime import timedelta

from app.core.security import business_today
from tests.conftest import as_role


def _receive(c, item_id, qty, lot, expiry=None, **kw):
    body = {"consumable_id": item_id, "quantity": qty, "lot_number": lot, **kw}
    if expiry:
        body["expiry_date"] = expiry.isoformat()
    r = c.post("/api/inventory/receive", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_receive_creates_batch_and_movement(login, world):
    c = as_role(login, "inventory_manager")
    res = _receive(c, world["gloves"].id, 250, "LOT-A", business_today() + timedelta(days=400),
                   supplier_id=world["supplier"].id, unit_cost=42.5, reference="GRN-1")
    assert res["usable_stock"] == 250 and res["status"] == "OK"
    m = res["movements"][0]
    assert m["movement_type"] == "RECEIPT" and m["quantity"] == 250 and m["balance_after"] == 250
    assert m["supplier"]["code"] == "SUP"
    detail = c.get(f"/api/inventory/{world['gloves'].id}").json()
    assert detail["batches"][0]["unit_cost"] == 42.5
    assert detail["stock"]["stock_value"] == 250 * 42.5


def test_cannot_receive_expired_batch(login, world):
    c = as_role(login, "inventory_manager")
    r = c.post("/api/inventory/receive", json={"consumable_id": world["gloves"].id, "quantity": 5, "lot_number": "OLD",
                                               "expiry_date": (business_today() - timedelta(days=1)).isoformat()})
    assert r.status_code == 422


def test_issue_is_fefo_across_batches(login, world):
    c = as_role(login, "inventory_manager")
    today = business_today()
    _receive(c, world["gloves"].id, 100, "LATE", today + timedelta(days=300))
    _receive(c, world["gloves"].id, 30, "EARLY", today + timedelta(days=90))
    _receive(c, world["gloves"].id, 50, "NOEXP")
    r = c.post("/api/inventory/issue", json={"consumable_id": world["gloves"].id, "quantity": 50,
                                             "department_id": world["ortho"].id, "reference": "IND-1"})
    assert r.status_code == 201
    moves = r.json()["movements"]
    assert [(m["batch"]["lot_number"], m["quantity"]) for m in moves] == [("EARLY", -30), ("LATE", -20)]
    assert moves[-1]["balance_after"] == 130
    assert r.json()["usable_stock"] == 130


def test_issue_skips_expired_stock_and_rejects_shortfall(login, world, db):
    from app.models import StockBatch

    c = as_role(login, "inventory_manager")
    _receive(c, world["gloves"].id, 40, "GOOD", business_today() + timedelta(days=100))
    # an expired batch inserted directly (can't be received through the API)
    db.add(StockBatch(consumable_id=world["gloves"].id, lot_number="EXPIRED", quantity=500, initial_quantity=500,
                      expiry_date=business_today() - timedelta(days=2), unit_cost=40))
    db.commit()
    row = next(r for r in c.get("/api/inventory").json() if r["sku"] == "GLV-7")
    assert row["usable_stock"] == 40 and row["expired_stock"] == 500
    r = c.post("/api/inventory/issue", json={"consumable_id": world["gloves"].id, "quantity": 41,
                                             "department_id": world["icu"].id})
    assert r.status_code == 422 and "available 40" in r.json()["detail"]


def test_return_wastage_and_adjust(login, world):
    c = as_role(login, "inventory_manager")
    res = _receive(c, world["gloves"].id, 200, "LOT-R", business_today() + timedelta(days=200))
    batch_id = res["movements"][0]["batch"]["id"]
    c.post("/api/inventory/issue", json={"consumable_id": world["gloves"].id, "quantity": 20,
                                         "department_id": world["ortho"].id})
    # can't return more than the department took
    bad = c.post("/api/inventory/return", json={"batch_id": batch_id, "quantity": 21, "department_id": world["ortho"].id})
    assert bad.status_code == 422
    # ICU took nothing from this batch
    assert c.post("/api/inventory/return", json={"batch_id": batch_id, "quantity": 1,
                                                 "department_id": world["icu"].id}).status_code == 422
    ok = c.post("/api/inventory/return", json={"batch_id": batch_id, "quantity": 5, "department_id": world["ortho"].id})
    assert ok.status_code == 201 and ok.json()["usable_stock"] == 185

    w = c.post("/api/inventory/wastage", json={"batch_id": batch_id, "quantity": 10, "reason": "Packaging damaged"})
    assert w.status_code == 201 and w.json()["usable_stock"] == 175
    assert c.post("/api/inventory/wastage", json={"batch_id": batch_id, "quantity": 1000,
                                                  "reason": "too much"}).status_code == 422

    a = c.post("/api/inventory/adjust", json={"batch_id": batch_id, "counted_quantity": 170, "reason": "Cycle count"})
    assert a.status_code == 201
    assert a.json()["movements"][0]["quantity"] == -5 and a.json()["usable_stock"] == 170
    assert c.post("/api/inventory/adjust", json={"batch_id": batch_id, "counted_quantity": 170,
                                                 "reason": "Same"}).status_code == 422

    page = c.get("/api/inventory/movements", params={"consumable_id": world["gloves"].id}).json()
    types = [m["movement_type"] for m in page["items"]]
    assert page["total"] == 5
    assert set(types) == {"RECEIPT", "ISSUE", "RETURN", "WASTAGE", "ADJUSTMENT"}
    # ledger integrity: sum of movements equals on-hand
    assert sum(m["quantity"] for m in page["items"]) == 170


def test_movement_filters_and_pagination(login, world):
    c = as_role(login, "inventory_manager")
    _receive(c, world["gloves"].id, 500, "LOT-P")
    for _ in range(3):
        c.post("/api/inventory/issue", json={"consumable_id": world["gloves"].id, "quantity": 1,
                                             "department_id": world["icu"].id})
    p = c.get("/api/inventory/movements", params={"movement_type": "ISSUE", "page_size": 2}).json()
    assert p["total"] == 3 and len(p["items"]) == 2
    p = c.get("/api/inventory/movements", params={"department_id": world["ortho"].id}).json()
    assert p["total"] == 0
    today = business_today().isoformat()
    assert c.get("/api/inventory/movements", params={"date_from": today, "date_to": today}).json()["total"] == 4


def test_status_filter_and_levels_validation(login, world):
    c = as_role(login, "inventory_manager")
    assert [r["sku"] for r in c.get("/api/inventory", params={"status": "OUT_OF_STOCK"}).json()] == ["GLV-7"]
    _receive(c, world["gloves"].id, 60, "L")
    assert [r["sku"] for r in c.get("/api/inventory", params={"status": "LOW"}).json()] == ["GLV-7"]
    r = c.patch(f"/api/consumables/{world['gloves'].id}", json={"max_level": 10})
    assert r.status_code == 422


def test_audit_log_records_stock_changes(login, world):
    inv = as_role(login, "inventory_manager")
    _receive(inv, world["gloves"].id, 10, "AUD")
    admin = as_role(login, "admin")
    actions = [a["action"] for a in admin.get("/api/audit-logs").json()["items"]]
    assert "stock.receive" in actions and "auth.login" in actions


def test_supplier_catalogue_single_preferred(login, world):
    c = as_role(login, "procurement_manager")
    s2 = c.post("/api/suppliers", json={"code": "SUP2", "name": "Second Supplier", "gstin": "",
                                        "email": ""}).json()
    p1 = c.post(f"/api/suppliers/{world['supplier'].id}/products",
                json={"consumable_id": world["gloves"].id, "unit_price": 40, "is_preferred": True})
    assert p1.status_code == 201
    p2 = c.post(f"/api/suppliers/{s2['id']}/products",
                json={"consumable_id": world["gloves"].id, "unit_price": 38, "is_preferred": True, "moq": 100})
    assert p2.status_code == 201
    detail = c.get(f"/api/inventory/{world['gloves'].id}").json()
    preferred = [s["supplier"]["code"] for s in detail["suppliers"] if s["is_preferred"]]
    assert preferred == ["SUP2"]
    dup = c.post(f"/api/suppliers/{s2['id']}/products", json={"consumable_id": world["gloves"].id, "unit_price": 1})
    assert dup.status_code == 409
    lst = c.get("/api/suppliers").json()
    assert {s["code"]: s["product_count"] for s in lst} == {"SUP": 1, "SUP2": 1}
