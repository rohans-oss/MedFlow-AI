"""V6 — operational knowledge graph: projection (pure), sync + verification, staleness / auto re-sync, explanation chain,
impact analysis, predefined queries, permissions, hospital isolation, and graceful degradation when the graph store is
down (core MedFlow unaffected).

Graph-store tests need an openCypher engine: set TEST_GRAPH_URL (redis://… → FalkorDB, bolt://… → Neo4j, with
TEST_GRAPH_PASSWORD). Without it those tests are skipped; projection and degradation tests always run.
"""

import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import business_today
from app.graph import projection, store, sync
from app.graph import queries as Q
from app.models import (
    AuditLog,
    Consumable,
    GraphSyncRun,
    ProcedureItemMapping,
    ProcedureSchedule,
    ProcedureType,
    ProcurementRecommendation,
    SupplierOrder,
    SupplierProduct,
)
from app.services import stock
from tests.conftest import as_role
from tests.test_procurement import _world

GRAPH_URL = os.getenv("TEST_GRAPH_URL")


def _graph_world(db, world):
    """V5 test world (+ gloves: RELY reliable, CHEAP unreliable, HIGH risk) + a procedure using gloves, department
    usage, and a second item supplied only by CHEAP."""
    rely, cheap = _world(db, world)
    h, gloves, ortho = world["hospital"], world["gloves"], world["ortho"]
    today = business_today()
    tkr = ProcedureType(hospital_id=h.id, department_id=ortho.id, code="TKR", name="Total knee replacement",
                        is_synthetic=True)
    db.add(tkr)
    db.flush()
    db.add(ProcedureItemMapping(hospital_id=h.id, procedure_type_id=tkr.id, consumable_id=gloves.id,
                                quantity_per_procedure=4, is_synthetic=True))
    for d in range(1, 8):
        db.add(ProcedureSchedule(hospital_id=h.id, procedure_type_id=tkr.id, department_id=ortho.id,
                                 scheduled_date=today + timedelta(days=d), count=2, is_synthetic=True))
    wax = Consumable(hospital_id=h.id, sku="WAX", name="Bone wax", unit="piece", reorder_level=5)
    db.add(wax)
    db.flush()
    db.add(SupplierProduct(supplier_id=cheap.id, consumable_id=wax.id, unit_price=90, lead_time_days=2, moq=1))
    db.add(ProcedureItemMapping(hospital_id=h.id, procedure_type_id=tkr.id, consumable_id=wax.id,
                                quantity_per_procedure=1, is_synthetic=True))
    stock.issue(db, world["users"]["admin"], gloves, 30, ortho, None, None)
    stock.issue(db, world["users"]["admin"], gloves, 10, world["icu"], None, None)
    db.commit()
    return rely, cheap, tkr, wax


# ---------------------------------------------------------------- projection (pure, no graph store needed)


def test_projection_nodes_edges_evidence_and_isolation(db, world):
    rely, cheap, tkr, wax = _graph_world(db, world)
    hid = world["hospital"].id
    p = projection.build(db, hid)
    nc = p.node_counts()
    assert nc["Hospital"] == 1 and nc["Item"] == 2 and nc["Supplier"] == 2 and nc["Procedure"] == 1
    assert nc["StockoutRisk"] == 1 and nc["SupplierOrder"] == 40 and nc["Batch"] == 1
    keys = {n["key"] for rows in p.nodes.values() for n in rows}
    assert all(k.startswith(f"{hid}:") for k in keys)  # hospital-scoped keys
    assert world["other_item"].id not in {n["props"]["pg_id"] for n in p.nodes["Item"]}  # other hospital excluded
    uses_item = p.edges[("USES_ITEM", "Procedure", "Item")]
    g = next(e for e in uses_item if e["b"] == f"{hid}:Item:{world['gloves'].id}")
    assert g["props"]["quantity_per_procedure"] == 4 and g["props"]["units_next_14"] == 56  # 7 days × 2 × 4
    sup = {e["a"]: e["props"] for e in p.edges[("SUPPLIES", "Supplier", "Item")] if e["b"].endswith(f":{world['gloves'].id}")}
    r = sup[f"{hid}:Supplier:{rely.id}"]
    assert r["window_days"] == 3 and (r["window_k"], r["window_n"]) == (20, 20) and r["evidence_basis"] == "item"
    use = {e["a"]: e["props"] for e in p.edges[("USES", "Department", "Item")]}
    assert use[f"{hid}:Department:{world['ortho'].id}"]["units_90"] == 30
    assert use[f"{hid}:Department:{world['ortho'].id}"]["share"] == pytest.approx(0.75)
    # every relationship endpoint is a projected node
    for (_, _a, _b), rows in p.edges.items():
        for e in rows:
            assert e["a"] in keys and e["b"] in keys
    # no patient data can be projected: only these labels exist
    assert set(p.nodes) <= set(projection.LABELS)


def test_fingerprint_changes_when_postgres_changes(db, world):
    _graph_world(db, world)
    hid = world["hospital"].id
    before = projection.fingerprint(db, hid)
    assert projection.fingerprint(db, hid) == before
    stock.issue(db, world["users"]["admin"], world["gloves"], 5, world["ortho"], None, None)
    db.commit()
    after = projection.fingerprint(db, hid)
    assert after["movements"] != before["movements"] and after["batches"] != before["batches"]


# ---------------------------------------------------------------- graceful degradation (no graph store)


@pytest.fixture()
def graph_down(monkeypatch):
    monkeypatch.setattr(settings, "GRAPH_BACKEND", "falkordb")
    monkeypatch.setattr(settings, "GRAPH_URL", "redis://127.0.0.1:1")  # nothing listens here
    monkeypatch.setattr(settings, "GRAPH_TIMEOUT_SECONDS", 0.5)
    store.reset()
    yield
    store.reset()


def test_graph_down_returns_503_and_core_medflow_keeps_working(login, world, db, graph_down):
    _graph_world(db, world)
    c = as_role(login, "procurement_manager")
    st = c.get("/api/graph/status").json()
    assert st["available"] is False and st["error"] and st["current"] is None
    for url in (f"/api/graph/items/{world['gloves'].id}/explain", "/api/graph/schema",
                f"/api/graph/impact/suppliers/{world['supplier'].id}"):
        r = c.get(url)
        assert r.status_code == 503 and "unaffected" in r.json()["detail"]
    r = c.post("/api/graph/sync")
    assert r.status_code == 503
    assert db.scalar(select(GraphSyncRun).order_by(GraphSyncRun.id.desc())).status == "FAILED"
    # the rest of MedFlow does not depend on the graph
    for url in ("/api/inventory", "/api/dashboard/summary", "/api/suppliers", "/api/procurement/needs-attention",
                f"/api/procurement/items/{world['gloves'].id}/plan", "/api/supplier-intelligence/scorecards"):
        assert c.get(url).status_code == 200, url
    store_c = as_role(login, "inventory_manager")
    assert store_c.post("/api/inventory/receive", json={"consumable_id": world["gloves"].id, "quantity": 5,
                                                        "lot_number": "G2"}).status_code == 201
    g = as_role(login, "procurement_manager").post("/api/procurement/recommendations/generate", json={})
    assert g.status_code == 201


def test_graph_disabled(login, world, db, monkeypatch):
    monkeypatch.setattr(settings, "GRAPH_BACKEND", "disabled")
    store.reset()
    c = as_role(login, "viewer")
    assert c.get("/api/graph/status").json()["configured"] is False
    assert c.get("/api/graph/schema").status_code == 503
    store.reset()


# ---------------------------------------------------------------- with a graph store


@pytest.fixture()
def graph(monkeypatch):
    if not GRAPH_URL:
        pytest.skip("TEST_GRAPH_URL not set (needs FalkorDB redis://… or Neo4j bolt://…)")
    backend = "neo4j" if GRAPH_URL.startswith(("bolt", "neo4j")) else "falkordb"
    monkeypatch.setattr(settings, "GRAPH_BACKEND", backend)
    monkeypatch.setattr(settings, "GRAPH_URL", GRAPH_URL)
    monkeypatch.setattr(settings, "GRAPH_NAME", "medflow_test" if backend == "falkordb" else "medflow")
    monkeypatch.setattr(settings, "GRAPH_PASSWORD", os.getenv("TEST_GRAPH_PASSWORD", ""))
    store.reset()
    ex = store.get_executor()
    ex.run("MATCH (n) DETACH DELETE n")  # test ids restart at 1 for every test
    yield ex
    store.reset()


def test_sync_is_verified_idempotent_and_removes_stale_nodes(db, world, graph):
    rely, cheap, tkr, wax = _graph_world(db, world)
    hid = world["hospital"].id
    r1 = sync.sync(db, hid)
    assert r1.status == "SUCCESS" and r1.verified and r1.graph_node_counts == r1.node_counts
    assert r1.graph_edge_counts == r1.edge_counts and r1.graph_node_counts["Item"] == 2
    r2 = sync.sync(db, hid)
    assert r2.verified and r2.graph_node_counts == r1.graph_node_counts and r2.removed_nodes == 0
    wax.is_active = False  # deactivated in PostgreSQL → gone from the graph on the next sync
    db.commit()
    r3 = sync.sync(db, hid)
    assert r3.verified and r3.graph_node_counts["Item"] == 1 and r3.removed_nodes == 1
    assert graph.run("MATCH (i:Item {key: $k}) RETURN count(i) AS c", {"k": f"{hid}:Item:{wax.id}"})[0]["c"] == 0
    ok, last, changed = sync.is_current(db, hid)
    assert ok and last.id == r3.id and changed == []


def test_reads_resync_when_postgres_changed(login, world, db, graph):
    _graph_world(db, world)
    c = as_role(login, "viewer")
    e1 = c.get(f"/api/graph/items/{world['gloves'].id}/explain").json()
    usable1 = next(s for s in e1["data"]["steps"] if s["kind"] == "item")["lines"][0]
    assert "Usable stock 80 pair" in usable1  # 120 received − 40 issued
    as_role(login, "inventory_manager").post("/api/inventory/receive", json={
        "consumable_id": world["gloves"].id, "quantity": 100, "lot_number": "NEW"})
    st = as_role(login, "viewer").get("/api/graph/status").json()
    assert st["current"] is False and "movements" in st["changed"]
    e2 = as_role(login, "viewer").get(f"/api/graph/items/{world['gloves'].id}/explain").json()
    assert e2["sync_run_id"] != e1["sync_run_id"]
    assert "Usable stock 180 pair" in next(s for s in e2["data"]["steps"] if s["kind"] == "item")["lines"][0]
    assert as_role(login, "viewer").get("/api/graph/status").json()["current"] is True


def test_explanation_chain_traverses_risk_forecast_procedures_suppliers(login, world, db, graph):
    rely, cheap, tkr, wax = _graph_world(db, world)
    n_orders, n_recs = db.scalar(select(func.count(SupplierOrder.id))), db.scalar(select(func.count(ProcurementRecommendation.id)))
    d = as_role(login, "viewer").get(f"/api/graph/items/{world['gloves'].id}/explain").json()["data"]
    kinds = [s["kind"] for s in d["steps"]]
    assert kinds[:2] == ["item", "risk"] and {"procedures", "departments", "suppliers"} <= set(kinds)
    text = " ".join(" ".join(s["lines"]) for s in d["steps"])
    assert "HIGH stockout risk: 90% probability" in text
    assert "Total knee replacement (Orthopaedics): 14 scheduled × 4.0 = 56 pair" in text
    assert "20/20 past orders delivered within 3 days" in text  # RELY
    assert "Orthopaedics: 30 pair issued in the last 90 days (75%)" in text
    assert "HIGH risk" not in d["summary"] and "high risk (90%)" in d["summary"]
    labels = {n["label"] for n in d["graph"]["nodes"]}
    assert {"Item", "StockoutRisk", "Supplier", "Procedure", "Department"} <= labels
    assert all(e["from"] in {n["id"] for n in d["graph"]["nodes"]} for e in d["graph"]["edges"])
    assert "MATCH" in d["cypher"]["procedures"]
    # reading the graph never changes business data
    assert db.scalar(select(func.count(SupplierOrder.id))) == n_orders
    assert db.scalar(select(func.count(ProcurementRecommendation.id))) == n_recs


def test_supplier_impact_classifies_sole_source_and_follows_procedures(login, world, db, graph):
    rely, cheap, tkr, wax = _graph_world(db, world)
    c = as_role(login, "procurement_manager")
    d = c.get(f"/api/graph/impact/suppliers/{cheap.id}").json()["data"]
    items = {i["sku"]: i for i in d["items"]}
    assert items["WAX"]["sole_source"] and items["WAX"]["severity"] == "high"
    assert not items["GLV-7"]["sole_source"] and items["GLV-7"]["alternatives"] == ["RELY"]
    assert items["GLV-7"]["severity"] == "watch"  # HIGH risk but an alternative exists
    proc = d["procedures"][0]
    assert proc["procedure"] == "Total knee replacement" and proc["sole_source_items"] == 1 and proc["scheduled_next_14"] == 14
    assert "1 have no active alternative supplier" in d["summary"][0]
    assert d["chains"][0][1]["name"] == "Bone wax" and d["chains"][0][2]["label"] == "Procedure"
    assert any(x["department"] == "Orthopaedics" for x in d["departments"])
    it = c.get(f"/api/graph/impact/items/{world['gloves'].id}").json()["data"]
    assert it["units_needed_14"] == 56 and it["procedures"][0]["procedures_covered_by_stock"] == 20  # 80 usable / 4
    assert {x["department"] for x in it["departments"]} == {"Orthopaedics", "ICU"}


def test_predefined_queries_permissions_and_isolation(login, world, db, graph):
    rely, cheap, tkr, wax = _graph_world(db, world)
    c = as_role(login, "viewer")
    names = [q["name"] for q in c.get("/api/graph/queries").json()]
    assert len(names) >= 8
    for q in c.get("/api/graph/queries").json():
        body = {"item_id": world["gloves"].id, "supplier_id": cheap.id}
        r = c.post(f"/api/graph/queries/{q['name']}", json=body)
        assert r.status_code == 200, (q["name"], r.text)
        assert r.json()["data"]["cypher"] == q["cypher"]
    rows = c.post("/api/graph/queries/single_source_items", json={}).json()["data"]["rows"]
    assert [r["sku"] for r in rows] == ["WAX"]
    assert c.post("/api/graph/queries/suppliers_for_item", json={}).status_code == 422
    assert c.post("/api/graph/queries/nope", json={}).status_code == 404
    # permissions: viewers read, inventory/procurement/admin may sync
    assert c.post("/api/graph/sync").status_code == 403
    r = as_role(login, "inventory_manager").post("/api/graph/sync")
    assert r.status_code == 200 and r.json()["verified"]
    assert db.scalar(select(AuditLog).where(AuditLog.action == "graph.sync")) is not None
    # isolation: another hospital cannot address this hospital's nodes and sees only its own graph
    other = login("admin@other.demo")
    assert other.get(f"/api/graph/items/{world['gloves'].id}/explain").status_code == 404
    assert other.get(f"/api/graph/impact/suppliers/{cheap.id}").status_code == 404
    assert other.post("/api/graph/queries/suppliers_for_item", json={"item_id": world["gloves"].id}).status_code == 404
    osch = other.get("/api/graph/schema").json()["data"]
    assert {x["label"]: x["count"] for x in osch["labels"]}["Item"] == 1  # only its own item
    assert other.post("/api/graph/queries/single_source_items", json={}).json()["data"]["rows"][0]["sku"] == "X-1"
    mine = as_role(login, "viewer").get("/api/graph/schema").json()["data"]
    assert {x["label"]: x["count"] for x in mine["labels"]}["Item"] == 2  # unaffected by the other hospital's sync


def test_query_catalog_is_hospital_scoped():
    for q in Q.CATALOG:
        c = q["cypher"]
        assert "$hid" in c or "$item" in c or "$supplier" in c, q["name"]
