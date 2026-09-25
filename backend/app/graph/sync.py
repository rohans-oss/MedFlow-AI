"""V6.2 — PostgreSQL → graph synchronisation (one direction only; PostgreSQL is the source of truth).

    fingerprint(PostgreSQL) ≠ last successful sync?  → project → write with a new generation → delete what the
    generation didn't touch → count what is in the graph → compare with the projection → record a graph_sync_runs row

Writes are idempotent (MERGE on a stable key) so a failed run leaves the previous generation readable, and re-running
repairs it. Graph failures never propagate to inventory, forecasting, risk, supplier or procurement endpoints: those
never call this module.
"""

import threading
import time
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import utcnow
from app.graph import projection, store
from app.graph.projection import LABELS, RELATIONSHIPS, Projection
from app.graph.store import GraphUnavailable
from app.models import GraphSyncRun, User

CHUNK = 500
_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


def latest_run(db: Session, hospital_id: int, success_only: bool = True) -> GraphSyncRun | None:
    q = select(GraphSyncRun).where(GraphSyncRun.hospital_id == hospital_id)
    if success_only:
        q = q.where(GraphSyncRun.status == "SUCCESS")
    return db.scalar(q.order_by(GraphSyncRun.id.desc()).limit(1))


def _chunks(rows: list, n: int = CHUNK):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def write(ex: store.Executor, p: Projection, gen: int) -> int:
    """MERGE every node and relationship with generation `gen`, then remove this hospital's leftovers."""
    hid = p.hospital_id
    ex.ensure_indexes(LABELS)
    for label in LABELS:
        for rows in _chunks(p.nodes.get(label, [])):
            ex.run(f"UNWIND $rows AS r MERGE (n:{label} {{key: r.key}}) "
                   f"SET n += r.props, n.hospital_id = $hid, n.gen = $gen", {"rows": rows, "hid": hid, "gen": gen})
    for (rel, a, b), rows_all in p.edges.items():
        for rows in _chunks(rows_all):
            ex.run(f"UNWIND $rows AS r MATCH (a:{a} {{key: r.a}}) MATCH (b:{b} {{key: r.b}}) "
                   f"MERGE (a)-[e:{rel}]->(b) SET e += r.props, e.hospital_id = $hid, e.gen = $gen",
                   {"rows": rows, "hid": hid, "gen": gen})
    removed = 0
    for label in LABELS:
        removed += ex.run(f"MATCH (n:{label} {{hospital_id: $hid}}) WHERE n.gen <> $gen WITH n, n.key AS k "
                          f"DETACH DELETE n RETURN count(k) AS c", {"hid": hid, "gen": gen})[0]["c"]
    for rel, a, b, _ in RELATIONSHIPS:
        ex.run(f"MATCH (:{a} {{hospital_id: $hid}})-[e:{rel}]->(:{b}) WHERE e.gen <> $gen DELETE e", {"hid": hid, "gen": gen})
    return removed


def graph_counts(ex: store.Executor, hospital_id: int) -> tuple[dict[str, int], dict[str, int]]:
    nodes = {lb: ex.run(f"MATCH (n:{lb} {{hospital_id: $hid}}) RETURN count(n) AS c", {"hid": hospital_id})[0]["c"]
             for lb in LABELS}
    edges: dict[str, int] = {}
    for rel, a, b, _ in RELATIONSHIPS:
        c = ex.run(f"MATCH (:{a} {{hospital_id: $hid}})-[e:{rel}]->(:{b}) RETURN count(e) AS c", {"hid": hospital_id})[0]["c"]
        if c:
            edges[f"{a}-{rel}->{b}"] = c
    return nodes, edges


def sync(db: Session, hospital_id: int, trigger: str = "manual", user: User | None = None) -> GraphSyncRun:
    """Rebuild the hospital's projection in the graph. Records the run (also when it fails) and commits it."""
    with _locks[hospital_id]:
        run = GraphSyncRun(hospital_id=hospital_id, trigger=trigger, backend=settings.GRAPH_BACKEND.lower(),
                           started_at=utcnow(), triggered_by_id=user.id if user else None)
        db.add(run)
        db.commit()
        t0 = time.monotonic()
        try:
            ex = store.get_executor()
            fp = projection.fingerprint(db, hospital_id)
            p = projection.build(db, hospital_id)
            run.fingerprint, run.node_counts, run.edge_counts = fp, p.node_counts(), p.edge_counts()
            run.removed_nodes = write(ex, p, run.id)
            gn, ge = graph_counts(ex, hospital_id)
            run.graph_node_counts, run.graph_edge_counts = gn, ge
            run.verified = gn == run.node_counts and ge == run.edge_counts
            run.status = "SUCCESS" if run.verified else "FAILED"
            if not run.verified:
                run.error = "Graph counts differ from the PostgreSQL projection after writing."
        except GraphUnavailable as e:
            store.mark_failed(e)
            run.status, run.error = "FAILED", str(e)
        except Exception as e:  # a failed sync must never break the caller
            if "connect" in str(e).lower():
                store.mark_failed(e)
            run.status, run.error = "FAILED", f"{type(e).__name__}: {e}"[:2000]
        run.finished_at = utcnow()
        run.duration_ms = int((time.monotonic() - t0) * 1000)
        db.commit()
        return run


def is_current(db: Session, hospital_id: int) -> tuple[bool, GraphSyncRun | None, list[str]]:
    """(graph matches PostgreSQL, last successful run, which parts of PostgreSQL changed since)."""
    last = latest_run(db, hospital_id)
    if last is None:
        return False, None, ["never synced"]
    fp = projection.fingerprint(db, hospital_id)
    changed = [k for k, v in fp.items() if (last.fingerprint or {}).get(k) != v]
    return not changed, last, changed


def ensure_synced(db: Session, hospital_id: int, user: User | None = None) -> GraphSyncRun:
    """Read path: re-project when PostgreSQL changed since the last sync. Raises GraphUnavailable if the store is down."""
    store.get_executor()  # fail fast (and without writing a run row) when the graph store is down
    ok, last, _ = is_current(db, hospital_id)
    if ok and last is not None:
        return last
    run = sync(db, hospital_id, trigger="auto", user=user)
    if run.status != "SUCCESS":
        if last is not None and "unavailable" not in (run.error or "").lower() and store.last_error() is None:
            return last  # serve the previous (still consistent) generation, flagged stale by the status endpoint
        raise GraphUnavailable(run.error or "graph sync failed")
    return run
