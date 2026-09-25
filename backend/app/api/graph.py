"""V6 — operational knowledge graph API (reads the graph; PostgreSQL stays the source of truth).

GET  /graph/status                      store health, last sync, is the graph behind PostgreSQL? (never syncs)
POST /graph/sync                        rebuild the projection now (graph:sync)
GET  /graph/schema                      node labels / relationship types with counts
GET  /graph/items/{id}/explain          V6.3 why is this item at risk? (explanation chain + subgraph)
GET  /graph/impact/suppliers/{id}       V6.4 what if this supplier becomes unavailable?
GET  /graph/impact/items/{id}           V6.4 what depends on this item?
GET  /graph/queries                     V6.5 predefined questions (with their Cypher)
POST /graph/queries/{name}              run one (item_id / supplier_id parameters)

Graph reads re-project automatically when PostgreSQL changed since the last sync. When the graph store is unreachable
these endpoints answer 503 — nothing else in MedFlow depends on the graph.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import DB, client_ip, get_owned, require
from app.core.config import settings
from app.core.permissions import GRAPH_SYNC, READ
from app.graph import queries as Q
from app.graph import store, sync
from app.graph.store import GraphUnavailable
from app.models import Consumable, GraphSyncRun, Supplier, User
from app.schemas.graph import GraphResult, GraphStatus, QueryDef, QueryIn, SyncRunOut
from app.services import audit

router = APIRouter(prefix="/graph", tags=["knowledge graph"])
Reader = Annotated[User, Depends(require(READ))]
Syncer = Annotated[User, Depends(require(GRAPH_SYNC))]

UNAVAILABLE = ("The knowledge graph is unavailable ({err}). Inventory, forecasting, stockout risk, supplier intelligence "
               "and procurement are unaffected — they never read from the graph.")


def _unavailable(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE.format(err=e))


def _graph(db: DB, user: User) -> tuple[store.Executor, GraphSyncRun]:
    """Executor + a projection that matches PostgreSQL (re-synced when stale). 503 when the store is down."""
    from app.api.risk import ensure_current  # today's V3 risk first, so the graph shows current risk

    ensure_current(db, user.hospital_id)
    try:
        run = sync.ensure_synced(db, user.hospital_id, user)
        return store.get_executor(), run
    except GraphUnavailable as e:
        raise _unavailable(e) from e


def _result(run: GraphSyncRun, data: dict) -> dict:
    return {"synced_at": run.finished_at, "sync_run_id": run.id, "data": data}


def _call(fn, *args):
    try:
        return fn(*args)
    except GraphUnavailable as e:
        store.mark_failed(e)
        raise _unavailable(e) from e


@router.get("/status", response_model=GraphStatus)
def graph_status(user: Reader, db: DB):
    available, err = False, None
    if store.configured():
        try:
            store.get_executor()
            available = True
        except GraphUnavailable as e:
            err = str(e)
    else:
        err = "disabled (GRAPH_BACKEND=disabled)"
    current, last, changed = sync.is_current(db, user.hospital_id)
    runs = db.scalars(select(GraphSyncRun).where(GraphSyncRun.hospital_id == user.hospital_id)
                      .order_by(GraphSyncRun.id.desc()).limit(10)).all()
    return {"backend": settings.GRAPH_BACKEND.lower(), "url": settings.GRAPH_URL.split("@")[-1],
            "configured": store.configured(), "available": available, "error": err,
            "current": current if last else None, "changed": changed if last else [],
            "last_success": last, "runs": runs,
            "note": "PostgreSQL is the source of truth; the graph is a projection rebuilt from it."}


@router.post("/sync", response_model=SyncRunOut)
def graph_sync(request: Request, db: DB, user: Syncer):
    run = sync.sync(db, user.hospital_id, trigger="manual", user=user)
    audit.record(db, user, "graph.sync", "graph_sync_run", run.id,
                 {"status": run.status, "verified": run.verified, "nodes": sum((run.graph_node_counts or {}).values()),
                  "error": run.error}, client_ip(request))
    db.commit()
    if run.status != "SUCCESS" and not run.graph_node_counts:
        raise _unavailable(RuntimeError(run.error))
    return run


@router.get("/schema", response_model=GraphResult)
def graph_schema(user: Reader, db: DB):
    ex, run = _graph(db, user)
    return _result(run, _call(Q.schema_counts, ex, user.hospital_id))


@router.get("/items/{item_id}/explain", response_model=GraphResult)
def explain(item_id: int, user: Reader, db: DB):
    get_owned(db, Consumable, item_id, user.hospital_id, "Item")
    ex, run = _graph(db, user)
    out = _call(Q.explain_item, ex, user.hospital_id, item_id)
    if not out.get("found"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item is not in the knowledge graph (inactive?)")
    return _result(run, out)


@router.get("/impact/suppliers/{supplier_id}", response_model=GraphResult)
def supplier_impact(supplier_id: int, user: Reader, db: DB):
    get_owned(db, Supplier, supplier_id, user.hospital_id, "Supplier")
    ex, run = _graph(db, user)
    out = _call(Q.impact_supplier, ex, user.hospital_id, supplier_id)
    if not out.get("found"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found in the knowledge graph")
    return _result(run, out)


@router.get("/impact/items/{item_id}", response_model=GraphResult)
def item_impact(item_id: int, user: Reader, db: DB):
    get_owned(db, Consumable, item_id, user.hospital_id, "Item")
    ex, run = _graph(db, user)
    out = _call(Q.impact_item, ex, user.hospital_id, item_id)
    if not out.get("found"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item is not in the knowledge graph (inactive?)")
    return _result(run, out)


@router.get("/queries", response_model=list[QueryDef])
def list_queries(user: Reader):
    return [{"name": q["name"], "title": q["title"], "params": q["params"], "cypher": q["cypher"]} for q in Q.CATALOG]


@router.post("/queries/{name}", response_model=GraphResult)
def run_query(name: str, body: QueryIn, user: Reader, db: DB):
    q = Q.CATALOG_BY_NAME.get(name)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown query")
    if "item" in q["params"]:
        if body.item_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose an item")
        get_owned(db, Consumable, body.item_id, user.hospital_id, "Item")
    if "supplier" in q["params"]:
        if body.supplier_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a supplier")
        get_owned(db, Supplier, body.supplier_id, user.hospital_id, "Supplier")
    ex, run = _graph(db, user)
    return _result(run, _call(Q.run_catalog, ex, user.hospital_id, name, body.item_id, body.supplier_id))
