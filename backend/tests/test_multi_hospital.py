"""V8 — multi-hospital SaaS: tenant isolation tested aggressively at every layer.

World: hospital A ("Test Hospital", organization TESTORG) with the V5/V6 test data, and hospital B ("Other Hospital",
organization OTHERORG) with its own copy of the same *shape* of data — same SKU and supplier codes (allowed: codes are
unique per hospital) but every B name contains the marker "ZZB", so any leak into an A response is detectable.

Layers: database (ORM guard, PostgreSQL triggers, per-hospital uniqueness), authentication (memberships, active hospital,
switching, token binding, stale-tab header), every API route that takes an id (A user × B ids), body-id attacks, list /
search / pagination, settings, alerts / risk refresh, knowledge graph, AI assistant (tools, cross-hospital questions,
conversations), audit logs, organization and platform administration, onboarding, CSV import, roles.
"""

import re
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from app.core.permissions import ROLE_PERMISSIONS
from app.core.security import business_today
from app.db.tenant_guard import TenantViolation
from app.main import app
from app.models import (
    Alert,
    AssistantConversation,
    AuditLog,
    Consumable,
    Department,
    Hospital,
    HospitalMembership,
    ModelVersion,
    Organization,
    OrganizationMembership,
    ProcedureItemMapping,
    ProcedureSchedule,
    ProcedureType,
    ProcurementRecommendation,
    ProcurementSettings,
    Role,
    StockMovement,
    StockoutPrediction,
    Supplier,
    SupplierOrder,
    SupplierProduct,
    TenantStatus,
    User,
)
from app.services import alerts
from app.services import procurement as procurement_service
from tests.conftest import PASSWORD, TEST_DB_URL, as_role
from tests.test_graph import _graph_world
from tests.test_procurement import _world

MARK = "ZZB"


# ---------------------------------------------------------------- worlds


def _b_world(db, world) -> dict:
    """Hospital B: same shape of data as A (V5 _world), every name marked ZZB."""
    other, admin_b = world["other"], world["users"]["other_admin"]
    item = world["other_item"]
    item.name, item.sku, item.unit, item.reorder_level, item.max_level = f"{MARK} Secret gloves", "GLV-7", "pair", 100, 1000
    sup = Supplier(hospital_id=other.id, code="SUP", name=f"{MARK} Supplier", default_lead_time_days=3)
    dept = Department(hospital_id=other.id, code="ORTHO", name=f"{MARK} Orthopaedics")  # same code as A's: allowed
    db.add_all([sup, dept])
    db.flush()
    bw = {"hospital": other, "gloves": item, "supplier": sup, "users": {"admin": admin_b}}
    rely, cheap = _world(db, bw)
    rely.name, cheap.name = f"{MARK} Reliable", f"{MARK} Cheap"
    pt = ProcedureType(hospital_id=other.id, department_id=dept.id, code="TKR", name=f"{MARK} Knee", is_synthetic=True)
    db.add(pt)
    db.flush()
    mapping = ProcedureItemMapping(hospital_id=other.id, procedure_type_id=pt.id, consumable_id=item.id,
                                   quantity_per_procedure=2, is_synthetic=True)
    sched = ProcedureSchedule(hospital_id=other.id, procedure_type_id=pt.id, department_id=dept.id,
                              scheduled_date=business_today() + timedelta(days=2), count=3, is_synthetic=True)
    today = business_today()
    mv = ModelVersion(hospital_id=other.id, run_id="b", name=f"{MARK}_model", model_type="moving_average_7",
                      data_start=today, data_end=today, test_start=today, test_end=today, horizon_days=30, n_series=1,
                      n_train_rows=1, dataset_hash="b", metrics={})
    db.add_all([mapping, sched, mv])
    db.commit()
    alerts.evaluate(db, other.id)
    procurement_service.generate(db, other.id, None)
    db.commit()
    rec = db.scalar(select(ProcurementRecommendation).where(ProcurementRecommendation.hospital_id == other.id))
    order = db.scalar(select(SupplierOrder).where(SupplierOrder.hospital_id == other.id))
    alert = db.scalar(select(Alert).where(Alert.hospital_id == other.id))
    sp = db.scalar(select(SupplierProduct).where(SupplierProduct.supplier_id == rely.id))
    cat = item.category_id
    from app.models import RiskModelVersion

    risk_model = db.scalar(select(RiskModelVersion).where(RiskModelVersion.hospital_id == other.id))
    # V9: an integration source with an API key, a sync run (with a rejected record) and a reconciliation issue
    from app.integrations import credentials as icred
    from app.integrations import engine as ieng
    from app.integrations import sources as isrc
    from app.models import ReconciliationIssue

    src = isrc.create_source(db, other.id, None, f"{MARK} ERP", "erp", "api_push", ["items", "inventory"], {})
    cred, _key = icred.create(db, src, f"{MARK} key", None)
    run = ieng.run_sync(db, src, "inventory", [{"item_code": "GLV-7", "quantity": 99999}, {"item_code": f"{MARK}-X",
                                                                                          "quantity": 1}], trigger="api")
    db.commit()
    issue = db.scalar(select(ReconciliationIssue).where(ReconciliationIssue.hospital_id == other.id))
    # V10: a pilot with an issue, feedback and a saved report
    from app.models import Pilot, PilotFeedback, PilotIssue, PilotReportSnapshot

    pilot = Pilot(hospital_id=other.id, organization_id=other.organization_id, name=f"{MARK} pilot", status="active",
                  data_classification="observed", baseline_start=today - timedelta(days=40), baseline_end=today - timedelta(days=21),
                  pilot_start=today - timedelta(days=20), pilot_end=today + timedelta(days=9), external_factors=[])
    db.add(pilot)
    db.flush()
    p_issue = PilotIssue(hospital_id=other.id, pilot_id=pilot.id, category="other", title=f"{MARK} issue")
    snap = PilotReportSnapshot(hospital_id=other.id, pilot_id=pilot.id, data_classification="observed", report={},
                               markdown=f"# {MARK} report")
    db.add_all([p_issue, snap, PilotFeedback(hospital_id=other.id, pilot_id=pilot.id, target_type="workflow", rating="useful",
                                             comment=f"{MARK} comment")])
    db.commit()
    return {"pilot": pilot, "pilot_issue": p_issue, "report_snapshot": snap,
            "integration_source": src, "credential": cred, "sync_run": run, "recon_issue": issue,
            "risk_model": risk_model, "hospital": other, "item": item, "supplier": rely, "cheap": cheap, "dept": dept,
            "type": pt, "mapping": mapping,
            "schedule": sched, "model": mv, "rec": rec, "order": order, "alert": alert, "product": sp, "cat": cat,
            "admin": admin_b}


@pytest.fixture()
def two(db, world):
    """A (full V5/V6 test data) + B (same shape, ZZB-marked) + B's assistant conversation."""
    _graph_world(db, world)
    b = _b_world(db, world)
    return b


def _no_leak(resp) -> None:
    assert MARK not in resp.text, f"hospital B data leaked: {resp.request.method} {resp.request.url} → {resp.text[:300]}"


def _login(client, email: str):
    client.cookies.clear()
    pw = "Demo@1234" if email.endswith((".demo",)) and not email.endswith("@test.demo") and email != "admin@other.demo" else PASSWORD
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return client


# ---------------------------------------------------------------- database layer


def test_legacy_user_creation_adds_membership_and_active_pointer_is_guarded(db, world):
    u = world["users"]["viewer"]
    m = db.scalar(select(HospitalMembership).where(HospitalMembership.user_id == u.id))
    assert (m.hospital_id, m.role, m.status) == (world["hospital"].id, Role.VIEWER, TenantStatus.ACTIVE)
    u.hospital_id = world["other"].id  # no membership there
    with pytest.raises(TenantViolation):
        db.flush()
    db.rollback()


def test_orm_guard_rejects_rows_linking_two_hospitals(db, world, two):
    a_sup, b_item = world["supplier"], two["item"]
    cases = [
        SupplierProduct(supplier_id=a_sup.id, consumable_id=b_item.id, unit_price=1, lead_time_days=1, moq=1),
        ProcedureItemMapping(hospital_id=world["hospital"].id, procedure_type_id=db.scalar(
            select(ProcedureType.id).where(ProcedureType.hospital_id == world["hospital"].id)),
            consumable_id=b_item.id, quantity_per_procedure=1),
        HospitalMembership(user_id=world["users"]["viewer"].id, hospital_id=world["other"].id, role=Role.VIEWER,
                           department_id=world["ortho"].id),
        StockMovement(hospital_id=world["hospital"].id, consumable_id=b_item.id, movement_type="ISSUE", quantity=-1,
                      balance_after=0),
    ]
    for obj in cases:
        db.add(obj)
        with pytest.raises(TenantViolation):
            db.flush()
        db.rollback()


@pytest.mark.skipif(not TEST_DB_URL.startswith("postgresql"), reason="PostgreSQL triggers (the ORM guard covers SQLite)")
def test_postgres_triggers_reject_cross_hospital_rows_even_without_the_orm(db, world, two):
    from sqlalchemy.exc import DBAPIError

    a_sup, b_item = world["supplier"].id, two["item"].id
    stmts = [
        f"INSERT INTO supplier_products (supplier_id, consumable_id, unit_price, lead_time_days, moq, is_preferred, "
        f"created_at, updated_at) VALUES ({a_sup}, {b_item}, 1, 1, 1, false, now(), now())",
        f"UPDATE stock_movements SET consumable_id = {b_item} WHERE hospital_id = {world['hospital'].id}",
        f"UPDATE procedure_item_mappings SET consumable_id = {world['gloves'].id} WHERE hospital_id = {world['other'].id}",
    ]
    for s in stmts:
        with pytest.raises(DBAPIError, match="tenant violation"):
            db.execute(text(s))
        db.rollback()
    # the active-hospital pointer needs an active membership (deferred: checked at commit)
    db.execute(text(f"UPDATE users SET hospital_id = {world['other'].id} WHERE id = {world['users']['viewer'].id}"))
    with pytest.raises(DBAPIError, match="no active membership"):
        db.commit()
    db.rollback()


def test_codes_are_unique_per_hospital_not_globally(db, world, two):
    # A and B both have SKU GLV-7, supplier codes SUP / RELY / CHEAP and department ORTHO — allowed
    for model, col in ((Consumable, Consumable.sku), (Supplier, Supplier.code), (Department, Department.code)):
        dup = db.execute(select(col, func.count(func.distinct(model.hospital_id))).group_by(col)
                         .having(func.count(func.distinct(model.hospital_id)) > 1)).all()
        assert dup, model
    db.add(Consumable(hospital_id=world["hospital"].id, sku="GLV-7", name="dup", unit="pair"))
    with pytest.raises(Exception, match="(?i)unique|duplicate"):
        db.flush()
    db.rollback()


# ---------------------------------------------------------------- authentication, memberships, switching


def _two_hospital_user(db, world) -> User:
    """An account that is ADMIN of A and VIEWER of B."""
    u = User(email="multi@test.demo", full_name="Multi", hashed_password=world["users"]["admin"].hashed_password,
             hospital_id=world["hospital"].id, role=Role.ADMIN)
    db.add(u)
    db.flush()
    db.add(HospitalMembership(user_id=u.id, hospital_id=world["other"].id, role=Role.VIEWER, status=TenantStatus.ACTIVE))
    db.commit()
    return u


def test_me_lists_memberships_and_switching_changes_everything_server_side(client, db, world, two):
    _two_hospital_user(db, world)
    c = _login(client, "multi@test.demo")
    me = c.get("/api/auth/me").json()
    assert me["hospital"]["id"] == world["hospital"].id and me["role"] == "admin"
    assert {(m["hospital_id"], m["role"]) for m in me["memberships"]} == {(world["hospital"].id, "admin"), (world["other"].id, "viewer")}
    old_token = c.cookies.get("access_token")
    a_items = c.get("/api/consumables").json()
    _no_leak(c.get("/api/consumables"))
    a_ids = {i["id"] for i in a_items}

    r = c.post(f"/api/hospitals/{world['other'].id}/switch")
    assert r.status_code == 200 and r.json()["hospital"]["id"] == world["other"].id and r.json()["role"] == "viewer"
    assert set(r.json()["permissions"]) == ROLE_PERMISSIONS[Role.VIEWER]
    b_items = c.get("/api/consumables").json()
    assert {i["id"] for i in b_items}.isdisjoint(a_ids) and all(MARK in i["name"] for i in b_items)
    assert c.get(f"/api/consumables/{world['gloves'].id}").status_code == 404  # A's item is gone after the switch
    assert c.post("/api/categories", json={"name": "x"}).status_code == 403  # viewer in B
    # an access token issued for A no longer works
    r = c.get("/api/consumables", headers={"Authorization": f"Bearer {old_token}"})  # bearer wins over the cookie
    assert r.status_code == 401
    # a tab still showing A gets 409 instead of acting on B
    assert c.get("/api/consumables", headers={"X-MedFlow-Hospital": str(world["hospital"].id)}).status_code == 409
    assert c.get("/api/consumables", headers={"X-MedFlow-Hospital": str(world["other"].id)}).status_code == 200
    # switch back; audit rows land in the hospital switched to
    assert c.post(f"/api/hospitals/{world['hospital'].id}/switch").json()["role"] == "admin"
    db.expire_all()
    sw = db.scalars(select(AuditLog).where(AuditLog.action == "hospital.switch").order_by(AuditLog.id)).all()
    assert [a.hospital_id for a in sw] == [world["other"].id, world["hospital"].id]


def test_switching_requires_a_usable_membership(client, db, world, two):
    c = as_role(lambda e: _login(client, e), "admin")
    assert c.post(f"/api/hospitals/{world['other'].id}/switch").status_code == 404  # not a member
    assert c.post("/api/hospitals/999999/switch").status_code == 404  # same answer: no existence hint
    u = _two_hospital_user(db, world)
    m = db.scalar(select(HospitalMembership).where(HospitalMembership.user_id == u.id,
                                                   HospitalMembership.hospital_id == world["other"].id))
    c = _login(client, "multi@test.demo")
    m.status = TenantStatus.SUSPENDED
    db.commit()
    assert c.post(f"/api/hospitals/{world['other'].id}/switch").status_code == 404
    m.status = TenantStatus.ACTIVE
    world["other"].status = TenantStatus.SUSPENDED  # a suspended hospital is unreachable for everyone
    db.commit()
    assert c.post(f"/api/hospitals/{world['other'].id}/switch").status_code == 404
    world["other"].status = TenantStatus.ACTIVE
    world["other_org"].status = TenantStatus.SUSPENDED  # …and so is every hospital of a suspended organization
    db.commit()
    assert c.post(f"/api/hospitals/{world['other'].id}/switch").status_code == 404


def test_revoking_the_active_membership_removes_access_immediately(client, db, world):
    c = _login(client, "inventory_manager@test.demo")
    assert c.get("/api/inventory").status_code == 200
    m = db.scalar(select(HospitalMembership).where(HospitalMembership.user_id == world["users"]["inventory_manager"].id))
    m.status = TenantStatus.SUSPENDED
    db.commit()
    assert c.get("/api/inventory").status_code == 403  # context cleared on this request
    assert c.get("/api/inventory").status_code == 401  # the token was bound to the old hospital
    assert c.post("/api/auth/refresh").status_code == 200
    assert c.get("/api/inventory").status_code == 403  # no hospital selected
    me = c.get("/api/auth/me").json()
    assert me["hospital"] is None and me["permissions"] == [] and me["memberships"][0]["available"] is False


def test_platform_admin_manages_organizations_but_sees_no_hospital_data(client, db, world, two):
    db.add(User(email="platform@test.demo", full_name="Platform", hashed_password=world["users"]["admin"].hashed_password,
                hospital_id=None, role=None, is_platform_admin=True))
    db.commit()
    c = _login(client, "platform@test.demo")
    me = c.get("/api/auth/me").json()
    assert me["is_platform_admin"] and me["hospital"] is None and me["memberships"] == []
    for url in ("/api/inventory", "/api/consumables", "/api/dashboard/summary", "/api/stockout-risks",
                "/api/assistant/conversations", "/api/audit-logs"):
        assert c.get(url).status_code == 403, url
    orgs = c.get("/api/organizations").json()
    assert {o["code"] for o in orgs} >= {"TESTORG", "OTHERORG"}
    r = c.post("/api/organizations", json={"name": "New Group", "code": "newgrp"})
    assert r.status_code == 201 and r.json()["code"] == "NEWGRP"
    assert c.get(f"/api/organizations/{world['org'].id}/overview").status_code == 404  # no operational data
    assert c.post(f"/api/hospitals/{world['hospital'].id}/switch").status_code == 404
    pa = c.get("/api/platform/audit-logs").json()
    assert pa["total"] >= 1 and all(i["action"].startswith(("organization.", "auth.")) for i in pa["items"])
    assert as_role(lambda e: _login(client, e), "admin").get("/api/platform/audit-logs").status_code == 403


# ---------------------------------------------------------------- every id-taking route, A user × B ids


def _path_params(two, world) -> dict[str, int | str]:
    conv = two["conversation"]
    return {"dept_id": two["dept"].id, "user_id": two["admin"].id, "cat_id": two["cat"], "item_id": two["item"].id,
            "consumable_id": two["item"].id, "supplier_id": two["supplier"].id, "product_id": two["product"].id,
            "alert_id": two["alert"].id, "model_id": two["model"].id, "type_id": two["type"].id,
            "row_id": two["schedule"].id, "mapping_id": two["mapping"].id, "order_id": two["order"].id,
            "rec_id": two["rec"].id, "conversation_id": conv, "hospital_id": world["other"].id,
            "org_id": world["other_org"].id, "risk_model_id": two["risk_model"].id,
            "source_id": two["integration_source"].id, "credential_id": two["credential"].id,
            "run_id": two["sync_run"].id, "issue_id": two["recon_issue"].id, "entity": "items",
            "hospital_code": world["other"].code, "resource": "materials", "pilot_id": two["pilot"].id,
            "pilot_issue_id": two["pilot_issue"].id, "snapshot_id": two["report_snapshot"].id, "item_key": "no_patient_data"}


def test_every_route_with_an_id_rejects_another_hospitals_ids(client, db, world, two, monkeypatch):
    from app.graph import store
    monkeypatch.setattr("app.core.config.settings.GRAPH_BACKEND", "disabled")
    store.reset()
    b = _login(client, "admin@other.demo")
    two["conversation"] = b.post("/api/assistant/ask", json={"question": "Why are ZZB secret gloves at risk?"}).json()[
        "conversation_id"]
    params = _path_params(two, world)
    before = (db.scalar(select(func.count(StockMovement.id))), db.scalar(select(func.count(SupplierOrder.id))),
              db.scalar(select(func.count(ProcurementRecommendation.id))))
    spec = app.openapi()
    checked = 0
    for role in ("admin", "procurement_manager", "viewer"):
        c = as_role(lambda e: _login(client, e), role)
        for path, ops in spec["paths"].items():
            names = re.findall(r"{(\w+)}", path)
            if not names or set(names) & {"kind", "name"} or path.startswith("/api/ingest/"):
                continue  # ingest is API-key authenticated (tested in test_integrations)
            vals = {n: params[n] for n in names}
            if "stockout-risks/models" in path:
                vals["model_id"] = params["risk_model_id"]
            url = path.format(**vals)
            for method in ops:
                r = c.request(method.upper(), url, json={})
                assert r.status_code in (400, 403, 404, 405, 409, 422), f"{role} {method.upper()} {url} → {r.status_code} {r.text[:200]}"
                _no_leak(r)
                checked += 1
    assert checked == 3 * 88  # every (method, path) with an id parameter, for three roles (V9: +16, V10: +22)
    db.expire_all()
    after = (db.scalar(select(func.count(StockMovement.id))), db.scalar(select(func.count(SupplierOrder.id))),
             db.scalar(select(func.count(ProcurementRecommendation.id))))
    assert after == before
    store.reset()


def test_ids_of_another_hospital_in_request_bodies_are_rejected(client, db, world, two):
    c = as_role(lambda e: _login(client, e), "admin")
    a_item, a_sup, b = world["gloves"].id, world["supplier"].id, two
    a_type = db.scalar(select(ProcedureType.id).where(ProcedureType.hospital_id == world["hospital"].id))
    before = (db.scalar(select(func.count(StockMovement.id))), db.scalar(select(func.count(SupplierOrder.id))),
              db.scalar(select(func.count(ProcedureSchedule.id))), db.scalar(select(func.count(ProcedureItemMapping.id))),
              db.scalar(select(func.count(ProcurementRecommendation.id))))
    attacks = [
        ("POST", "/api/inventory/issue", {"consumable_id": a_item, "quantity": 1, "department_id": b["dept"].id}),
        ("POST", "/api/inventory/issue", {"consumable_id": b["item"].id, "quantity": 1, "department_id": world["ortho"].id}),
        ("POST", "/api/inventory/receive", {"consumable_id": a_item, "quantity": 1, "lot_number": "X", "supplier_id": b["supplier"].id}),
        ("POST", "/api/inventory/receive", {"consumable_id": a_item, "quantity": 1, "lot_number": "X",
                                            "supplier_order_id": b["order"].id}),
        ("POST", "/api/supplier-orders", {"supplier_id": b["supplier"].id, "consumable_id": a_item, "quantity_ordered": 5,
                                          "unit_price": 1}),
        ("POST", "/api/supplier-orders", {"supplier_id": a_sup, "consumable_id": b["item"].id, "quantity_ordered": 5,
                                          "unit_price": 1}),
        ("POST", "/api/procedures/schedule", {"procedure_type_id": b["type"].id, "scheduled_date": str(business_today()),
                                              "count": 1}),
        ("POST", "/api/procedures/schedule", {"procedure_type_id": a_type, "department_id": b["dept"].id,
                                              "scheduled_date": str(business_today()), "count": 1}),
        ("POST", "/api/procedures/mappings", {"procedure_type_id": a_type, "consumable_id": b["item"].id,
                                              "quantity_per_procedure": 1}),
        ("POST", f"/api/suppliers/{a_sup}/products", {"consumable_id": b["item"].id, "unit_price": 1, "lead_time_days": 1}),
        ("POST", "/api/procurement/recommendations/generate", {"item_ids": [b["item"].id]}),
        ("POST", f"/api/procurement/items/{a_item}/what-if", {"delays": [{"supplier_id": b["supplier"].id, "days": 2}]}),
        ("POST", "/api/users", {"email": "x@test.demo", "full_name": "X Y", "password": "Passw0rd!", "role": "department_manager",
                                "department_id": b["dept"].id}),
        ("POST", "/api/consumables", {"sku": "NEW-1", "name": "n", "unit": "u", "category_id": b["cat"]}),
    ]
    for method, url, body in attacks:
        r = c.request(method, url, json=body)
        assert not 200 <= r.status_code < 300 or (url.endswith("generate") and r.json().get("created", 0) == 0), \
            f"{method} {url} {body} → {r.status_code} {r.text[:200]}"
        _no_leak(r)
    db.expire_all()
    after = (db.scalar(select(func.count(StockMovement.id))), db.scalar(select(func.count(SupplierOrder.id))),
             db.scalar(select(func.count(ProcedureSchedule.id))), db.scalar(select(func.count(ProcedureItemMapping.id))),
             db.scalar(select(func.count(ProcurementRecommendation.id))))
    assert after == before


def test_lists_search_filters_and_pagination_never_include_another_hospital(client, db, world, two):
    spec = app.openapi()
    c = as_role(lambda e: _login(client, e), "admin")
    n = 0
    for path, ops in spec["paths"].items():
        if "{" in path or "get" not in ops or path.startswith(("/api/platform", "/api/docs", "/api/openapi")):
            continue
        for q in ({}, {"search": MARK}, {"q": MARK}, {"page": 2, "page_size": 1}, {"status": "all"}, {"include_inactive": True}):
            r = c.get(path, params=q)
            assert r.status_code < 500 or (r.status_code == 503 and "/graph/" in path), path
            _no_leak(r)
            n += 1
    assert n > 200
    # and B, symmetrically, sees nothing of A (A's names are the V5 test names)
    b = _login(client, "admin@other.demo")
    assert b.get("/api/consumables", params={"search": "Surgical"}).json() == []


# ---------------------------------------------------------------- hospital-specific configuration and derived data


def test_procurement_settings_are_per_hospital(client, db, world, two):
    a = as_role(lambda e: _login(client, e), "admin")
    vals = a.get("/api/procurement/settings").json()["values"]
    r = a.put("/api/procurement/settings", json=vals | {"service_level": 0.99, "stockout_cost_multiplier": 9.0})
    assert r.status_code == 200, r.text
    b = _login(client, "admin@other.demo")
    bv = b.get("/api/procurement/settings").json()
    assert bv["values"]["service_level"] == 0.95 and bv["values"]["stockout_cost_multiplier"] == 5.0 and bv["is_default"]
    db.expire_all()
    rows = db.scalars(select(ProcurementSettings)).all()
    assert [(s.hospital_id, s.service_level) for s in rows] == [(world["hospital"].id, 0.99)]


def test_alert_evaluation_and_risk_refresh_only_touch_their_hospital(db, world, two):
    from app.risk import engine as risk_engine

    b_alerts = db.scalar(select(func.count(Alert.id)).where(Alert.hospital_id == world["other"].id))
    b_risk = db.scalar(select(func.count(StockoutPrediction.id)).where(StockoutPrediction.hospital_id == world["other"].id))
    alerts.evaluate(db, world["hospital"].id)
    risk_engine.refresh(db, world["hospital"].id)
    db.commit()
    assert db.scalar(select(func.count(Alert.id)).where(Alert.hospital_id == world["other"].id)) == b_alerts
    assert db.scalar(select(func.count(StockoutPrediction.id)).where(StockoutPrediction.hospital_id == world["other"].id)) == b_risk


def test_recommendations_orders_and_approvals_stay_in_their_hospital(client, db, world, two):
    a = as_role(lambda e: _login(client, e), "admin")
    gen = a.post("/api/procurement/recommendations/generate", json={})
    assert gen.status_code == 201
    a_recs = a.get("/api/procurement/recommendations", params={"status": "PENDING"}).json()
    _no_leak(a.get("/api/procurement/recommendations", params={"status": "PENDING"}))
    assert a_recs and two["rec"].id not in {r["id"] for r in a_recs}
    assert a.post(f"/api/procurement/recommendations/{two['rec'].id}/approve", json={}).status_code == 404
    b = _login(client, "admin@other.demo")
    b_recs = b.get("/api/procurement/recommendations", params={"status": "PENDING"}).json()
    assert {r["id"] for r in b_recs}.isdisjoint({r["id"] for r in a_recs})


def test_hospital_audit_logs_are_isolated(client, db, world, two):
    b = _login(client, "admin@other.demo")
    b.post("/api/categories", json={"name": f"{MARK} audited category"})
    a = as_role(lambda e: _login(client, e), "admin")
    r = a.get("/api/audit-logs", params={"page_size": 200})
    _no_leak(r)
    assert all(i["user"] is None or i["user"]["id"] != two["admin"].id for i in r.json()["items"])
    assert as_role(lambda e: _login(client, e), "viewer").get("/api/audit-logs").status_code == 403


# ---------------------------------------------------------------- organization administration


def _org_admin(db, world, email="orgadmin@test.demo") -> User:
    u = User(email=email, full_name="Org Admin", hashed_password=world["users"]["admin"].hashed_password,
             hospital_id=None, role=None)
    db.add(u)
    db.flush()
    db.add(OrganizationMembership(user_id=u.id, organization_id=world["org"].id))
    db.commit()
    return u


def _onboard(c, org_id, code="A2", admin_email="a2admin@test.demo", **extra):
    body = {"name": "Second Test Hospital", "code": code, "city": "Pune", "admin": {
        "email": admin_email, "full_name": "Second Admin", "password": "Passw0rd!"},
        "departments": [{"code": "ICU", "name": "Intensive Care"}, {"code": "OT", "name": "Theatre"}]} | extra
    return c.post(f"/api/organizations/{org_id}/hospitals", json=body)


def test_onboarding_flow_creates_a_ready_isolated_hospital(client, db, world, two):
    _org_admin(db, world)
    oa = _login(client, "orgadmin@test.demo")
    assert oa.get("/api/auth/me").json()["admin_organizations"][0]["code"] == "TESTORG"
    r = _onboard(oa, world["org"].id, procurement={"service_level": 0.9})
    assert r.status_code == 201, r.text
    out = r.json()
    hid = out["hospital"]["id"]
    assert out["admin_created"] and out["departments"] == 2 and out["procurement_settings"] == "custom"
    assert _onboard(oa, world["other_org"].id, code="EVIL").status_code == 404  # not my organization
    assert _onboard(oa, world["org"].id, code="A3", admin_email="admin@other.demo").status_code == 409  # other org's account
    # the new admin lands in the new hospital, which is empty (nothing copied from A or B)
    na = _login(client, "a2admin@test.demo")
    me = na.get("/api/auth/me").json()
    assert me["hospital"]["id"] == hid and me["role"] == "admin"
    assert na.get("/api/consumables").json() == []
    assert {d["code"] for d in na.get("/api/departments").json()} == {"ICU", "OT"}
    assert na.get("/api/procurement/settings").json()["values"]["service_level"] == 0.9
    assert na.get(f"/api/consumables/{world['gloves'].id}").status_code == 404
    db.expire_all()
    ev = db.scalar(select(AuditLog).where(AuditLog.action == "hospital.onboard"))
    assert ev.hospital_id == hid and ev.organization_id == world["org"].id


def test_organization_admin_scope(client, db, world, two):
    _org_admin(db, world)
    oa = _login(client, "orgadmin@test.demo")
    _onboard(oa, world["org"].id)
    hs = oa.get("/api/hospitals").json()
    assert {h["code"] for h in hs} == {"TEST", "A2"} and all(h["can_admin"] and not h["can_switch"] for h in hs)
    assert oa.get(f"/api/organizations/{world['other_org'].id}").status_code == 404
    assert oa.get(f"/api/hospitals/{world['other'].id}/members").status_code == 404
    # administers members of its hospitals without a membership — but gets no operational data
    members = oa.get(f"/api/hospitals/{world['hospital'].id}/members").json()
    assert {m["email"] for m in members} >= {"admin@test.demo", "viewer@test.demo"}
    assert oa.get("/api/inventory").status_code == 403
    assert oa.post(f"/api/hospitals/{world['hospital'].id}/switch").status_code == 404
    # add an A account to A2 (same organization) with a different role; its role in A is untouched
    r = oa.post(f"/api/hospitals/{hs[0]['id'] if hs[0]['code'] == 'A2' else hs[1]['id']}/members",
                json={"email": "viewer@test.demo", "full_name": "ignored", "password": "Passw0rd!", "role": "procurement_manager"})
    assert r.status_code == 201 and r.json()["other_hospitals"] == 1
    db.expire_all()
    roles = dict(db.execute(select(HospitalMembership.hospital_id, HospitalMembership.role).where(
        HospitalMembership.user_id == world["users"]["viewer"].id)).all())
    assert roles[world["hospital"].id] == "viewer" and "procurement_manager" in roles.values()
    # an account of another organization cannot be pulled in
    assert oa.post(f"/api/hospitals/{world['hospital'].id}/members", json={
        "email": "admin@other.demo", "full_name": "x y", "password": "Passw0rd!", "role": "viewer"}).status_code == 409
    # suspending a hospital of the organization
    a2 = next(h for h in hs if h["code"] == "A2")
    assert oa.patch(f"/api/hospitals/{a2['id']}", json={"status": "SUSPENDED"}).json()["status"] == "SUSPENDED"


def test_hospital_admin_manages_only_the_active_hospital(client, db, world, two):
    a = as_role(lambda e: _login(client, e), "admin")
    assert a.get(f"/api/hospitals/{world['hospital'].id}/members").status_code == 200
    for method, url in (("GET", f"/api/hospitals/{world['other'].id}"), ("GET", f"/api/hospitals/{world['other'].id}/members"),
                        ("PATCH", f"/api/hospitals/{world['other'].id}"),
                        ("POST", f"/api/hospitals/{world['other'].id}/members"),
                        ("GET", f"/api/organizations/{world['org'].id}/overview")):
        assert a.request(method, url, json={}).status_code in (404, 422), url
    assert a.patch(f"/api/hospitals/{world['hospital'].id}", json={"status": "SUSPENDED"}).status_code == 403
    assert as_role(lambda e: _login(client, e), "procurement_manager").get(
        f"/api/hospitals/{world['hospital'].id}/members").status_code == 404


def test_members_of_several_hospitals_keep_account_changes_with_the_holder(client, db, world, two):
    u = _two_hospital_user(db, world)
    b_admin = _login(client, "admin@other.demo")
    assert b_admin.patch(f"/api/users/{u.id}", json={"password": "N3wPassw0rd"}).status_code == 403
    assert b_admin.patch(f"/api/users/{u.id}", json={"role": "procurement_manager"}).status_code == 200
    db.expire_all()
    roles = dict(db.execute(select(HospitalMembership.hospital_id, HospitalMembership.role)
                            .where(HospitalMembership.user_id == u.id)).all())
    assert roles == {world["hospital"].id: "admin", world["other"].id: "procurement_manager"}
    users_b = b_admin.get("/api/users").json()
    assert "multi@test.demo" in {x["email"] for x in users_b} and "admin@test.demo" not in {x["email"] for x in users_b}


def test_organization_overview_is_an_explicit_per_hospital_aggregate(client, db, world, two):
    _org_admin(db, world)
    oa = _login(client, "orgadmin@test.demo")
    _onboard(oa, world["org"].id)
    ov = oa.get(f"/api/organizations/{world['org'].id}/overview")
    assert ov.status_code == 200, ov.text
    _no_leak(ov)
    d = ov.json()
    assert d["contributing_hospitals"] == sorted(d["contributing_hospitals"]) and len(d["hospitals"]) == 2
    for k in ("items", "risk_high", "active_alerts", "pending_recommendations"):
        assert d["totals"][k] == sum(h[k] for h in d["hospitals"])
    a_row = next(h for h in d["hospitals"] if h["code"] == "TEST")
    assert a_row["items"] == db.scalar(select(func.count(Consumable.id)).where(Consumable.hospital_id == world["hospital"].id,
                                                                               Consumable.is_active.is_(True)))
    assert any("never pooled" in n for n in d["notes"])
    oaudit = oa.get(f"/api/organizations/{world['org'].id}/audit-logs", params={"page_size": 200})
    _no_leak(oaudit)
    assert oaudit.json()["total"] > 0


# ---------------------------------------------------------------- CSV import


def test_csv_import_validates_everything_writes_only_into_the_active_hospital_and_never_overwrites(client, db, world, two):
    a = as_role(lambda e: _login(client, e), "admin")
    bad = "sku,name,unit,category,unit_cost\nGLV-7,dup of existing,pair,Gloves,1\nNEW-1,New item,box,New Cat,abc\nNEW-1,again,box,,1\n"
    r = a.post("/api/imports/items", json={"csv": bad, "dry_run": False})
    assert r.status_code == 200 and r.json()["created"] == 0
    errs = {e["line"]: e["error"] for e in r.json()["errors"]}
    assert "already exists" in errs[2] and "number" in errs[3] and "duplicate" in errs[4]
    good = "sku,name,unit,category,unit_cost,reorder_level\nNEW-1,New item,box,New Cat,12.5,10\nNEW-2,Other,pair,Gloves,3,\n"
    dry = a.post("/api/imports/items", json={"csv": good, "dry_run": True}).json()
    assert dry["valid"] == 2 and dry["created"] == 0 and dry["created_categories"] == ["New Cat"]
    assert db.scalar(select(func.count(Consumable.id)).where(Consumable.sku == "NEW-1")) == 0
    done = a.post("/api/imports/items", json={"csv": good, "dry_run": False}).json()
    assert done["created"] == 2
    # B has a supplier coded SUP (A's was renamed RELY): it must not be resolvable from A
    leak = "sku,lot_number,quantity,expiry_date,supplier_code\nNEW-1,L1,40,2030-01-01,SUP\n"
    r = a.post("/api/imports/opening_stock", json={"csv": leak, "dry_run": False}).json()
    assert r["created"] == 0 and "not found in this hospital" in r["errors"][0]["error"]
    stock_csv = "sku,lot_number,quantity,expiry_date,supplier_code\nNEW-1,L1,40,2030-01-01,RELY\nNEW-2,L2,5,,\n"
    r = a.post("/api/imports/opening_stock", json={"csv": stock_csv, "dry_run": False}).json()
    assert r["created"] == 2, r
    db.expire_all()
    new = db.scalars(select(Consumable).where(Consumable.sku.in_(["NEW-1", "NEW-2"]))).all()
    assert {c.hospital_id for c in new} == {world["hospital"].id}
    assert a.post("/api/imports/items", json={"csv": "sku,name\nX,Y\n"}).status_code == 422  # missing column
    assert a.post("/api/imports/nope", json={"csv": "a\n1"}).status_code == 404
    v = as_role(lambda e: _login(client, e), "viewer")
    assert v.post("/api/imports/items", json={"csv": good}).status_code == 403


# ---------------------------------------------------------------- roles


def test_hospital_role_permissions_are_unchanged_by_v8(client, db, world):
    for role in Role:
        me = as_role(lambda e: _login(client, e), role.value).get("/api/auth/me").json()
        assert set(me["permissions"]) == ROLE_PERMISSIONS[role], role
        assert me["is_platform_admin"] is False and me["admin_organizations"] == []


def test_membership_rows_exist_for_every_hospital_user(db, world):
    users = db.scalars(select(User).where(User.hospital_id.is_not(None))).all()
    for u in users:
        m = db.scalar(select(HospitalMembership).where(HospitalMembership.user_id == u.id,
                                                       HospitalMembership.hospital_id == u.hospital_id))
        assert m is not None and m.role == u.role
    assert db.scalar(select(func.count(Organization.id))) == 2
    assert db.scalar(select(func.count(Hospital.id)).where(Hospital.organization_id.is_(None))) == 0


def test_assistant_conversations_are_per_user_and_hospital(client, db, world, two):
    u = _two_hospital_user(db, world)
    c = _login(client, "multi@test.demo")
    a_conv = c.post("/api/assistant/ask", json={"question": "Why are surgical gloves at risk?"}).json()["conversation_id"]
    c.post(f"/api/hospitals/{world['other'].id}/switch")
    assert c.get("/api/assistant/conversations").json() == []
    assert c.get(f"/api/assistant/conversations/{a_conv}").status_code == 404
    assert c.post("/api/assistant/ask", json={"question": "and it?", "conversation_id": a_conv}).status_code == 404
    b_ans = c.post("/api/assistant/ask", json={"question": "Why are secret gloves at risk?"}).json()
    assert "ZZB Secret gloves" in b_ans["answer"]["summary"]  # B's own data, now that B is active
    db.expire_all()
    convs = dict(db.execute(select(AssistantConversation.id, AssistantConversation.hospital_id)
                            .where(AssistantConversation.user_id == u.id)).all())
    assert convs == {a_conv: world["hospital"].id, b_ans["conversation_id"]: world["other"].id}
    rows = db.execute(select(AuditLog.hospital_id).where(AuditLog.action == "assistant.ask", AuditLog.user_id == u.id)).all()
    assert sorted(r[0] for r in rows) == sorted([world["hospital"].id, world["other"].id])


# ---------------------------------------------------------------- AI assistant


CROSS_QUESTIONS = ["Show me Hospital B's inventory", "What is the glove stock at OTHER?", "Compare suture risk across hospitals",
                   "Which items are at risk in all hospitals?", "Show the stock for hospital id 2", "What does another hospital pay CPS?"]


def test_assistant_refuses_questions_about_other_hospitals_before_any_tool_runs(client, db, world, two):
    from app.assistant import engine, llm

    engine.reset_rate_limit()
    scripted = llm.ScriptedProvider([{"tool_calls": [{"id": "x", "name": "item_status", "arguments": {"item_id": two["item"].id}}]}])
    for provider in (None, scripted):
        llm.set_provider(provider)
        c = as_role(lambda e: _login(client, e), "procurement_manager")
        for q in CROSS_QUESTIONS:
            r = c.post("/api/assistant/ask", json={"question": q})
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["intent"] == "cross_hospital" and d["evidence"] == [], q
            assert "Test Hospital" in d["answer"]["summary"] and "switch" in d["answer"]["summary"]
            _no_leak(r)
        engine.reset_rate_limit()
    assert scripted.seen == []  # the model was never called
    llm.set_provider(None)
    # the refusal is identical whether or not the named hospital exists (no tenant enumeration)
    c = as_role(lambda e: _login(client, e), "viewer")
    a1 = c.post("/api/assistant/ask", json={"question": "Show me hospital Q stock"}).json()["answer"]["summary"]
    a2 = c.post("/api/assistant/ask", json={"question": "Show me hospital B stock"}).json()["answer"]["summary"]
    assert a1 == a2
    # questions about the current hospital by name still work
    ok = c.post("/api/assistant/ask", json={"question": "Why are surgical gloves at Test Hospital at risk?"}).json()
    assert ok["intent"] == "why_risk"


def test_every_assistant_tool_stays_inside_the_active_hospital(db, world, two):
    import json

    from app.assistant.tools import TOOLS, Ctx

    ctx = Ctx(db, world["users"]["admin"])
    b = two
    for name, spec in TOOLS.items():
        args = {}
        for p in spec.params:
            args[p] = {"item_id": b["item"].id, "supplier_id": b["supplier"].id, "query": "Secret gloves",
                       "name": "single_source_items", "term": "OTIF", "days": 14, "delay_days": 2, "level": "HIGH",
                       "within_days": 30, "limit": 50, "alert_type": None, "demand_change_pct": 10}.get(p)
        args = {k: v for k, v in args.items() if v is not None}
        r = spec.fn(ctx, **args)
        blob = json.dumps({"data": r.data, "facts": r.facts, "error": r.error}, default=str)
        assert MARK not in blob, (name, blob[:300])
        if "item_id" in args or "supplier_id" in args:
            assert not r.ok, name  # another hospital's id is 'not found'
    # entity resolution only searches the active hospital
    from app.assistant.tools import resolve

    assert resolve(ctx, f"{MARK} Secret gloves", "item") == [] or all(
        c["id"] != b["item"].id for c in resolve(ctx, f"{MARK} Secret gloves", "item"))
    assert all(c["id"] != b["supplier"].id for c in resolve(ctx, "RELY", "supplier"))


def test_scripted_llm_cannot_reach_another_hospital_through_tool_arguments(client, db, world, two):
    from app.assistant import engine, llm

    engine.reset_rate_limit()
    llm.set_provider(llm.ScriptedProvider([
        {"tool_calls": [{"id": "1", "name": "item_status", "arguments": {"item_id": two["item"].id}}]},
        {"tool_calls": [{"id": "2", "name": "supplier_impact", "arguments": {"supplier_id": two["supplier"].id}}]},
        {"tool_calls": [{"id": "3", "name": "item_status", "arguments": {"item_id": two["item"].id, "hospital_id": world["other"].id}}]},
        {"content": '{"summary": "ZZB gloves: 90%", "points": [], "follow_ups": []}'},
    ]))
    try:
        r = as_role(lambda e: _login(client, e), "admin").post("/api/assistant/ask", json={"question": "Why are gloves at risk?"})
        d = r.json()
        _no_leak(r)
        assert d["mode"] == "deterministic" and d["fallback_reason"]  # the model's answer was not used
        assert [e["ok"] for e in d["evidence"][:2]] == [False, False]
    finally:
        llm.set_provider(None)


# ---------------------------------------------------------------- knowledge graph


def test_graph_sync_queries_and_impact_are_per_hospital(client, db, world, two, graph):  # noqa: F811
    from app.graph import sync

    ra = sync.sync(db, world["hospital"].id)
    a_nodes, a_edges = sync.graph_counts(graph, world["hospital"].id)
    rb = sync.sync(db, world["other"].id)
    assert ra.verified and rb.verified and ra.hospital_id != rb.hospital_id
    assert sync.graph_counts(graph, world["hospital"].id) == (a_nodes, a_edges)  # B's sync left A untouched
    keys = graph.run("MATCH (n) RETURN n.key AS k, n.hospital_id AS h")
    assert keys and all(r["k"].startswith(f"{r['h']}:") for r in keys)
    # changing B and re-syncing B does not rebuild A
    two["item"].name = f"{MARK} Secret gloves v2"
    db.commit()
    sync.sync(db, world["other"].id)
    assert sync.graph_counts(graph, world["hospital"].id) == (a_nodes, a_edges)
    c = as_role(lambda e: _login(client, e), "procurement_manager")
    for name in ("single_source_items", "risky_single_source", "procedures_at_risk", "departments_exposed"):
        r = c.post(f"/api/graph/queries/{name}", json={})
        assert r.status_code == 200
        _no_leak(r)
    assert c.post("/api/graph/queries/item_neighbourhood", json={"item_id": two["item"].id}).status_code in (404, 422)
    assert c.get(f"/api/graph/items/{two['item'].id}/explain").status_code == 404
    assert c.get(f"/api/graph/impact/suppliers/{two['supplier'].id}").status_code == 404
    impact = c.get(f"/api/graph/impact/suppliers/{world['supplier'].id}")
    assert impact.status_code == 200
    _no_leak(impact)
    st = c.get("/api/graph/status").json()
    _no_leak(c.get("/api/graph/status"))
    assert st["current"] is not None


from tests.test_graph import graph  # noqa: E402,F401  (fixture)

# ---------------------------------------------------------------- ML: two demo hospitals with different demand


def test_forecasts_risk_supplier_metrics_and_procurement_are_trained_per_hospital(client, db):
    """Sunrise (surgical, high glove use) and Lakeview (obstetric, low glove use, poor CPS deliveries) in ONE database:
    each hospital's models see only its own data and produce its own answers."""
    from app.ml.pipeline import run_training
    from app.models import Forecast, ModelItemMetric
    from app.risk.pipeline import run_training as run_risk
    from app.seed import LAKEVIEW, SUNRISE, seed
    from app.supplier_intel import service as sis

    a = seed(db, days=55, verbose=False, profile=SUNRISE)
    b = seed(db, days=55, verbose=False, profile=LAKEVIEW)
    for h in (a, b):
        run_training(db, h.id)
        run_risk(db, h.id)
        alerts.evaluate(db, h.id)
        procurement_service.generate(db, h.id, None)
        db.commit()
    for h in (a, b):
        mvs = db.scalars(select(ModelVersion).where(ModelVersion.hospital_id == h.id)).all()
        assert mvs
        own = set(db.scalars(select(Consumable.id).where(Consumable.hospital_id == h.id)))
        from app.ml.data import load_panel

        panel = load_panel(db, h.id)  # the training dataset (series = item × department)
        own_depts = set(db.scalars(select(Department.id).where(Department.hospital_id == h.id)))
        assert set(panel.series["consumable_id"]) <= own and set(panel.series["department_id"]) <= own_depts
        assert set(panel.items) <= own
        for mv in mvs:
            used = set(db.scalars(select(ModelItemMetric.consumable_id).where(ModelItemMetric.model_version_id == mv.id)))
            fc = set(db.scalars(select(Forecast.consumable_id).where(Forecast.model_version_id == mv.id)))
            assert used <= own and fc <= own  # no other hospital's item in its training data or forecasts
    # deliberately different demand → different forecasts for the same SKU
    def f14(email: str) -> float:
        c = _login(client, email)
        item = next(i for i in c.get("/api/consumables", params={"search": "GLV-EXM-M"}).json() if i["sku"] == "GLV-EXM-M")
        return c.get(f"/api/forecasts/{item['id']}", params={"days": 14}).json()["predicted_demand"]
    fa, fb = f14("procurement@sunrise.demo"), f14("procurement@lakeview.demo")
    assert fa > 1.5 * fb, (fa, fb)
    # supplier performance is per hospital: CPS delivers well to Sunrise, badly to Lakeview
    def cps(h):
        sc = sis.scorecards(db, h.id)
        return next(s for s in sc["suppliers"] if s["code"] == "CPS")["metrics"]["otif_rate"]
    assert cps(a) > cps(b) + 0.1
    # the same assistant question answers from each hospital's own data
    from app.assistant import engine
    engine.reset_rate_limit()
    answers = {}
    for email in ("procurement@sunrise.demo", "procurement@lakeview.demo"):
        c = _login(client, email)
        ans = c.post("/api/assistant/ask", json={"question": "Why is Suture 2-0 at risk?"}).json()
        item = next(i for i in c.get("/api/consumables", params={"search": "SUT-VIC-20"}).json())
        risk = c.get(f"/api/stockout-risks/{item['id']}").json()
        assert f"{risk['risk_level']} stockout risk ({round(risk['probability'] * 100)}%" in ans["answer"]["summary"]
        answers[email] = (ans["answer"]["summary"], ans["evidence"][0]["args"]["item_id"])
    assert answers["procurement@sunrise.demo"] != answers["procurement@lakeview.demo"]
    # and Lakeview's name is a cross-hospital reference for a Sunrise user
    c = _login(client, "procurement@sunrise.demo")
    assert c.post("/api/assistant/ask", json={"question": "What is Lakeview's suture stock?"}).json()["intent"] == "cross_hospital"
    # recommendations: each hospital sees only its own
    ra = {r["id"] for r in _login(client, "procurement@sunrise.demo").get("/api/procurement/recommendations").json()}
    rb = {r["id"] for r in _login(client, "procurement@lakeview.demo").get("/api/procurement/recommendations").json()}
    assert ra and rb and ra.isdisjoint(rb)
