"""CLI: train forecasting models (V2) and the stockout-risk model (V3) for every hospital (or one), then generate
V5 procurement recommendations (PENDING — nothing is ordered or approved) and sync the V6 knowledge graph.

    python -m app.ml.train                 # train all hospitals
    python -m app.ml.train --hospital SUNRISE-BLR
    python -m app.ml.train --if-missing    # only hospitals without active models (used by the Docker entrypoint)
"""

import argparse
import json

from sqlalchemy import select

from app.db.session import SessionLocal
from app.ml.pipeline import InsufficientDataError, has_active_model, prune_old_forecasts, run_training
from app.models import Hospital, ProcurementRecommendation, RiskModelVersion
from app.risk.pipeline import run_training as run_risk_training
from app.services import alerts


def main() -> None:
    parser = argparse.ArgumentParser(description="Train MedFlow demand forecasting models")
    parser.add_argument("--hospital", help="hospital code (default: all)")
    parser.add_argument("--if-missing", action="store_true", help="skip hospitals that already have an active model")
    args = parser.parse_args()

    with SessionLocal() as db:
        q = select(Hospital)
        if args.hospital:
            q = q.where(Hospital.code == args.hospital)
        for h in db.scalars(q).all():
            if args.if_missing and has_active_model(db, h.id):
                print(f"[{h.code}] active forecast model exists — skipping V2")
                _risk(db, h, args.if_missing)
                _procurement(db, h, args.if_missing)
                _graph(db, h)
                continue
            try:
                result = run_training(db, h.id)
                prune_old_forecasts(db, h.id)
                db.commit()
            except InsufficientDataError as e:
                db.rollback()
                print(f"[{h.code}] skipped: {e}")
                continue
            summary = {k: {m: (round(v, 4) if isinstance(v, float) else v) for m, v in r.items() if m in ("wape", "mae", "rmse", "bias")}
                       for k, r in result["candidates"].items()}
            print(f"[{h.code}] active={result['active_model']} data={result['data_start']}..{result['data_end']} "
                  f"({result['seconds']}s)\n{json.dumps(summary, indent=2)}")
            pm = result.get("procedure_model") or {}
            if pm.get("trained"):
                folds = ", ".join(f"{f['v2a_wape']:.3f}→{f['v2b_wape']:.3f}" for f in pm["validation_folds"])
                print(f"  V2B procedure-aware: {'ACTIVE' if pm['improved'] else 'not selected'} "
                      f"(WAPE V2A→V2B per window: {folds})")
            else:
                print(f"  V2B procedure-aware: skipped — {pm.get('skipped_reason')}")
            _risk(db, h, False)
            _procurement(db, h, False)
            _graph(db, h)


def _graph(db, h: Hospital) -> None:
    """V6: project PostgreSQL into the knowledge graph. Optional — a missing graph store never fails training."""
    from app.graph import store, sync

    if not store.configured():
        print(f"[{h.code}] knowledge graph disabled — skipping V6 sync")
        return
    run = sync.sync(db, h.id, trigger="train")
    if run.status == "SUCCESS":
        print(f"  V6 knowledge graph: {sum(run.graph_node_counts.values())} nodes, "
              f"{sum(run.graph_edge_counts.values())} relationships ({run.backend}, {run.duration_ms} ms, verified)")
    else:
        print(f"  V6 knowledge graph: not synced — {run.error} (everything else is unaffected)")


def _procurement(db, h: Hospital, if_missing: bool) -> None:
    """V5: generate procurement recommendations for review (PENDING; recommend-only)."""
    from app.services import procurement

    if if_missing and db.scalar(select(ProcurementRecommendation.id).where(ProcurementRecommendation.hospital_id == h.id)):
        print(f"[{h.code}] procurement recommendations exist — skipping V5")
        return
    out = procurement.generate(db, h.id, None)
    db.commit()
    print(f"  V5 procurement: {out['created']} recommendations for review ({out['orders_recommended']} with an order, "
          f"purchase value ₹{out['purchase_value']:,.0f}; solver {out['solver']['status']}) — nothing ordered.")


def _risk(db, h: Hospital, if_missing: bool) -> None:
    """V3: train the stockout-risk model, then score every item and update alerts."""
    if if_missing and db.scalar(select(RiskModelVersion.id).where(RiskModelVersion.hospital_id == h.id,
                                                                  RiskModelVersion.is_active.is_(True))):
        print(f"[{h.code}] active stockout-risk model exists — skipping V3")
        return
    try:
        r = run_risk_training(db, h.id)
        alerts.evaluate(db, h.id)
        db.commit()
    except ValueError as e:
        db.rollback()
        print(f"[{h.code}] V3 stockout risk skipped: {e}")
        return
    bt = r["backtest"][r["model_type"]]
    print(f"  V3 stockout risk: active={r['active_model']} ({r['seconds']}s) — {r['selection']}")
    print(f"     backtest on ledger history: {r['n_events']} stockout events, precision {bt['precision']}, "
          f"recall {bt['recall']}, PR-AUC {bt['pr_auc']}, lead-time-aware event recall {bt['lead_time_recall']}")
    fl = r["backtest_with_floor"].get(r["model_type"])
    if fl:
        print(f"     served policy (+ lead-time rule): precision {fl['precision']}, recall {fl['recall']}, "
              f"FP {fl['fp']}, FN {fl['fn']}, lead-time-aware event recall {fl['lead_time_recall']}")


if __name__ == "__main__":
    main()
