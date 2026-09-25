"""V9 — integrations & data exchange: framework, CSV/Excel upload, API push (keys, rate limit), REST pull (retries,
pagination, checkpoints) against the local reference ERP simulator, mapping, validation, idempotency, retry,
reconciliation, monitoring, audit trail, hospital isolation, and data reaching the existing V1–V5 services."""

import io
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import business_today
from app.db.base import utcnow
from app.integrations import connectors, credentials, engine, reference_erp
from app.integrations import sources as isrc
from app.main import app
from app.models import (
    AuditLog,
    Consumable,
    ExternalRef,
    IntegrationCredential,
    IntegrationSource,
    MovementType,
    ReconciliationIssue,
    StockBatch,
    StockMovement,
    Supplier,
    SupplierDelivery,
    SupplierOrder,
    SyncCheckpoint,
    SyncRecord,
    SyncRun,
    User,
)
from app.services import stock
from tests.conftest import as_role


@pytest.fixture(autouse=True)
def _reset():
    credentials.reset_rate_limit()
    reference_erp.reset_cache()
    yield
    credentials.reset_rate_limit()
    reference_erp.reset_cache()


def _stock(db, world, qty=300, sku_item=None):
    item = sku_item or world["gloves"]
    stock.receive(db, world["users"]["inventory_manager"], item, qty, "LOT-A", business_today() + timedelta(days=400),
                  world["supplier"], 40, "INIT", None, at=utcnow() - timedelta(hours=2))
    db.commit()


def _source(db, world, connector="api_push", entities=None, config=None, name="Hospital ERP"):
    src = isrc.create_source(db, world["hospital"].id, world["users"]["admin"], name, "erp", connector,
                             entities or list(engine.ENTITIES), config or {})
    db.commit()
    return src


def _key(db, src) -> str:
    _cred, key = credentials.create(db, src, "test", None)
    db.commit()
    return key


def _push(client, key, entity, records, dry_run=False):
    return client.post(f"/api/ingest/v1/{entity}", json={"records": records, "dry_run": dry_run},
                       headers={"Authorization": f"Bearer {key}"})


def _codes(body) -> list[str]:
    return [e["code"] for r in body["rejected_records"] for e in r["errors"]]


def _now_iso():
    return utcnow().isoformat()


# ---------------------------------------------------------------- framework, permissions, configuration


def test_permissions_and_entity_catalogue(login, world):
    v = as_role(login, "viewer")
    assert v.get("/api/integrations/overview").status_code == 403
    inv = as_role(login, "inventory_manager")
    assert inv.get("/api/integrations/overview").status_code == 200
    ents = inv.get("/api/integrations/entities").json()
    assert [e["name"] for e in ents] == [
        "departments", "suppliers", "items", "supplier_items", "purchase_orders", "deliveries", "consumption", "inventory"]
    body = {"name": "ERP", "connector": "api_push", "entities": ["items"]}
    assert inv.post("/api/integrations/sources", json=body).status_code == 403  # manage = admin only
    a = as_role(login, "admin")
    r = a.post("/api/integrations/sources", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["enabled"] is True and r.json()["last_success_at"] is None


def test_source_config_is_validated_and_never_holds_secrets(login, world, monkeypatch):
    a = as_role(login, "admin")

    def create(cfg, name="XX"):
        return a.post("/api/integrations/sources", json={"name": name, "connector": "rest_pull", "entities": ["items"],
                                                         "config": cfg})

    assert "allowlist" in create({"base_url": "http://169.254.169.254/latest"}).json()["detail"]  # SSRF guard
    assert "auth_env" in create({"base_url": "reference-erp", "auth_env": "JWT_SECRET"}).json()["detail"]
    assert "Secrets" in create({"base_url": "reference-erp", "api_key": "abc"}).json()["detail"]
    assert create({"base_url": "reference-erp", "frobnicate": 1}).status_code == 422
    monkeypatch.setattr(settings, "INTEGRATION_ALLOWED_HOSTS", ["erp.hospital.test"])
    ok = create({"base_url": "https://erp.hospital.test/api", "auth_env": "MEDFLOW_INTEGRATION_ERP_TOKEN",
                 "schedule_minutes": 30}, "Real ERP")
    assert ok.status_code == 201, ok.text
    assert ok.json()["is_simulated"] is False and ok.json()["config"]["page_size"] == 200
    sim = create({"base_url": "reference-erp", "auth_env": "REFERENCE_ERP_TOKEN"}, "Sim")
    assert sim.json()["is_simulated"] is True
    assert create({"base_url": "reference-erp"}, "sIM").status_code == 422  # duplicate name (case-insensitive)


# ---------------------------------------------------------------- upload (CSV / Excel), mapping, idempotency


def _upload(c, src_id, entity, name, content, dry_run=False):
    return c.post(f"/api/integrations/sources/{src_id}/upload", data={"entity": entity, "dry_run": str(dry_run).lower()},
                  files={"file": (name, content, "text/csv")})


def test_csv_items_with_field_mapping_are_idempotent(login, db, world):
    src = _source(db, world, "upload", ["items", "consumption"], name="Stores spreadsheet")
    a = as_role(login, "admin")
    m = a.put(f"/api/integrations/sources/{src.id}/mappings/items", json={"field_map": {
        "code": "material_code", "name": "material_description", "unit": "uom", "unit_cost": "std_price"},
        "defaults": {"category": "Gloves"}})
    assert m.status_code == 200 and m.json()["customized"] is True
    csv1 = b"material_code;material_description;uom;std_price\nglv-8;Surgical gloves 8;pair;42.50\nGLV-7;Surgical gloves 7;pair;40\n"
    r = _upload(a, src.id, "items", "materials.csv", csv1)
    assert r.status_code == 201, r.text
    run = r.json()
    assert (run["status"], run["records_created"], run["records_unchanged"]) == ("SUCCESS", 1, 1)  # GLV-7 already matches
    db.expire_all()
    g8 = db.scalar(select(Consumable).where(Consumable.hospital_id == world["hospital"].id, Consumable.sku == "GLV-8"))
    assert g8.name == "Surgical gloves 8" and float(g8.unit_cost) == 42.5 and g8.category.name == "Gloves"
    again = _upload(a, src.id, "items", "materials.csv", csv1).json()
    assert (again["records_created"], again["records_updated"], again["records_unchanged"]) == (0, 0, 2)
    changed = _upload(a, src.id, "items", "m2.csv", csv1.replace(b"42.50", b"44.00")).json()
    assert changed["records_updated"] == 1
    db.expire_all()
    assert float(db.get(Consumable, g8.id).unit_cost) == 44.0
    # unknown mapping field → 422; reset mapping → defaults back to MedFlow names
    assert a.put(f"/api/integrations/sources/{src.id}/mappings/items", json={"field_map": {"nope": "x"}}).status_code == 422
    assert a.delete(f"/api/integrations/sources/{src.id}/mappings/items").json()["customized"] is False


def test_consumption_validation_rejections_and_retry(login, db, world):
    _stock(db, world)
    src = _source(db, world, "upload", ["items", "consumption"])
    a = as_role(login, "admin")
    today = business_today().isoformat()
    csv = (
        "external_id,item_code,department_code,quantity,occurred_at\n"
        f"T1,GLV-7,ORTHO,10,{today}\n"            # ok
        f"T2,GLV-9,ORTHO,5,{today}\n"             # unknown item (fixed by retry later)
        f"T3,GLV-7,ORTHO,-4,{today}\n"            # negative quantity
        f"T4,GLV-7,ORTHO,4,31/02/2026\n"          # invalid date
        f"T5,GLV-7,NOPE,4,{today}\n"              # unknown department
        f"T6,,ORTHO,4,{today}\n"                  # missing field
        f"T1,GLV-7,ORTHO,10,{today}\n"            # duplicate in batch
        f"T7,GLV-7,ICU,99999,{today}\n"           # more than the stock
    ).encode()
    r = _upload(a, src.id, "consumption", "consumption.csv", csv)
    run = r.json()
    assert run["status"] == "PARTIAL" and run["records_received"] == 8 and run["records_created"] == 1
    assert run["records_rejected"] == 7
    reasons = run["error_summary"]["reasons"]
    for code in ("unknown_item", "negative_quantity", "invalid_date", "unknown_department", "missing_field",
                 "duplicate_record", "insufficient_stock"):
        assert reasons.get(code) == 1, (code, reasons)
    rows = {x["row_number"]: x for x in run["rejected"]}
    assert rows[3]["external_id"] == "T2" and rows[3]["raw"]["item_code"] == "GLV-9"  # spreadsheet line numbers
    db.expire_all()
    svc = db.get(User, db.get(IntegrationSource, src.id).service_user_id)
    mv = db.scalar(select(StockMovement).where(StockMovement.reference == "T1"))
    assert mv.movement_type == MovementType.ISSUE and mv.quantity == -10 and mv.performed_by_id == svc.id
    assert svc.is_active is False and svc.hospital_id is None and svc.full_name.startswith("Integration:")
    # fix the cause (add item GLV-9 with stock) and retry only the rejected records
    items = _upload(a, src.id, "items", "i.csv", b"code,name,unit\nGLV-9,Gloves 9,pair\n").json()
    assert items["records_created"] == 1
    _stock(db, world, 50, db.scalar(select(Consumable).where(Consumable.sku == "GLV-9")))
    retry = a.post(f"/api/integrations/runs/{run['id']}/retry").json()
    assert retry["mode"] == "retry" and retry["parent_run_id"] == run["id"]
    assert retry["records_received"] == 7 and retry["records_created"] == 1  # T2 now applies
    # the in-batch duplicate of T1 is now simply "already applied" (idempotent); the other five still fail
    assert (retry["records_unchanged"], retry["records_rejected"]) == (1, 5)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(SyncRecord).where(SyncRecord.run_id == run["id"],
                                                                          SyncRecord.status == "RETRIED")) == 7
    assert a.post(f"/api/integrations/runs/{run['id']}/retry").status_code == 409  # nothing left to retry there


def test_excel_upload_and_dry_run_writes_nothing(login, db, world):
    from openpyxl import Workbook

    _stock(db, world)
    src = _source(db, world, "upload", ["departments", "consumption"])
    a = as_role(login, "admin")
    wb = Workbook()
    ws = wb.active
    ws.append(["code", "name", "active"])
    ws.append(["ENT", "ENT clinic", "yes"])
    ws.append(["ICU", "Intensive care", "true"])
    buf = io.BytesIO()
    wb.save(buf)
    r = _upload(a, src.id, "departments", "departments.xlsx", buf.getvalue())
    assert r.status_code == 201, r.text
    assert (r.json()["records_created"], r.json()["records_updated"]) == (1, 1)  # ICU renamed
    before = db.scalar(select(func.count(StockMovement.id)))
    csv = f"external_id,item_code,department_code,quantity,occurred_at\nD1,GLV-7,ENT,5,{_now_iso()}\n".encode()
    dry = _upload(a, src.id, "consumption", "c.csv", csv, dry_run=True).json()
    assert dry["mode"] == "dry_run" and dry["records_created"] == 1 and dry["status"] == "SUCCESS"
    db.expire_all()
    assert db.scalar(select(func.count(StockMovement.id))) == before
    assert db.scalar(select(func.count(ExternalRef.id)).where(ExternalRef.entity == "consumption")) == 0
    assert db.get(IntegrationSource, src.id).last_run_at is not None  # the departments run, not the dry run
    bad = _upload(a, src.id, "consumption", "c.xls", b"garbage")
    assert bad.status_code == 422 and ".xlsx" in bad.json()["detail"]


# ---------------------------------------------------------------- API push: keys, rate limit, idempotency


def test_api_keys_are_hashed_shown_once_and_revocable(login, db, world):
    src = _source(db, world, "api_push", ["items"])
    a = as_role(login, "admin")
    r = a.post(f"/api/integrations/sources/{src.id}/credentials", json={"label": "SAP PI"})
    assert r.status_code == 201
    key = r.json()["api_key"]
    assert key.startswith("mfk_")
    db.expire_all()
    cred = db.scalar(select(IntegrationCredential).where(IntegrationCredential.source_id == src.id))
    assert key not in (cred.key_hash, cred.key_prefix) and len(cred.key_hash) == 64
    assert "api_key" not in a.get(f"/api/integrations/sources/{src.id}/credentials").json()[0]
    c = TestClient(app)
    ok = _push(c, key, "items", [{"code": "PUSH-1", "name": "Pushed item", "unit": "piece"}])
    assert ok.status_code == 200 and ok.json()["created"] == 1
    assert _push(c, key[:-2] + "xx", "items", []).status_code == 401
    assert _push(c, "not-a-key", "items", []).status_code == 401
    assert c.post("/api/ingest/v1/items", json={"records": []}).status_code == 401
    assert _push(c, key, "consumption", [{}]).status_code == 403  # entity not enabled for this source
    assert _push(c, key, "unicorns", [{}]).status_code == 404
    a.post(f"/api/integrations/credentials/{cred.id}/revoke")
    assert _push(c, key, "items", [{"code": "PUSH-2", "name": "x", "unit": "piece"}]).status_code == 401
    key2 = _key(db, src)
    a.patch(f"/api/integrations/sources/{src.id}", json={"enabled": False})
    assert _push(c, key2, "items", [{"code": "PUSH-2", "name": "x", "unit": "piece"}]).status_code == 403


def test_ingest_rate_limit_and_request_size(client, db, world, monkeypatch):
    src = _source(db, world, "api_push", ["items"])
    key = _key(db, src)
    c = TestClient(app)
    monkeypatch.setattr(settings, "INTEGRATION_RATE_LIMIT_PER_MINUTE", 2)
    monkeypatch.setattr(settings, "INTEGRATION_MAX_RECORDS_PER_REQUEST", 3)
    rec = {"code": "RL-1", "name": "r", "unit": "piece"}
    assert _push(c, key, "items", [rec]).status_code == 200
    assert _push(c, key, "items", [rec] * 4).status_code == 413
    r = _push(c, key, "items", [rec])
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_transactions_are_idempotent_and_the_ledger_stays_append_only(client, db, world):
    _stock(db, world)
    src = _source(db, world, "api_push", ["consumption"])
    key = _key(db, src)
    c = TestClient(app)
    t = {"external_id": "TX-1", "item_code": "GLV-7", "department_code": "ICU", "quantity": 7, "occurred_at": _now_iso()}
    assert _push(c, key, "consumption", [t]).json()["created"] == 1
    again = _push(c, key, "consumption", [t]).json()
    assert (again["created"], again["unchanged"], again["rejected"]) == (0, 1, 0)  # re-sent: nothing issued twice
    changed = _push(c, key, "consumption", [{**t, "quantity": 9}]).json()
    assert _codes(changed) == ["changed_transaction"]
    old = {**t, "external_id": "TX-OLD", "occurred_at": (utcnow() - timedelta(days=3)).isoformat()}
    assert _codes(_push(c, key, "consumption", [old]).json()) == ["out_of_order"]
    future = {**t, "external_id": "TX-F", "occurred_at": (utcnow() + timedelta(days=1)).isoformat()}
    assert _codes(_push(c, key, "consumption", [future]).json()) == ["invalid_date"]
    db.expire_all()
    assert db.scalar(select(func.sum(StockMovement.quantity)).where(StockMovement.hospital_id == world["hospital"].id,
                                                                    StockMovement.movement_type == MovementType.ISSUE)) == -7


def test_purchase_orders_and_deliveries_flow_into_the_v4_order_log(client, db, world):
    src = _source(db, world, "api_push", ["purchase_orders", "deliveries"])
    key = _key(db, src)
    c = TestClient(app)
    today = business_today()
    po = {"po_number": "PO-9001", "supplier_code": "SUP", "item_code": "GLV-7", "quantity": 100, "unit_price": "38.00",
          "order_date": (today - timedelta(days=2)).isoformat(), "expected_date": (today + timedelta(days=1)).isoformat()}
    assert _push(c, key, "purchase_orders", [po, {**po, "po_number": "PO-9002", "supplier_code": "NOPE"}]).json()[
        "rejected"] == 1
    upd = _push(c, key, "purchase_orders", [{**po, "expected_date": (today + timedelta(days=3)).isoformat()}]).json()
    assert upd["updated"] == 1
    d = {"external_id": "GRN-1", "po_number": "PO-9001", "quantity": 60, "received_at": _now_iso(), "lot_number": "L-77",
         "expiry_date": (today + timedelta(days=500)).isoformat()}
    res = _push(c, key, "deliveries", [d, {**d, "external_id": "GRN-X", "po_number": "PO-0000"},
                                       {**d, "external_id": "GRN-E", "expiry_date": (today - timedelta(days=1)).isoformat()}]).json()
    assert res["created"] == 1 and sorted(_codes(res)) == ["invalid_date", "unknown_purchase_order"]
    db.expire_all()
    order = db.scalar(select(SupplierOrder).where(SupplierOrder.reference == "PO-9001"))
    assert (order.status, order.quantity_received, order.expected_date) == ("PARTIAL", 60, today + timedelta(days=3))
    dl = db.scalar(select(SupplierDelivery).where(SupplierDelivery.order_id == order.id))
    batch = db.get(StockBatch, dl.batch_id)
    assert batch.lot_number == "L-77" and batch.quantity == 60 and batch.supplier_id == world["supplier"].id
    assert db.get(StockMovement, dl.stock_movement_id).movement_type == MovementType.RECEIPT
    closed = _push(c, key, "purchase_orders", [{**po, "expected_date": (today + timedelta(days=3)).isoformat(),
                                                "status": "closed"}]).json()
    assert closed["updated"] == 1
    db.expire_all()
    assert db.get(SupplierOrder, order.id).status == "RECEIVED"  # closed short (V4 semantics)
    # a PO number that MedFlow already uses for an order this source did not create is not taken over
    stock_order = SupplierOrder(hospital_id=world["hospital"].id, supplier_id=world["supplier"].id,
                                consumable_id=world["gloves"].id, reference="SO-LOCAL", ordered_date=today,
                                quoted_lead_time_days=3, expected_date=today, quantity_ordered=5, unit_price=1)
    db.add(stock_order)
    db.commit()
    assert _codes(_push(c, key, "purchase_orders", [{**po, "po_number": "SO-LOCAL"}]).json()) == ["conflict"]


# ---------------------------------------------------------------- reconciliation


def test_inventory_snapshots_create_reconciliation_issues_resolved_by_people(login, db, world):
    _stock(db, world, 300)
    new_item = Consumable(hospital_id=world["hospital"].id, sku="NEW-1", name="New item", unit="piece")
    db.add(new_item)
    db.commit()
    src = _source(db, world, "api_push", ["inventory"], config={"reconciliation_tolerance": 2})
    key = _key(db, src)
    c = TestClient(app)
    snap = [{"item_code": "GLV-7", "quantity": 330, "as_of": _now_iso()}, {"item_code": "NEW-1", "quantity": 40},
            {"item_code": "GLV-7X", "quantity": 1}]
    r = _push(c, key, "inventory", snap).json()
    assert (r["created"], r["rejected"]) == (2, 1) and r["info"] == {"reconciliation_opened": 1, "opening_balances": 1}
    db.expire_all()
    issue = db.scalar(select(ReconciliationIssue).where(ReconciliationIssue.consumable_id == world["gloves"].id))
    assert (issue.external_quantity, issue.medflow_quantity, issue.difference, issue.status) == (330, 300, 30, "OPEN")
    assert stock.stock_levels(db, world["hospital"].id, [new_item.id])[new_item.id].usable == 40  # opening balance
    within = _push(c, key, "inventory", [{"item_code": "GLV-7", "quantity": 301}]).json()  # within tolerance → closes
    assert within["updated"] == 1
    db.expire_all()
    assert (db.get(ReconciliationIssue, issue.id).status, db.get(ReconciliationIssue, issue.id).resolution) == (
        "RESOLVED", "matched_later")
    _push(c, key, "inventory", [{"item_code": "GLV-7", "quantity": 280, "as_of": _now_iso()}])
    db.expire_all()
    issue2 = db.scalar(select(ReconciliationIssue).where(ReconciliationIssue.status == "OPEN"))
    assert issue2.difference == -20
    p = as_role(login, "procurement_manager")
    listed = p.get("/api/integrations/reconciliation").json()
    assert [x["difference"] for x in listed] == [-20] and listed[0]["medflow_now"] == 300
    body = {"action": "adjust", "note": "Recount confirmed 280"}
    assert p.post(f"/api/integrations/reconciliation/{issue2.id}/resolve", json=body).status_code == 403
    inv = as_role(login, "inventory_manager")
    res = inv.post(f"/api/integrations/reconciliation/{issue2.id}/resolve", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["resolution"] == "adjusted" and res.json()["medflow_now"] == 280
    db.expire_all()
    adj = db.scalar(select(StockMovement).where(StockMovement.movement_type == MovementType.ADJUSTMENT))
    assert adj.quantity == -20 and "Reconciliation" in adj.reason
    assert inv.post(f"/api/integrations/reconciliation/{issue2.id}/resolve", json=body).status_code == 409


# ---------------------------------------------------------------- REST pull + the reference ERP simulator


@pytest.fixture()
def erp(db, world, monkeypatch):
    """Enable the simulator; MedFlow's HTTP client is the FastAPI TestClient (a real httpx.Client over ASGI)."""
    world["hospital"].is_demo = True
    db.commit()
    monkeypatch.setattr(settings, "REFERENCE_ERP_ENABLED", True)
    monkeypatch.setattr(settings, "REFERENCE_ERP_TOKEN", "test-erp-token")
    monkeypatch.setattr(settings, "REFERENCE_ERP_BASE_URL", "http://testserver")
    monkeypatch.setattr(connectors, "client_factory", lambda: TestClient(app))
    monkeypatch.setattr(connectors, "sleep", lambda s: None)
    src = isrc.create_source(db, world["hospital"].id, world["users"]["admin"], "Reference ERP (simulated)", "erp",
                             "rest_pull", list(engine.ENTITIES),
                             {"base_url": "reference-erp", "auth_env": "REFERENCE_ERP_TOKEN", "page_size": 2,
                              "schedule_minutes": 60})
    from app.models import IntegrationMapping

    for ent, fm in reference_erp.MAPPINGS.items():
        db.add(IntegrationMapping(hospital_id=src.hospital_id, source_id=src.id, entity=ent, field_map=fm, defaults={}))
    db.commit()
    return src


def _sync(c, src_id, **kw):
    r = c.post(f"/api/integrations/sources/{src_id}/sync", json=kw)
    assert r.status_code == 200, r.text
    return {x["entity"]: x for x in r.json()}


def test_rest_pull_from_the_reference_erp_end_to_end(login, db, world, erp, monkeypatch):
    monkeypatch.setattr(reference_erp, "DELTAS", {"GLV-7": 30})
    _stock(db, world, 300)
    a = as_role(login, "procurement_manager")
    t = a.post(f"/api/integrations/sources/{erp.id}/test").json()
    assert t["ok"] is True and "SIMULATED" in t["message"]
    runs = _sync(a, erp.id)
    assert list(runs) == ["departments", "suppliers", "items", "supplier_items", "purchase_orders", "deliveries",
                          "consumption", "inventory"]
    assert all(r["mode"] == "initial" for r in runs.values())
    assert runs["suppliers"]["records_created"] == 1  # HLS (simulated new vendor); SUP unchanged
    assert runs["items"]["records_created"] == 1 and runs["items"]["records_unchanged"] == 1  # GLV-NIT-L new
    assert runs["purchase_orders"]["records_created"] == 1 and runs["deliveries"]["records_created"] == 1
    assert runs["consumption"]["status"] == "PARTIAL" and runs["consumption"]["records_created"] == 1
    assert runs["consumption"]["error_summary"]["reasons"] == {"unknown_item": 1}
    assert runs["inventory"]["error_summary"]["info"] == {"reconciliation_opened": 1}
    db.expire_all()
    nit = db.scalar(select(Consumable).where(Consumable.sku == "GLV-NIT-L"))
    assert stock.stock_levels(db, world["hospital"].id, [nit.id])[nit.id].usable == 80  # received against the PO
    hls = db.scalar(select(Supplier).where(Supplier.code == "HLS"))
    order = db.scalar(select(SupplierOrder).where(SupplierOrder.supplier_id == hls.id))
    assert (order.status, order.quantity_received, order.quantity_ordered) == ("PARTIAL", 80, 200)
    issue = db.scalar(select(ReconciliationIssue).where(ReconciliationIssue.status == "OPEN"))
    assert (issue.consumable.sku, issue.difference) == ("GLV-7", 30)
    assert db.scalar(select(SyncCheckpoint.cursor).where(SyncCheckpoint.source_id == erp.id,
                                                         SyncCheckpoint.entity == "consumption"))
    # incremental: only records changed since the checkpoint come back — and re-applying them changes nothing
    runs2 = _sync(a, erp.id)
    assert all(r["mode"] == "incremental" and r["checkpoint_before"] for r in runs2.values())
    assert sum(r["records_created"] + r["records_updated"] for r in runs2.values()) == 0
    # the cursor is inclusive: boundary records come back once more and are recognised (unchanged), never re-applied
    assert all(r["records_received"] == r["records_unchanged"] + r["records_rejected"] for r in runs2.values())
    assert runs2["consumption"]["records_unchanged"] == 1
    db.expire_all()
    assert db.scalar(select(func.count(SupplierOrder.id)).where(SupplierOrder.supplier_id == hls.id)) == 1
    ov = a.get("/api/integrations/overview").json()
    row = ov["sources"][0]
    assert row["health"] == "degraded" and row["open_reconciliation"] == 1 and row["is_simulated"] is True
    assert row["rejected_7d"] >= 2 and ov["totals"]["received_7d"] > 0
    # dry-run full resync writes nothing
    dry = _sync(a, erp.id, full=True, dry_run=True)
    assert all(r["mode"] == "dry_run" for r in dry.values())


def test_simulator_is_off_by_default_and_protected(db, world, erp, login, monkeypatch):
    c = TestClient(app)
    base = f"/api/reference-erp/{world['hospital'].code}"
    assert c.get(f"{base}/materials").status_code == 401
    ok = c.get(f"{base}/materials", headers={"Authorization": "Bearer test-erp-token"})
    assert ok.status_code == 200 and ok.json()["simulated"] is True
    assert c.get(f"/api/reference-erp/{world['other'].code}/materials",
                 headers={"Authorization": "Bearer test-erp-token"}).status_code == 404  # not a demo hospital
    monkeypatch.setenv("REFERENCE_ERP_TOKEN", "stale-token")  # MedFlow's copy of the credential no longer matches
    a = as_role(login, "admin")
    runs = _sync(a, erp.id, entities=["items"])
    assert runs["items"]["status"] == "FAILED" and "Authentication failed" in runs["items"]["error_summary"]["fatal"]
    monkeypatch.setattr(settings, "REFERENCE_ERP_ENABLED", False)
    assert c.get(f"{base}/materials", headers={"Authorization": "Bearer test-erp-token"}).status_code == 404
    runs = _sync(a, erp.id, entities=["items"])
    assert "disabled" in runs["items"]["error_summary"]["fatal"]
    db.expire_all()
    s = db.get(IntegrationSource, erp.id)
    assert s.last_failure_at is not None and s.last_success_at is None and "disabled" in s.last_error


def test_rest_pull_retries_paginates_and_checkpoints(db, world, monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_ALLOWED_HOSTS", ["erp.hospital.test"])
    monkeypatch.setenv("MEDFLOW_INTEGRATION_ERP_TOKEN", "s3cret")
    monkeypatch.setattr(connectors, "sleep", lambda s: None)
    calls = []
    t0, t1 = "2026-09-01T10:00:00+00:00", "2026-09-02T10:00:00+00:00"

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.headers["Authorization"] == "Bearer s3cret"
        if len(calls) == 1:
            return httpx.Response(503)  # transient
        page = int(req.url.params["page"])
        since = req.url.params.get("updated_since")
        rows = [{"code": "A-1", "name": "Alpha", "unit": "piece", "updated_at": t0},
                {"code": "B-2", "name": "Beta", "unit": "piece", "updated_at": t1}]
        if since:
            rows = [r for r in rows if r["updated_at"] >= since]
        chunk = rows[page - 1:page]
        return httpx.Response(200, json={"items": chunk, "next_page": page + 1 if page < len(rows) else None})

    monkeypatch.setattr(connectors, "client_factory", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    src = isrc.create_source(db, world["hospital"].id, None, "Real-shaped ERP", "erp", "rest_pull", ["items"],
                             {"base_url": "https://erp.hospital.test/v2", "auth_env": "MEDFLOW_INTEGRATION_ERP_TOKEN",
                              "page_size": 1})
    db.commit()
    [run] = connectors.sync_source(db, src, None, "manual")
    assert (run.status, run.records_created, run.checkpoint_after) == ("SUCCESS", 2, t1)
    assert len(calls) == 3 and str(calls[1].url).startswith("https://erp.hospital.test/v2/items")
    [run2] = connectors.sync_source(db, src, None, "schedule")
    assert run2.checkpoint_before == t1 and run2.records_received == 1 and run2.records_unchanged == 1
    # 401 is not retried; nothing is applied and the checkpoint stays
    monkeypatch.setattr(connectors, "client_factory",
                        lambda: httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(401))))
    n = len(calls)
    [run3] = connectors.sync_source(db, src, None, "manual")
    assert run3.status == "FAILED" and len(calls) == n + 1
    assert engine.get_checkpoint(db, src, "items") == t1
    # a server that keeps failing: bounded retries
    monkeypatch.setattr(connectors, "client_factory",
                        lambda: httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(502))))
    n = len(calls)
    [run4] = connectors.sync_source(db, src, None, "manual")
    assert run4.status == "FAILED" and "unreachable after" in run4.error_summary["fatal"]
    assert len(calls) == n + settings.INTEGRATION_HTTP_RETRIES + 1
    monkeypatch.delenv("MEDFLOW_INTEGRATION_ERP_TOKEN")
    [run5] = connectors.sync_source(db, src, None, "manual")
    assert "not set" in run5.error_summary["fatal"]


def test_scheduler_picks_due_rest_pull_sources_only(db, world):
    due = isrc.create_source(db, world["hospital"].id, None, "Due", "erp", "rest_pull", ["items"],
                             {"base_url": "reference-erp", "schedule_minutes": 30})
    isrc.create_source(db, world["hospital"].id, None, "Manual only", "erp", "rest_pull", ["items"],
                       {"base_url": "reference-erp"})
    isrc.create_source(db, world["hospital"].id, None, "Push", "erp", "api_push", ["items"], {})
    db.commit()
    assert [s.name for s in connectors.due_sources(db)] == ["Due"]
    due.last_run_at = utcnow() - timedelta(minutes=10)
    db.commit()
    assert connectors.due_sources(db) == []
    assert [s.name for s in connectors.due_sources(db, utcnow() + timedelta(minutes=25))] == ["Due"]


# ---------------------------------------------------------------- audit trail, isolation, downstream data


def test_every_run_is_an_audit_record(login, db, world):
    src = _source(db, world, "upload", ["suppliers"])
    a = as_role(login, "admin")
    run = _upload(a, src.id, "suppliers", "s.csv", b"code,name\nNEW,New supplier\nBAD CODE!,x\n").json()
    for k in ("started_at", "completed_at", "records_received", "records_created", "records_updated", "records_rejected",
              "status", "error_summary", "file_name", "file_sha256", "source_name"):
        assert run[k] is not None, k
    assert run["triggered_by"]["full_name"] == "Admin"
    db.expire_all()
    log = db.scalar(select(AuditLog).where(AuditLog.action == "integration.sync"))
    assert log.hospital_id == world["hospital"].id and log.details["rejected"] == 1 and log.details["file"] == "s.csv"
    listed = a.get("/api/integrations/runs", params={"source_id": src.id}).json()
    assert listed["total"] == 1 and listed["items"][0]["status"] == "PARTIAL"


def test_integration_data_is_isolated_per_hospital(login, db, world):
    other = world["other"]
    b_src = isrc.create_source(db, other.id, None, "B ERP", "erp", "api_push", ["items", "inventory"], {})
    key_b = _key(db, b_src)
    a_src = _source(db, world, "api_push", ["items"])
    c = TestClient(app)
    # B's key writes into B only, even with codes that exist in A
    r = _push(c, key_b, "items", [{"code": "GLV-7", "name": "B renamed gloves", "unit": "pair"}]).json()
    assert r["created"] == 1  # a new B item — A's GLV-7 is invisible to B's source
    db.expire_all()
    assert db.get(Consumable, world["gloves"].id).name == "Surgical gloves 7"
    b_run = db.scalar(select(SyncRun).where(SyncRun.hospital_id == other.id))
    a = as_role(login, "admin")
    assert a.get(f"/api/integrations/runs/{b_run.id}").status_code == 404
    assert a.get(f"/api/integrations/sources/{b_src.id}").status_code == 404
    assert a.post(f"/api/integrations/runs/{b_run.id}/retry").status_code == 404
    assert [s["id"] for s in a.get("/api/integrations/sources").json()] == [a_src.id]
    assert a.get("/api/integrations/runs").json()["total"] == 0
    assert "B ERP" not in a.get("/api/integrations/overview").text


def test_imported_data_reaches_the_existing_services(login, db, world):
    """V9 brings data in; V1–V5 read it through their unchanged code paths."""
    _stock(db, world, 300)
    src = _source(db, world, "api_push", ["consumption", "purchase_orders"])
    key = _key(db, src)
    c = TestClient(app)
    _push(c, key, "consumption", [{"external_id": f"C{i}", "item_code": "GLV-7", "department_code": "ORTHO",
                                   "quantity": 25, "occurred_at": _now_iso()} for i in range(3)])
    _push(c, key, "purchase_orders", [{"po_number": "PO-77", "supplier_code": "SUP", "item_code": "GLV-7",
                                       "quantity": 500, "order_date": business_today().isoformat()}])
    db.expire_all()
    series = stock.daily_series(db, world["hospital"].id, 1, world["gloves"].id)  # the V2 consumption signal
    assert series[-1]["issued"] == 75
    a = as_role(login, "admin")
    inv = a.get(f"/api/inventory/{world['gloves'].id}")
    assert inv.status_code == 200 and inv.json()["stock"]["usable_stock"] == 225  # V1/V3 stock position
    orders = a.get("/api/supplier-orders", params={"status": "OPEN"}).json()  # V4 order log (→ V5 in-transit)
    refs = [o["reference"] for o in (orders["items"] if isinstance(orders, dict) else orders)]
    assert "PO-77" in refs


def test_future_stamped_seed_movements_do_not_block_or_skew_imports(client, db, world):
    """The synthetic demo seed stamps some of "today's" movements later in the day. A transaction made now must still be
    accepted (as a manual issue would be), and a snapshot is compared with the ledger rewound to its time."""
    _stock(db, world, 300)
    stock.receive(db, world["users"]["inventory_manager"], world["gloves"], 50, "LOT-F", None, None, 40, "SEED", None,
                  at=utcnow() + timedelta(hours=3))
    db.commit()
    src = _source(db, world, "api_push", ["consumption", "inventory"])
    key = _key(db, src)
    t = {"external_id": "NOW-1", "item_code": "GLV-7", "department_code": "ICU", "quantity": 5, "occurred_at": _now_iso()}
    assert _push(client, key, "consumption", [t]).json()["created"] == 1
    snap = _push(client, key, "inventory", [{"item_code": "GLV-7", "quantity": 295, "as_of": _now_iso()}]).json()
    assert (snap["unchanged"], snap["info"]) == (1, {})  # 300 − 5 at that time; the future +50 is not part of it
