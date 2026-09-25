"""V10 — real-hospital readiness checklist.

Each item has automatic evidence computed from MedFlow's own data (where such evidence exists) and/or a person's
confirmation. An item is "done" when its automatic check passes (auto items), a person confirmed it (manual items), or
both (items marked `both`). This checklist states what has been prepared — it is not a claim that a real hospital
completed it.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    IntegrationMapping,
    Pilot,
    PilotParticipant,
    PilotReadiness,
    ProcurementRecommendation,
    ReconciliationIssue,
    SyncRun,
)
from app.pilots.metrics import pilot_source_ids

# key, group, label, mode (auto | manual | both), hint
ITEMS = [
    ("source_connected", "Integration", "Data source connected", "auto", "at least one enabled V9 source linked to the pilot"),
    ("mapping_verified", "Integration", "Mapping verified", "both", "field mappings saved for the linked sources, reviewed by a person"),
    ("initial_sync", "Integration", "Initial sync completed", "auto", "a successful initial sync / import of every linked source"),
    ("incremental_sync", "Integration", "Incremental sync tested", "auto", "a successful incremental run (or a second import)"),
    ("items_mapped", "Data", "Items mapped", "both", "no unknown-item rejections in the latest run of each entity + confirmation"),
    ("suppliers_mapped", "Data", "Suppliers mapped", "both", "no unknown-supplier rejections in the latest runs + confirmation"),
    ("departments_mapped", "Data", "Departments mapped", "both", "no unknown-department rejections in the latest runs + confirmation"),
    ("inventory_validated", "Data", "Inventory validated", "both", "an inventory snapshot compared and no reconciliation issue left open"),
    ("consumption_validated", "Data", "Consumption validated", "both", "a successful consumption sync + confirmation"),
    ("procurement_verified", "Operations", "Procurement workflow verified", "both", "at least one recommendation decided by a person"),
    ("reconciliation_verified", "Operations", "Stock reconciliation verified", "both",
     "at least one reconciliation issue resolved by a person"),
    ("roles_verified", "Operations", "User roles verified", "manual", "roles of every participant checked"),
    ("baseline_configured", "Pilot", "Baseline period configured", "auto", "baseline dates set and before the pilot"),
    ("pilot_configured", "Pilot", "Pilot period configured", "auto", "pilot dates set"),
    ("users_assigned", "Pilot", "Users assigned", "auto", "at least one participating user"),
    ("departments_assigned", "Pilot", "Departments assigned", "auto", "at least one participating department"),
    ("audit_enabled", "Governance", "Audit logging enabled", "auto", "audit rows are being written for this hospital"),
    ("retention_documented", "Governance", "Data retention documented", "manual", "retention period agreed with the hospital"),
    ("access_reviewed", "Governance", "Access reviewed", "manual", "memberships and roles reviewed with the hospital"),
    ("no_patient_data", "Governance", "No patient data required", "manual",
     "confirmed that the pilot's data contain no patient names, IDs, records, diagnoses or clinical notes"),
]
KEYS = {k for k, *_ in ITEMS}


def _latest_runs(db: Session, src_ids: list[int]) -> list[SyncRun]:
    out = []
    for sid in src_ids:
        seen = set()
        for r in db.scalars(select(SyncRun).where(SyncRun.source_id == sid, SyncRun.mode != "dry_run")
                            .order_by(SyncRun.started_at.desc()).limit(200)):
            if r.entity not in seen:
                seen.add(r.entity)
                out.append(r)
    return out


def evaluate(db: Session, p: Pilot) -> dict:
    src_ids = pilot_source_ids(db, p)
    from app.models import IntegrationSource

    sources = list(db.scalars(select(IntegrationSource).where(IntegrationSource.id.in_(src_ids)))) if src_ids else []
    latest = _latest_runs(db, src_ids)
    def ok_runs(**kw) -> int:
        if not src_ids:
            return 0
        conds = [getattr(SyncRun, k) == v for k, v in kw.items()]
        return db.scalar(select(func.count(SyncRun.id)).where(
            SyncRun.source_id.in_(src_ids), SyncRun.status.in_(["SUCCESS", "PARTIAL"]), SyncRun.mode != "dry_run", *conds)) or 0

    def no_reason(code: str) -> tuple[bool, str]:
        if not latest:
            return False, "no sync yet"
        bad = [r for r in latest if ((r.error_summary or {}).get("reasons") or {}).get(code)]
        return (not bad), ("clean in the latest runs" if not bad else f"{len(bad)} latest run(s) rejected records ({code})")

    per_source_initial = all(db.scalar(select(func.count(SyncRun.id)).where(
        SyncRun.source_id == s.id, SyncRun.status.in_(["SUCCESS", "PARTIAL"]), SyncRun.mode != "dry_run")) for s in sources)
    incremental = (ok_runs(mode="incremental") or 0) > 0 or any(
        (db.scalar(select(func.count(SyncRun.id)).where(SyncRun.source_id == s.id, SyncRun.mode != "dry_run",
                                                         SyncRun.status.in_(["SUCCESS", "PARTIAL"]))) or 0) >= 2 for s in sources)
    mapped = bool(src_ids) and all(db.scalar(select(func.count(IntegrationMapping.id)).where(IntegrationMapping.source_id == s.id))
                                   or s.connector != "rest_pull" for s in sources)
    inv_runs = (ok_runs(entity="inventory") or 0) if src_ids else 0
    open_recon = db.scalar(select(func.count(ReconciliationIssue.id)).where(
        ReconciliationIssue.source_id.in_(src_ids), ReconciliationIssue.status == "OPEN")) if src_ids else 0
    resolved_recon = db.scalar(select(func.count(ReconciliationIssue.id)).where(
        ReconciliationIssue.source_id.in_(src_ids), ReconciliationIssue.status == "RESOLVED",
        ReconciliationIssue.resolved_by_id.is_not(None))) if src_ids else 0
    decided = db.scalar(select(func.count(ProcurementRecommendation.id)).where(
        ProcurementRecommendation.hospital_id == p.hospital_id, ProcurementRecommendation.decided_by_id.is_not(None))) or 0
    n_users = db.scalar(select(func.count(PilotParticipant.id)).where(PilotParticipant.pilot_id == p.id,
                                                                      PilotParticipant.user_id.is_not(None))) or 0
    n_depts = db.scalar(select(func.count(PilotParticipant.id)).where(PilotParticipant.pilot_id == p.id,
                                                                      PilotParticipant.department_id.is_not(None))) or 0
    audit = db.scalar(select(func.count(AuditLog.id)).where(AuditLog.hospital_id == p.hospital_id)) or 0
    enabled = [s for s in sources if s.enabled]
    auto: dict[str, tuple[bool, str]] = {
        "source_connected": (bool(enabled), f"{len(enabled)} enabled source(s) linked"),
        "mapping_verified": (mapped, "mappings saved" if mapped else "no saved mapping for a REST source"),
        "initial_sync": (bool(sources) and per_source_initial, "every linked source has a successful run" if per_source_initial
                         and sources else "a linked source has no successful run yet"),
        "incremental_sync": (incremental, "an incremental / repeated run succeeded" if incremental else "not yet"),
        "items_mapped": no_reason("unknown_item"),
        "suppliers_mapped": no_reason("unknown_supplier"),
        "departments_mapped": no_reason("unknown_department"),
        "inventory_validated": (inv_runs > 0 and not open_recon,
                                f"{inv_runs} inventory run(s), {open_recon} open reconciliation issue(s)"),
        "consumption_validated": ((ok_runs(entity="consumption") or 0) > 0, "successful consumption sync"
                                  if (ok_runs(entity="consumption") or 0) else "no consumption sync yet"),
        "procurement_verified": (decided > 0, f"{decided} recommendation(s) decided by a person"),
        "reconciliation_verified": (resolved_recon > 0, f"{resolved_recon} reconciliation issue(s) resolved by a person"),
        "baseline_configured": (p.baseline_end < p.pilot_start, f"{p.baseline_start} → {p.baseline_end}"),
        "pilot_configured": (True, f"{p.pilot_start} → {p.pilot_end}"),
        "users_assigned": (n_users > 0, f"{n_users} user(s)"),
        "departments_assigned": (n_depts > 0, f"{n_depts} department(s)"),
        "audit_enabled": (audit > 0, f"{audit} audit row(s) in this hospital"),
    }
    confirmations = {r.item_key: r for r in db.scalars(select(PilotReadiness).where(PilotReadiness.pilot_id == p.id))}
    groups: dict[str, list] = {}
    done = 0
    for key, group, label, mode, hint in ITEMS:
        a = auto.get(key)
        c = confirmations.get(key)
        confirmed = bool(c and c.confirmed)
        if mode == "auto":
            ok = a[0]
        elif mode == "manual":
            ok = confirmed
        else:
            ok = a[0] and confirmed
        done += ok
        groups.setdefault(group, []).append({
            "key": key, "label": label, "mode": mode, "hint": hint, "done": ok,
            "auto_ok": a[0] if a else None, "evidence": a[1] if a else None, "confirmed": confirmed,
            "note": c.note if c else None, "confirmed_by_id": c.confirmed_by_id if c else None,
            "confirmed_at": c.confirmed_at if c else None})
    return {"groups": [{"group": g, "items": v} for g, v in groups.items()], "done": done, "total": len(ITEMS),
            "statement": "Readiness checklist — what has been prepared in MedFlow. It is not a claim that a real hospital "
                         "has completed a pilot."}
