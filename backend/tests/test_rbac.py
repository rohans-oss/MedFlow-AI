import pytest

from tests.conftest import as_role

NEW_ITEM = {"sku": "SYR-5", "name": "Syringe 5 ml", "unit": "piece", "unit_cost": 4, "reorder_level": 50}


@pytest.mark.parametrize("role,expected", [
    ("admin", 201), ("procurement_manager", 201), ("inventory_manager", 201),
    ("department_manager", 403), ("viewer", 403),
])
def test_catalog_permissions(login, world, role, expected):
    c = as_role(login, role)
    assert c.post("/api/consumables", json={**NEW_ITEM, "sku": f"SYR-{role[:4]}"}).status_code == expected


@pytest.mark.parametrize("role,expected", [
    ("admin", 201), ("procurement_manager", 403), ("inventory_manager", 201),
    ("department_manager", 403), ("viewer", 403),
])
def test_receive_permissions(login, world, role, expected):
    c = as_role(login, role)
    r = c.post("/api/inventory/receive", json={"consumable_id": world["gloves"].id, "quantity": 10, "lot_number": "L1"})
    assert r.status_code == expected


@pytest.mark.parametrize("role,expected", [
    ("admin", 201), ("procurement_manager", 201), ("inventory_manager", 403), ("viewer", 403),
])
def test_supplier_permissions(login, world, role, expected):
    c = as_role(login, role)
    assert c.post("/api/suppliers", json={"code": f"S{role[:3]}", "name": "New Supplier"}).status_code == expected


def test_department_manager_can_only_issue_to_own_department(login, world):
    inv = as_role(login, "inventory_manager")
    inv.post("/api/inventory/receive", json={"consumable_id": world["gloves"].id, "quantity": 100, "lot_number": "L1"})
    dm = as_role(login, "department_manager")
    ok = dm.post("/api/inventory/issue", json={"consumable_id": world["gloves"].id, "quantity": 5,
                                                 "department_id": world["ortho"].id})
    assert ok.status_code == 201
    denied = dm.post("/api/inventory/issue", json={"consumable_id": world["gloves"].id, "quantity": 5,
                                                     "department_id": world["icu"].id})
    assert denied.status_code == 403


def test_only_admin_reads_audit_and_manages_users(login, world):
    for role in ("procurement_manager", "inventory_manager", "department_manager", "viewer"):
        c = as_role(login, role)
        assert c.get("/api/audit-logs").status_code == 403
        assert c.get("/api/users").status_code == 403
    admin = as_role(login, "admin")
    assert admin.get("/api/audit-logs").status_code == 200
    r = admin.post("/api/users", json={"email": "new@test.demo", "full_name": "New User", "password": "Secret123",
                                       "role": "viewer"})
    assert r.status_code == 201


def test_department_manager_requires_department(login, world):
    admin = as_role(login, "admin")
    r = admin.post("/api/users", json={"email": "dm2@test.demo", "full_name": "DM Two", "password": "Secret123",
                                       "role": "department_manager"})
    assert r.status_code == 422


def test_admin_cannot_demote_self(login, world):
    admin = as_role(login, "admin")
    me = admin.get("/api/auth/me").json()
    assert admin.patch(f"/api/users/{me['id']}", json={"role": "viewer"}).status_code == 400


def test_hospital_isolation(login, world):
    c = as_role(login, "admin")
    assert c.get(f"/api/inventory/{world['other_item'].id}").status_code == 404
    assert c.post("/api/inventory/receive", json={"consumable_id": world["other_item"].id, "quantity": 1,
                                                  "lot_number": "X"}).status_code == 404
    assert c.post("/api/inventory/issue", json={"consumable_id": world["gloves"].id, "quantity": 1,
                                                "department_id": world["other_dept"].id}).status_code == 404
    skus = [r["sku"] for r in c.get("/api/inventory").json()]
    assert "X-1" not in skus
