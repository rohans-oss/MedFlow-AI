"""V10 — pilot management: create / update, lifecycle, participants, data sources, sandbox, issues, feedback, views.

Everything is scoped to the caller's ACTIVE hospital (V8): a pilot belongs to exactly one hospital; departments, users
(active members of that hospital), integration sources and recommendations referenced by a pilot must belong to it.
"""

from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.db.base import utcnow
from app.models import (
    Department,
    Hospital,
    HospitalMembership,
    IntegrationSource,
    Pilot,
    PilotDataSource,
    PilotFeedback,
    PilotIssue,
    PilotParticipant,
    ProcurementRecommendation,
    RecommendationView,
    ReconciliationIssue,
    Role,
    SyncRun,
    TenantStatus,
    User,
)

STATUSES = ("planned", "active", "paused", "completed", "cancelled")
TRANSITIONS = {"planned": {"active", "cancelled"}, "active": {"paused", "completed", "cancelled"},
               "paused": {"active", "completed", "cancelled"}, "completed": set(), "cancelled": set()}
ISSUE_CATEGORIES = ("inventory_mismatch", "unknown_item", "unknown_supplier", "incorrect_mapping", "duplicate_record",
                    "missing_data", "incorrect_quantity", "integration_failure", "prediction_problem",
                    "recommendation_problem", "user_workflow_problem", "other")
ISSUE_STATUSES = ("open", "investigating", "resolved", "ignored")
SEVERITIES = ("low", "medium", "high", "critical")
RATINGS = ("useful", "somewhat_useful", "not_useful")
REASONS = ("correct_recommendation", "wrong_quantity", "wrong_supplier", "data_problem", "timing_problem",
           "missing_context", "other")
FEEDBACK_TARGETS = ("recommendation", "forecast", "stockout_risk", "supplier", "integration", "workflow")
EXTERNAL_FACTORS = ("seasonal_demand", "department_change", "supplier_change", "data_source_change",
                    "inventory_policy_change", "staffing_change", "other")
SANDBOX_NAME = "Sandbox pilot — DEMO / SYNTHETIC DATA"


def _bad(msg: str, code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    return HTTPException(code, msg)


def check_periods(bs: date, be: date, ps: date, pe: date) -> None:
    if bs > be:
        raise _bad("The baseline period must start before it ends")
    if ps > pe:
        raise _bad("The pilot period must start before it ends")
    if be >= ps:
        raise _bad("The baseline period must end before the pilot period starts (the two are never mixed)")
    if (pe - ps).days > 730 or (be - bs).days > 730:
        raise _bad("Periods longer than two years are not supported")


def _member_ids(db: Session, hid: int, user_ids: list[int]) -> list[int]:
    if not user_ids:
        return []
    ok = set(db.scalars(select(HospitalMembership.user_id).where(
        HospitalMembership.hospital_id == hid, HospitalMembership.user_id.in_(user_ids),
        HospitalMembership.status == TenantStatus.ACTIVE)))
    missing = sorted(set(user_ids) - ok)
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found in this hospital")
    return sorted(ok)


def _owned_ids(db: Session, model, hid: int, ids: list[int], label: str) -> list[int]:
    if not ids:
        return []
    found = set(db.scalars(select(model.id).where(model.id.in_(ids), model.hospital_id == hid)))
    if found != set(ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} not found")
    return sorted(found)


def set_participants(db: Session, pilot: Pilot, department_ids: list[int] | None, user_ids: list[int] | None) -> None:
    hid = pilot.hospital_id
    if department_ids is not None:
        ids = _owned_ids(db, Department, hid, department_ids, "Department")
        db.execute(delete(PilotParticipant).where(PilotParticipant.pilot_id == pilot.id,
                                                  PilotParticipant.department_id.is_not(None)))
        db.add_all([PilotParticipant(hospital_id=hid, pilot_id=pilot.id, department_id=i) for i in ids])
    if user_ids is not None:
        ids = _member_ids(db, hid, user_ids)
        db.execute(delete(PilotParticipant).where(PilotParticipant.pilot_id == pilot.id, PilotParticipant.user_id.is_not(None)))
        db.add_all([PilotParticipant(hospital_id=hid, pilot_id=pilot.id, user_id=i) for i in ids])
    db.flush()


def set_sources(db: Session, pilot: Pilot, source_ids: list[int]) -> None:
    ids = _owned_ids(db, IntegrationSource, pilot.hospital_id, source_ids, "Integration source")
    db.execute(delete(PilotDataSource).where(PilotDataSource.pilot_id == pilot.id))
    db.add_all([PilotDataSource(hospital_id=pilot.hospital_id, pilot_id=pilot.id, source_id=i) for i in ids])
    db.flush()


def create(db: Session, user: User, body: dict) -> Pilot:
    hid = user.hospital_id
    h = db.get(Hospital, hid)
    check_periods(body["baseline_start"], body["baseline_end"], body["pilot_start"], body["pilot_end"])
    name = body["name"].strip()
    if db.scalar(select(Pilot.id).where(Pilot.hospital_id == hid, func.lower(Pilot.name) == name.lower())):
        raise _bad(f"A pilot named '{name}' already exists", status.HTTP_409_CONFLICT)
    owner = body.get("owner_id") or user.id
    _member_ids(db, hid, [owner])
    p = Pilot(hospital_id=hid, organization_id=h.organization_id, name=name, description=body.get("description"),
              status="planned", data_classification="synthetic" if h.is_demo else "observed", owner_id=owner,
              baseline_start=body["baseline_start"], baseline_end=body["baseline_end"], pilot_start=body["pilot_start"],
              pilot_end=body["pilot_end"], notes=body.get("notes"), external_factors=[], created_by_id=user.id)
    db.add(p)
    db.flush()
    set_participants(db, p, body.get("department_ids") or [], body.get("user_ids") or [])
    set_sources(db, p, body.get("source_ids") or [])
    return p


def update(db: Session, p: Pilot, user: User, changes: dict) -> dict:
    if p.status in ("completed", "cancelled") and set(changes) - {"notes", "description"}:
        raise _bad(f"A {p.status} pilot can only have its notes or description edited", status.HTTP_409_CONFLICT)
    dates = {k: changes.get(k, getattr(p, k)) for k in ("baseline_start", "baseline_end", "pilot_start", "pilot_end")}
    check_periods(dates["baseline_start"], dates["baseline_end"], dates["pilot_start"], dates["pilot_end"])
    if "name" in changes and changes["name"].strip().lower() != p.name.lower():
        if db.scalar(select(Pilot.id).where(Pilot.hospital_id == p.hospital_id,
                                            func.lower(Pilot.name) == changes["name"].strip().lower())):
            raise _bad("A pilot with that name already exists", status.HTTP_409_CONFLICT)
        p.name = changes["name"].strip()
    if "owner_id" in changes and changes["owner_id"]:
        _member_ids(db, p.hospital_id, [changes["owner_id"]])
        p.owner_id = changes["owner_id"]
    for k in ("description", "notes", *dates):
        if k in changes:
            setattr(p, k, changes[k])
    if "status" in changes and changes["status"] != p.status:
        transition(db, p, changes["status"])
    set_participants(db, p, changes.get("department_ids"), changes.get("user_ids"))
    if changes.get("source_ids") is not None:
        set_sources(db, p, changes["source_ids"])
    db.flush()
    return changes


def transition(db: Session, p: Pilot, new: str) -> None:
    if new not in STATUSES:
        raise _bad(f"status must be one of {', '.join(STATUSES)}")
    if new not in TRANSITIONS[p.status]:
        raise _bad(f"A {p.status} pilot cannot become {new}", status.HTTP_409_CONFLICT)
    if new == "active":
        other = db.scalar(select(Pilot.id).where(Pilot.hospital_id == p.hospital_id, Pilot.status == "active",
                                                 Pilot.id != p.id))
        if other:
            raise _bad("Another pilot is already active in this hospital — pause or complete it first", status.HTTP_409_CONFLICT)
    if new in ("completed", "cancelled"):
        p.actual_end = min(business_today(), p.pilot_end) if new == "completed" else business_today()
    p.status = new


def add_external_factor(p: Pilot, user: User, factor: str, note: str, period: str) -> dict:
    if factor not in EXTERNAL_FACTORS:
        raise _bad(f"factor must be one of {', '.join(EXTERNAL_FACTORS)}")
    if period not in ("baseline", "pilot", "both"):
        raise _bad("period must be baseline, pilot or both")
    entry = {"factor": factor, "note": note.strip(), "period": period, "recorded_by": user.full_name,
             "recorded_by_id": user.id, "at": utcnow().isoformat()}
    p.external_factors = [*(p.external_factors or []), entry]
    return entry


def active_pilot(db: Session, hid: int) -> Pilot | None:
    return db.scalar(select(Pilot).where(Pilot.hospital_id == hid, Pilot.status == "active").order_by(Pilot.id.desc()))


def sandbox(db: Session, user: User) -> Pilot:
    """A demo pilot in a DEMO hospital: last 60–31 days = baseline, a 60-day pilot that started 30 days ago."""
    h = db.get(Hospital, user.hospital_id)
    if not h.is_demo:
        raise _bad("The pilot sandbox is only available in demo hospitals (synthetic data)", status.HTTP_409_CONFLICT)
    today = business_today()
    n = db.scalar(select(func.count(Pilot.id)).where(Pilot.hospital_id == h.id, Pilot.name.like(f"{SANDBOX_NAME}%"))) or 0
    name = SANDBOX_NAME if n == 0 else f"{SANDBOX_NAME} ({n + 1})"
    depts = list(db.scalars(select(Department.id).where(Department.hospital_id == h.id, Department.is_active.is_(True))))
    users = list(db.scalars(select(HospitalMembership.user_id).where(
        HospitalMembership.hospital_id == h.id, HospitalMembership.status == TenantStatus.ACTIVE,
        HospitalMembership.role.in_([Role.ADMIN, Role.PROCUREMENT_MANAGER, Role.INVENTORY_MANAGER, Role.DEPARTMENT_MANAGER]))))
    sources = list(db.scalars(select(IntegrationSource.id).where(IntegrationSource.hospital_id == h.id)))
    p = create(db, user, {
        "name": name, "baseline_start": today - timedelta(days=60), "baseline_end": today - timedelta(days=31),
        "pilot_start": today - timedelta(days=30), "pilot_end": today + timedelta(days=29),
        "description": "Pilot sandbox: demonstrates the V10 workflow on the demo hospital's SYNTHETIC data. "
                       "NOT REAL HOSPITAL DATA — no result from this pilot is an observed outcome.",
        "notes": "Created from the sandbox. Sync the linked V9 sources (the reference ERP is simulated), review "
                 "reconciliation, decide recommendations, give feedback and log issues, then generate the report.",
        "department_ids": depts, "user_ids": users, "source_ids": sources})
    if active_pilot(db, h.id) is None:
        transition(db, p, "active")
    return p


# ---------------------------------------------------------------- issues


def _validate_ref(db: Session, hid: int, source: str, ref: str | None) -> str | None:
    if source in ("manual", "feedback") or not ref:
        return ref
    kind, _, raw = ref.partition(":")
    model = {"sync_run": SyncRun, "reconciliation": ReconciliationIssue}.get(kind)
    if model is None or not raw.isdigit():
        raise _bad("source_ref must look like sync_run:<id> or reconciliation:<id>")
    obj = db.get(model, int(raw))
    if obj is None or obj.hospital_id != hid:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Referenced record not found")
    return ref


def create_issue(db: Session, p: Pilot, user: User, body: dict) -> PilotIssue:
    if body["category"] not in ISSUE_CATEGORIES:
        raise _bad(f"category must be one of {', '.join(ISSUE_CATEGORIES)}")
    if body.get("severity", "medium") not in SEVERITIES:
        raise _bad(f"severity must be one of {', '.join(SEVERITIES)}")
    source = body.get("source") or "manual"
    if source not in ("manual", "sync_run", "reconciliation", "feedback"):
        raise _bad("source must be manual, sync_run, reconciliation or feedback")
    ref = _validate_ref(db, p.hospital_id, source, body.get("source_ref"))
    assigned = body.get("assigned_to_id")
    if assigned:
        _member_ids(db, p.hospital_id, [assigned])
    i = PilotIssue(hospital_id=p.hospital_id, pilot_id=p.id, category=body["category"], severity=body.get("severity") or "medium",
                   title=body["title"].strip(), description=body.get("description"), source=source, source_ref=ref,
                   impact=body.get("impact"), status="open", assigned_to_id=assigned, created_by_id=user.id)
    db.add(i)
    db.flush()
    return i


def update_issue(db: Session, i: PilotIssue, user: User, changes: dict) -> None:
    if "status" in changes:
        new = changes["status"]
        if new not in ISSUE_STATUSES:
            raise _bad(f"status must be one of {', '.join(ISSUE_STATUSES)}")
        if new in ("resolved", "ignored"):
            text = (changes.get("resolution") or i.resolution or "").strip()
            if len(text) < 3:
                raise _bad("A resolution note is required to resolve or ignore an issue")
            i.resolution, i.resolved_by_id, i.resolved_at = text, user.id, utcnow()
        elif i.status in ("resolved", "ignored"):  # reopened
            i.resolved_by_id = i.resolved_at = None
        i.status = new
    if "severity" in changes:
        if changes["severity"] not in SEVERITIES:
            raise _bad(f"severity must be one of {', '.join(SEVERITIES)}")
        i.severity = changes["severity"]
    if "assigned_to_id" in changes:
        if changes["assigned_to_id"]:
            _member_ids(db, i.hospital_id, [changes["assigned_to_id"]])
        i.assigned_to_id = changes["assigned_to_id"]
    for k in ("impact", "description"):
        if k in changes:
            setattr(i, k, changes[k])
    if "resolution" in changes and "status" not in changes:
        i.resolution = changes["resolution"]
    db.flush()


# ---------------------------------------------------------------- feedback / views


def add_feedback(db: Session, p: Pilot, user: User, body: dict) -> PilotFeedback:
    if p.status not in ("active", "paused"):
        raise _bad("Feedback is collected while the pilot is active or paused", status.HTTP_409_CONFLICT)
    if body["rating"] not in RATINGS:
        raise _bad(f"rating must be one of {', '.join(RATINGS)}")
    if body["target_type"] not in FEEDBACK_TARGETS:
        raise _bad(f"target_type must be one of {', '.join(FEEDBACK_TARGETS)}")
    reasons = list(dict.fromkeys(body.get("reasons") or []))
    bad = [r for r in reasons if r not in REASONS]
    if bad:
        raise _bad(f"Unknown reason(s): {', '.join(bad)}")
    rec_id = body.get("recommendation_id")
    if body["target_type"] == "recommendation":
        if not rec_id:
            raise _bad("recommendation_id is required for recommendation feedback")
        rec = db.get(ProcurementRecommendation, rec_id)
        if rec is None or rec.hospital_id != p.hospital_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Recommendation not found")
    elif rec_id:
        raise _bad("recommendation_id is only for recommendation feedback")
    f = PilotFeedback(hospital_id=p.hospital_id, pilot_id=p.id, user_id=user.id, target_type=body["target_type"],
                      recommendation_id=rec_id, rating=body["rating"], reasons=reasons,
                      comment=(body.get("comment") or "").strip() or None)
    db.add(f)
    db.flush()
    return f


def record_view(db: Session, user: User, rec: ProcurementRecommendation) -> RecommendationView:
    v = RecommendationView(hospital_id=rec.hospital_id, recommendation_id=rec.id, user_id=user.id)
    db.add(v)
    db.flush()
    return v
