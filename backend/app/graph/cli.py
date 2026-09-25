"""CLI for the knowledge graph (run inside the backend container or a dev shell):

    python -m app.graph.cli sync     # project every hospital from PostgreSQL into the graph (verified counts)
    python -m app.graph.cli check    # sync, then run every explanation / impact / predefined query and report

`check` is the quickest way to confirm a graph backend (e.g. Neo4j via docker compose) runs all MedFlow Cypher.
"""

import sys
import time

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.graph import queries as Q
from app.graph import store, sync
from app.models import Consumable, Hospital, Supplier


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    print(f"graph backend: {settings.GRAPH_BACKEND} at {settings.GRAPH_URL.split('@')[-1]}")
    failures = 0
    with SessionLocal() as db:
        for h in db.scalars(select(Hospital)).all():
            run = sync.sync(db, h.id, trigger="manual")
            print(f"[{h.code}] sync {run.status} verified={run.verified} "
                  f"nodes={sum((run.graph_node_counts or {}).values())} rels={sum((run.graph_edge_counts or {}).values())} "
                  f"{run.duration_ms} ms {run.error or ''}")
            if run.status != "SUCCESS":
                failures += 1
                continue
            if cmd != "check":
                continue
            ex = store.get_executor()
            items = db.scalars(select(Consumable.id).where(Consumable.hospital_id == h.id, Consumable.is_active.is_(True))).all()
            sups = db.scalars(select(Supplier.id).where(Supplier.hospital_id == h.id)).all()
            t = time.monotonic()
            for i in items:
                assert Q.explain_item(ex, h.id, i)["found"]
                assert Q.impact_item(ex, h.id, i)["found"]
            for s in sups:
                assert Q.impact_supplier(ex, h.id, s)["found"]
            for q in Q.CATALOG:
                r = Q.run_catalog(ex, h.id, q["name"], items[0] if items else None, sups[0] if sups else None)
                print(f"   {q['name']:32s} {len(r['rows']):4d} rows")
            print(f"   explain/impact for {len(items)} items and {len(sups)} suppliers: OK ({time.monotonic() - t:.1f} s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
