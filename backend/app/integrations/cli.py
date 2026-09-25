"""V9 — scheduled data feeds from the command line (cron / a scheduler container runs `run-due` every few minutes).

    python -m app.integrations.cli run-due            # every enabled rest_pull source whose schedule has elapsed
    python -m app.integrations.cli sync --source 3    # one source now (add --full to ignore checkpoints)
    python -m app.integrations.cli list               # sources and their last run, per hospital

Each source runs for its own hospital (explicit hospital scope, like `ml.train` and `graph.cli`). A failure in one source
is recorded as a FAILED run and never stops the others.
"""

import argparse
import sys

from sqlalchemy import select

from app.db.session import SessionLocal
from app.integrations import connectors
from app.integrations.engine import SyncError
from app.models import Hospital, IntegrationSource


def _print_runs(source: IntegrationSource, runs) -> None:
    for r in runs:
        print(f"  [{source.hospital_id}] {source.name} · {r.entity:<16} {r.status:<8} received {r.records_received}, "
              f"created {r.records_created}, updated {r.records_updated}, unchanged {r.records_unchanged}, "
              f"rejected {r.records_rejected}" + (f" — {r.error_summary['fatal']}" if (r.error_summary or {}).get("fatal") else ""))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MedFlow integration syncs")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run-due")
    s = sub.add_parser("sync")
    s.add_argument("--source", type=int, required=True)
    s.add_argument("--full", action="store_true")
    sub.add_parser("list")
    args = p.parse_args(argv)
    failures = 0
    with SessionLocal() as db:
        if args.cmd == "list":
            for src, code in db.execute(select(IntegrationSource, Hospital.code).join(Hospital)
                                        .order_by(Hospital.code, IntegrationSource.name)):
                print(f"{src.id:>4}  {code:<14} {src.name:<40} {src.connector:<9} enabled={src.enabled} "
                      f"last_success={src.last_success_at} last_failure={src.last_failure_at}")
            return 0
        sources = connectors.due_sources(db) if args.cmd == "run-due" else [db.get(IntegrationSource, args.source)]
        if args.cmd == "sync" and sources[0] is None:
            print("No such source", file=sys.stderr)
            return 2
        if not sources:
            print("No integration source is due.")
        for src in sources:
            try:
                runs = connectors.sync_source(db, src, None, "schedule" if args.cmd == "run-due" else "manual",
                                              full=getattr(args, "full", False))
            except SyncError as e:
                db.rollback()
                print(f"  {src.name}: {e}", file=sys.stderr)
                failures += 1
                continue
            _print_runs(src, runs)
            failures += sum(r.status == "FAILED" for r in runs)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
