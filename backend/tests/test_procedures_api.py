"""V2B — procedure types, schedule and item mappings: CRUD, validation, RBAC, isolation, audit."""

from datetime import timedelta

import pytest

from app.core.security import business_today
from tests.conftest import as_role


def _type(c, world, code="HERNIA", dept="ortho", **kw):
    r = c.post("/api/procedures/types", json={"code": code, "name": f"{code} procedure",
                                               "department_id": world[dept].id, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _day(n: int) -> str:
    return (business_today() + timedelta(days=n)).isoformat()


# ---------------------------------------------------------------- types


def test_create_and_update_type(login, world):
    admin = as_role(login, "admin")
    t = _type(admin, world, code="tkr", avg_duration_minutes=120)
    assert t["code"] == "TKR" and t["department"]["code"] == "ORTHO" and t["is_synthetic"] is False
    assert admin.post("/api/procedures/types", json={"code": "TKR", "name": "Dup", "department_id": world["ortho"].id}).status_code == 409
    r = admin.patch(f"/api/procedures/types/{t['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert all(x["id"] != t["id"] for x in admin.get("/api/procedures/types").json())
    assert any(x["id"] == t["id"] for x in admin.get("/api/procedures/types", params={"include_inactive": True}).json())


def test_type_validation(login, world):
    admin = as_role(login, "admin")
    assert admin.post("/api/procedures/types", json={"code": "X", "name": "x", "department_id": world["ortho"].id}).status_code == 422
    assert admin.post("/api/procedures/types", json={"code": "OK1", "name": "ok", "department_id": 99999}).status_code == 404
    foreign = {"code": "OK2", "name": "ok", "department_id": world["other_dept"].id}
    assert admin.post("/api/procedures/types", json=foreign).status_code == 404
    assert admin.post("/api/procedures/types", json={"code": "OK3", "name": "ok", "department_id": world["ortho"].id,
                                                     "avg_duration_minutes": -5}).status_code == 422


@pytest.mark.parametrize("role,expected", [
    ("admin", 201), ("department_manager", 201), ("procurement_manager", 403), ("inventory_manager", 403), ("viewer", 403),
])
def test_type_permissions(login, world, role, expected):
    c = as_role(login, role)
    r = c.post("/api/procedures/types", json={"code": f"P{role[:3].upper()}", "name": "Test", "department_id": world["ortho"].id})
    assert r.status_code == expected
    assert c.get("/api/procedures/types").status_code == 200  # everyone can read


def test_department_manager_limited_to_own_department(login, world):
    dm = as_role(login, "department_manager")  # ORTHO
    assert dm.post("/api/procedures/types", json={"code": "ICUX", "name": "ICU proc", "department_id": world["icu"].id}).status_code == 403
    admin = as_role(login, "admin")
    icu_type = _type(admin, world, code="ICUP", dept="icu")
    dm = as_role(login, "department_manager")
    assert dm.patch(f"/api/procedures/types/{icu_type['id']}", json={"name": "Renamed"}).status_code == 403
    body = {"procedure_type_id": icu_type["id"], "scheduled_date": _day(2), "count": 1}
    assert dm.post("/api/procedures/schedule", json=body).status_code == 403
    assert dm.post("/api/procedures/mappings", json={"procedure_type_id": icu_type["id"], "consumable_id": world["gloves"].id,
                                                     "quantity_per_procedure": 2}).status_code == 403


# ---------------------------------------------------------------- schedule


def test_schedule_lifecycle_and_validation(login, world):
    admin = as_role(login, "admin")
    t = _type(admin, world)
    body = {"procedure_type_id": t["id"], "scheduled_date": _day(3), "count": 5}
    r = admin.post("/api/procedures/schedule", json=body)
    assert r.status_code == 201 and r.json()["status"] == "SCHEDULED" and r.json()["department"]["code"] == "ORTHO"
    sid = r.json()["id"]
    # duplicate slot rejected
    assert admin.post("/api/procedures/schedule", json=body).status_code == 409
    # negative / zero / huge counts rejected
    for bad in (0, -3, 501):
        assert admin.post("/api/procedures/schedule", json={**body, "scheduled_date": _day(4), "count": bad}).status_code == 422
    # past date cannot be SCHEDULED, future cannot be COMPLETED, far dates rejected
    assert admin.post("/api/procedures/schedule", json={**body, "scheduled_date": _day(-2)}).status_code == 422
    assert admin.post("/api/procedures/schedule", json={**body, "scheduled_date": _day(5), "status": "COMPLETED"}).status_code == 422
    assert admin.post("/api/procedures/schedule", json={**body, "scheduled_date": _day(400)}).status_code == 422
    assert admin.post("/api/procedures/schedule", json={**body, "status": "CANCELLED"}).status_code == 422
    ok = admin.post("/api/procedures/schedule", json={**body, "scheduled_date": _day(-2), "status": "COMPLETED"})
    assert ok.status_code == 201
    # edit count
    r = admin.patch(f"/api/procedures/schedule/{sid}", json={"count": 8})
    assert r.status_code == 200 and r.json()["count"] == 8
    # completed can't be cancelled; scheduled can; cancelled frees the slot
    assert admin.post(f"/api/procedures/schedule/{ok.json()['id']}/cancel", json={}).status_code == 409
    r = admin.post(f"/api/procedures/schedule/{sid}/cancel", json={"reason": "Surgeon on leave"})
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED" and "Surgeon on leave" in r.json()["notes"]
    assert admin.post(f"/api/procedures/schedule/{sid}/cancel", json={}).status_code == 409
    assert admin.patch(f"/api/procedures/schedule/{sid}", json={"count": 2}).status_code == 409
    assert admin.post("/api/procedures/schedule", json=body).status_code == 201  # slot free again
    # filters
    active = admin.get("/api/procedures/schedule", params={"status": "active"}).json()
    assert active["total"] == 2
    cancelled = admin.get("/api/procedures/schedule", params={"status": "CANCELLED"}).json()
    assert cancelled["total"] == 1
    window = admin.get("/api/procedures/schedule", params={"date_from": _day(0), "date_to": _day(10)}).json()
    assert all(_day(0) <= r["scheduled_date"] <= _day(10) for r in window["items"])


def test_inactive_type_cannot_be_scheduled(login, world):
    admin = as_role(login, "admin")
    t = _type(admin, world, is_active=False)
    r = admin.post("/api/procedures/schedule", json={"procedure_type_id": t["id"], "scheduled_date": _day(1), "count": 1})
    assert r.status_code == 422 and "inactive" in r.json()["detail"]


def test_summary(login, world):
    admin = as_role(login, "admin")
    t = _type(admin, world)
    admin.post("/api/procedures/schedule", json={"procedure_type_id": t["id"], "scheduled_date": _day(1), "count": 4})
    admin.post("/api/procedures/schedule", json={"procedure_type_id": t["id"], "scheduled_date": _day(2), "count": 6})
    s = admin.get("/api/procedures/summary", params={"days": 14}).json()
    assert s["total_scheduled"] == 10 and s["by_type"][0]["code"] == "HERNIA"
    assert s["schedule_through"] == _day(2)
    types = admin.get("/api/procedures/types").json()
    assert types[0]["upcoming_count"] == 10


# ---------------------------------------------------------------- mappings


def test_mapping_crud_and_validation(login, world, db):
    admin = as_role(login, "admin")
    t = _type(admin, world)
    body = {"procedure_type_id": t["id"], "consumable_id": world["gloves"].id, "quantity_per_procedure": 4}
    r = admin.post("/api/procedures/mappings", json=body)
    assert r.status_code == 201 and r.json()["quantity_per_procedure"] == 4 and r.json()["consumable"]["sku"] == "GLV-7"
    mid = r.json()["id"]
    assert admin.post("/api/procedures/mappings", json=body).status_code == 409
    for q in (0, -1, 20000):
        assert admin.post("/api/procedures/mappings", json={**body, "quantity_per_procedure": q}).status_code == 422
    assert admin.post("/api/procedures/mappings", json={**body, "consumable_id": world["other_item"].id}).status_code == 404
    assert admin.post("/api/procedures/mappings", json={**body, "procedure_type_id": 99999}).status_code == 404
    r = admin.patch(f"/api/procedures/mappings/{mid}", json={"quantity_per_procedure": 2.5})
    assert r.json()["quantity_per_procedure"] == 2.5
    r = admin.patch(f"/api/procedures/mappings/{mid}", json={"is_active": False})
    assert r.json()["is_active"] is False
    assert admin.get("/api/procedures/mappings").json() == []
    assert len(admin.get("/api/procedures/mappings", params={"include_inactive": True}).json()) == 1
    assert admin.get("/api/procedures/types").json()[0]["mapping_count"] == 0
    # inactive item cannot be mapped
    world["gloves"].is_active = False
    db.commit()
    t2 = _type(admin, world, code="LAPC")
    r = admin.post("/api/procedures/mappings", json={**body, "procedure_type_id": t2["id"]})
    assert r.status_code == 422


def test_procedure_isolation_and_audit(login, world):
    admin = as_role(login, "admin")
    t = _type(admin, world)
    s = admin.post("/api/procedures/schedule", json={"procedure_type_id": t["id"], "scheduled_date": _day(1), "count": 3}).json()
    other = login("admin@other.demo")
    assert other.get("/api/procedures/types").json() == []
    assert other.get("/api/procedures/schedule").json()["total"] == 0
    assert other.patch(f"/api/procedures/types/{t['id']}", json={"name": "hack"}).status_code == 404
    assert other.post(f"/api/procedures/schedule/{s['id']}/cancel", json={}).status_code == 404
    body = {"procedure_type_id": t["id"], "scheduled_date": _day(1), "count": 1}
    assert other.post("/api/procedures/schedule", json=body).status_code == 404
    actions = {a["action"] for a in as_role(login, "admin").get("/api/audit-logs").json()["items"]}
    assert {"procedure_type.create", "procedure_schedule.create"} <= actions
