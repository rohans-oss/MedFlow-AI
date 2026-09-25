"""V10 — real hospital pilot & business validation API (hospital-scoped; the hospital is always the active one).

GET    /pilots                               list                                  (read)
POST   /pilots                               create                                (pilots:manage)
POST   /pilots/sandbox                       demo pilot on synthetic data          (pilots:manage, demo hospitals)
GET    /pilots/active                        the active pilot (or null)            (read)
GET    /pilots/{id} · PATCH                  detail · update / lifecycle            (read · pilots:manage)
POST   /pilots/{id}/external-factors         record an external factor             (pilots:manage)
GET    /pilots/{id}/dashboard                pilot-period metrics + issues + readiness summary
GET    /pilots/{id}/metrics?period=          metrics of one period · /baseline = the baseline period
GET    /pilots/{id}/comparison               descriptive baseline vs pilot comparison
GET    /pilots/{id}/decisions                recommendation decision funnel (pilot period)
GET/POST /pilots/{id}/issues · PATCH /pilot-issues/{id}                          (read · pilots:contribute)
GET/POST /pilots/{id}/feedback                                                    (read · pilots:contribute)
GET    /pilots/{id}/readiness · PUT /pilots/{id}/readiness/{item}                 (read · pilots:manage)
GET    /pilots/{id}/report[?format=markdown] · POST/GET /pilots/{id}/report/snapshots · GET /pilot-reports/{id}
POST   /pilots/{id}/sync                     sync the pilot's REST sources via V9   (integrations:run)
POST   /pilot-tracking/recommendations/{id}/view   record that a recommendation was opened (read)
GET    /organizations/{id}/pilots            pilots of the organization's hospitals (organization admin)
"""

from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, client_ip, get_owned, require
from app.core.permissions import INTEGRATIONS_RUN, PILOTS_CONTRIBUTE, PILOTS_MANAGE, READ
from app.db.base import utcnow
from app.models import (
    Department,
    Hospital,
    IntegrationSource,
    Pilot,
    PilotDataSource,
    PilotFeedback,
    PilotIssue,
    PilotParticipant,
    PilotReadiness,
    PilotReportSnapshot,
    ProcurementRecommendation,
    User,
)
from app.pilots import comparison, metrics, readiness, report, service
from app.schemas.pilots import (
    Comparison,
    Dashboard,
    DecisionRow,
    FactorIn,
    FeedbackIn,
    FeedbackOut,
    FeedbackSummary,
    IssueCreate,
    IssueOut,
    IssueUpdate,
    OrgPilotRow,
    PeriodMetrics,
    PilotCreate,
    PilotOut,
    PilotUpdate,
    Readiness,
    ReadinessConfirm,
    Ref,
    ReportSnapshotOut,
)
from app.services import audit, tenancy

router = APIRouter(tags=["pilots"])
Reader = Annotated[User, Depends(require(READ))]
Manager = Annotated[User, Depends(require(PILOTS_MANAGE))]
Contributor = Annotated[User, Depends(require(PILOTS_CONTRIBUTE))]
Syncer = Annotated[User, Depends(require(INTEGRATIONS_RUN))]


def _ref(db: Session, model, oid: int | None, attr: str = "full_name") -> Ref | None:
    o = db.get(model, oid) if oid else None
    return Ref(id=o.id, name=getattr(o, attr)) if o else None


def _pilot(db: Session, pilot_id: int, user: User) -> Pilot:
    return get_owned(db, Pilot, pilot_id, user.hospital_id, "Pilot")


def pilot_out(db: Session, p: Pilot) -> PilotOut:
    depts = db.execute(select(Department.id, Department.name).join(PilotParticipant, PilotParticipant.department_id == Department.id)
                       .where(PilotParticipant.pilot_id == p.id).order_by(Department.name)).all()
    users = db.execute(select(User.id, User.full_name).join(PilotParticipant, PilotParticipant.user_id == User.id)
                       .where(PilotParticipant.pilot_id == p.id).order_by(User.full_name)).all()
    srcs = db.execute(select(IntegrationSource.id, IntegrationSource.name)
                      .join(PilotDataSource, PilotDataSource.source_id == IntegrationSource.id)
                      .where(PilotDataSource.pilot_id == p.id).order_by(IntegrationSource.name)).all()
    open_issues = db.scalar(select(func.count(PilotIssue.id)).where(PilotIssue.pilot_id == p.id,
                                                                    PilotIssue.status.in_(["open", "investigating"]))) or 0
    return PilotOut(id=p.id, hospital_id=p.hospital_id, organization_id=p.organization_id,
                    hospital_name=db.get(Hospital, p.hospital_id).name, name=p.name, description=p.description, status=p.status,
                    data_classification=p.data_classification, owner=_ref(db, User, p.owner_id),
                    baseline_start=p.baseline_start, baseline_end=p.baseline_end, pilot_start=p.pilot_start,
                    pilot_end=p.pilot_end, actual_end=p.actual_end, notes=p.notes, external_factors=p.external_factors or [],
                    departments=[Ref(id=i, name=n) for i, n in depts], users=[Ref(id=i, name=n) for i, n in users],
                    data_sources=[Ref(id=i, name=n) for i, n in srcs], open_issues=open_issues, created_at=p.created_at,
                    updated_at=p.updated_at)


def _label(p: Pilot) -> str:
    return "Synthetic/demo result — DEMO / SYNTHETIC DATA, NOT REAL HOSPITAL DATA" if p.data_classification == "synthetic" \
        else "Observed pilot result"


# ---------------------------------------------------------------- pilots


@router.get("/pilots", response_model=list[PilotOut])
def list_pilots(db: DB, user: Reader):
    rows = db.scalars(select(Pilot).where(Pilot.hospital_id == user.hospital_id).order_by(Pilot.pilot_start.desc(), Pilot.id))
    return [pilot_out(db, p) for p in rows]


@router.post("/pilots", response_model=PilotOut, status_code=status.HTTP_201_CREATED)
def create_pilot(body: PilotCreate, request: Request, db: DB, user: Manager):
    p = service.create(db, user, body.model_dump())
    audit.record(db, user, "pilot.create", "pilot", p.id, body.model_dump(mode="json"), client_ip(request))
    db.commit()
    return pilot_out(db, p)


@router.post("/pilots/sandbox", response_model=PilotOut, status_code=status.HTTP_201_CREATED)
def create_sandbox(request: Request, db: DB, user: Manager):
    p = service.sandbox(db, user)
    audit.record(db, user, "pilot.sandbox", "pilot", p.id, {"name": p.name, "classification": p.data_classification},
                 client_ip(request))
    db.commit()
    return pilot_out(db, p)


@router.get("/pilots/active", response_model=PilotOut | None)
def get_active(db: DB, user: Reader):
    p = service.active_pilot(db, user.hospital_id)
    return pilot_out(db, p) if p else None


@router.get("/pilots/{pilot_id}", response_model=PilotOut)
def get_pilot(pilot_id: int, db: DB, user: Reader):
    return pilot_out(db, _pilot(db, pilot_id, user))


@router.patch("/pilots/{pilot_id}", response_model=PilotOut)
def update_pilot(pilot_id: int, body: PilotUpdate, request: Request, db: DB, user: Manager):
    p = _pilot(db, pilot_id, user)
    before = p.status
    changes = body.model_dump(exclude_unset=True)
    service.update(db, p, user, changes)
    audit.record(db, user, "pilot.status" if changes.get("status") and changes["status"] != before else "pilot.update", "pilot",
                 p.id, {**body.model_dump(mode="json", exclude_unset=True), "status_before": before}, client_ip(request))
    db.commit()
    return pilot_out(db, p)


@router.post("/pilots/{pilot_id}/external-factors", response_model=PilotOut)
def add_factor(pilot_id: int, body: FactorIn, request: Request, db: DB, user: Manager):
    p = _pilot(db, pilot_id, user)
    entry = service.add_external_factor(p, user, body.factor, body.note, body.period)
    audit.record(db, user, "pilot.external_factor", "pilot", p.id, entry, client_ip(request))
    db.commit()
    return pilot_out(db, p)


# ---------------------------------------------------------------- metrics


@router.get("/pilots/{pilot_id}/metrics", response_model=PeriodMetrics)
def period_metrics(pilot_id: int, db: DB, user: Reader,
                   period: Annotated[str, Query(pattern="^(baseline|pilot)$")] = "pilot"):
    p = _pilot(db, pilot_id, user)
    return metrics.compute(db, p, metrics.periods(p)[period])


@router.get("/pilots/{pilot_id}/baseline", response_model=PeriodMetrics)
def baseline_metrics(pilot_id: int, db: DB, user: Reader):
    p = _pilot(db, pilot_id, user)
    return metrics.compute(db, p, metrics.periods(p)["baseline"])


@router.get("/pilots/{pilot_id}/dashboard", response_model=Dashboard)
def dashboard(pilot_id: int, db: DB, user: Reader):
    p = _pilot(db, pilot_id, user)
    rd = readiness.evaluate(db, p)
    return Dashboard(pilot=pilot_out(db, p), classification_label=_label(p),
                     period=metrics.compute(db, p, metrics.periods(p)["pilot"]), issues=metrics.issue_counts(db, p),
                     readiness={"done": rd["done"], "total": rd["total"]})


@router.get("/pilots/{pilot_id}/comparison", response_model=Comparison)
def compare(pilot_id: int, db: DB, user: Reader):
    p = _pilot(db, pilot_id, user)
    per = metrics.periods(p)
    base, pil = metrics.compute(db, p, per["baseline"]), metrics.compute(db, p, per["pilot"])
    notes = [n for n in (comparison.period_note(per["baseline"]), comparison.period_note(per["pilot"])) if n]
    if per["baseline"].days != per["pilot"].days:
        notes.append(f"The periods differ in length ({per['baseline'].days} vs {per['pilot'].days} days): accumulating "
                     "counts are compared per 30 days.")
    return Comparison(label=comparison.LABEL, causality_statement=comparison.CAUSALITY, classification_label=_label(p),
                      baseline=base["period"], pilot=pil["period"], rows=comparison.compare(base, pil, per["baseline"], per["pilot"]),
                      external_factors=p.external_factors or [], notes=notes)


@router.get("/pilots/{pilot_id}/decisions", response_model=list[DecisionRow])
def decisions(pilot_id: int, db: DB, user: Reader, period: Annotated[str, Query(pattern="^(baseline|pilot)$")] = "pilot"):
    p = _pilot(db, pilot_id, user)
    rows = metrics.decision_rows(db, p.hospital_id, metrics.periods(p)[period])
    return [DecisionRow(**{k: v for k, v in r.items() if k != "decided_by_id"}, decided_by=_ref(db, User, r["decided_by_id"]))
            for r in rows]


@router.post("/pilots/{pilot_id}/sync")
def sync_sources(pilot_id: int, request: Request, db: DB, user: Syncer):
    """Pull every linked, enabled REST source through the V9 engine (no second integration engine)."""
    from app.integrations import connectors
    from app.integrations.engine import SyncError

    p = _pilot(db, pilot_id, user)
    src_ids = metrics.pilot_source_ids(db, p)
    out = []
    for s in db.scalars(select(IntegrationSource).where(IntegrationSource.id.in_(src_ids), IntegrationSource.connector == "rest_pull",
                                                         IntegrationSource.enabled.is_(True))) if src_ids else []:
        try:
            runs = connectors.sync_source(db, s, user, "manual")
            out.append({"source": s.name, "runs": [{"id": r.id, "entity": r.entity, "status": r.status,
                                                    "received": r.records_received, "rejected": r.records_rejected} for r in runs]})
        except SyncError as e:
            db.rollback()
            out.append({"source": s.name, "error": str(e), "runs": []})
    audit.record(db, user, "pilot.sync", "pilot", p.id, {"sources": [o["source"] for o in out]}, client_ip(request))
    db.commit()
    if not out:
        raise HTTPException(status.HTTP_409_CONFLICT, "This pilot has no enabled REST (pull) data source to sync — push and "
                                                      "upload sources send data themselves")
    return {"results": out}


# ---------------------------------------------------------------- issues


def issue_out(db: Session, i: PilotIssue) -> IssueOut:
    return IssueOut(id=i.id, pilot_id=i.pilot_id, category=i.category, severity=i.severity, title=i.title,
                    description=i.description, source=i.source, source_ref=i.source_ref, impact=i.impact, status=i.status,
                    assigned_to=_ref(db, User, i.assigned_to_id), created_by=_ref(db, User, i.created_by_id),
                    resolution=i.resolution, resolved_by=_ref(db, User, i.resolved_by_id), resolved_at=i.resolved_at,
                    created_at=i.created_at, updated_at=i.updated_at)


@router.get("/pilots/{pilot_id}/issues", response_model=list[IssueOut])
def list_issues(pilot_id: int, db: DB, user: Reader, status_: Annotated[str | None, Query(alias="status")] = None):
    p = _pilot(db, pilot_id, user)
    q = select(PilotIssue).where(PilotIssue.pilot_id == p.id)
    if status_:
        q = q.where(PilotIssue.status == status_)
    return [issue_out(db, i) for i in db.scalars(q.order_by(PilotIssue.created_at.desc()))]


@router.post("/pilots/{pilot_id}/issues", response_model=IssueOut, status_code=status.HTTP_201_CREATED)
def create_issue(pilot_id: int, body: IssueCreate, request: Request, db: DB, user: Contributor):
    p = _pilot(db, pilot_id, user)
    i = service.create_issue(db, p, user, body.model_dump())
    audit.record(db, user, "pilot.issue.create", "pilot_issue", i.id, {"pilot_id": p.id, **body.model_dump()}, client_ip(request))
    db.commit()
    return issue_out(db, i)


@router.patch("/pilot-issues/{pilot_issue_id}", response_model=IssueOut)
def update_issue(pilot_issue_id: int, body: IssueUpdate, request: Request, db: DB, user: Contributor):
    i = get_owned(db, PilotIssue, pilot_issue_id, user.hospital_id, "Issue")
    before = i.status
    changes = body.model_dump(exclude_unset=True)
    service.update_issue(db, i, user, changes)
    audit.record(db, user, "pilot.issue.update", "pilot_issue", i.id, {"status_before": before, **changes}, client_ip(request))
    db.commit()
    return issue_out(db, i)


# ---------------------------------------------------------------- feedback


def feedback_out(db: Session, f: PilotFeedback) -> FeedbackOut:
    return FeedbackOut(id=f.id, pilot_id=f.pilot_id, user=_ref(db, User, f.user_id), target_type=f.target_type,
                       recommendation_id=f.recommendation_id, rating=f.rating, reasons=f.reasons or [], comment=f.comment,
                       created_at=f.created_at)


@router.get("/pilots/{pilot_id}/feedback", response_model=FeedbackSummary)
def list_feedback(pilot_id: int, db: DB, user: Reader):
    p = _pilot(db, pilot_id, user)
    rows = list(db.scalars(select(PilotFeedback).where(PilotFeedback.pilot_id == p.id).order_by(PilotFeedback.created_at.desc())))
    return FeedbackSummary(items=[feedback_out(db, f) for f in rows], by_rating=dict(Counter(f.rating for f in rows)),
                           by_reason=dict(Counter(r for f in rows for r in (f.reasons or []))),
                           by_target=dict(Counter(f.target_type for f in rows)))


@router.post("/pilots/{pilot_id}/feedback", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED)
def add_feedback(pilot_id: int, body: FeedbackIn, request: Request, db: DB, user: Contributor):
    p = _pilot(db, pilot_id, user)
    f = service.add_feedback(db, p, user, body.model_dump())
    audit.record(db, user, "pilot.feedback", "pilot_feedback", f.id, {"pilot_id": p.id, **body.model_dump()}, client_ip(request))
    db.commit()
    return feedback_out(db, f)


@router.post("/pilot-tracking/recommendations/{rec_id}/view", status_code=status.HTTP_201_CREATED)
def record_view(rec_id: int, db: DB, user: Reader):
    rec = get_owned(db, ProcurementRecommendation, rec_id, user.hospital_id, "Recommendation")
    v = service.record_view(db, user, rec)
    db.commit()
    return {"id": v.id, "recommendation_id": rec.id}


# ---------------------------------------------------------------- readiness


@router.get("/pilots/{pilot_id}/readiness", response_model=Readiness)
def get_readiness(pilot_id: int, db: DB, user: Reader):
    return readiness.evaluate(db, _pilot(db, pilot_id, user))


@router.put("/pilots/{pilot_id}/readiness/{item_key}", response_model=Readiness)
def confirm(pilot_id: int, item_key: str, body: ReadinessConfirm, request: Request, db: DB, user: Manager):
    p = _pilot(db, pilot_id, user)
    mode = next((m for k, _g, _l, m, _h in readiness.ITEMS if k == item_key), None)
    if mode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown checklist item")
    if mode == "auto":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This item is checked automatically from MedFlow's data")
    r = db.scalar(select(PilotReadiness).where(PilotReadiness.pilot_id == p.id, PilotReadiness.item_key == item_key))
    if r is None:
        r = PilotReadiness(hospital_id=p.hospital_id, pilot_id=p.id, item_key=item_key)
        db.add(r)
    r.confirmed, r.note = body.confirmed, body.note
    r.confirmed_by_id, r.confirmed_at = (user.id, utcnow()) if body.confirmed else (None, None)
    audit.record(db, user, "pilot.readiness", "pilot", p.id, {"item": item_key, **body.model_dump()}, client_ip(request))
    db.commit()
    return readiness.evaluate(db, p)


# ---------------------------------------------------------------- report


@router.get("/pilots/{pilot_id}/report")
def get_report(pilot_id: int, db: DB, user: Reader, format: Annotated[str, Query(pattern="^(json|markdown)$")] = "json"):  # noqa: A002
    p = _pilot(db, pilot_id, user)
    r = report.build(db, p, user)
    if format == "markdown":
        return PlainTextResponse(report.markdown(r), media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="pilot-{p.id}-report.md"'})
    return {**r, "markdown": report.markdown(r)}


@router.post("/pilots/{pilot_id}/report/snapshots", response_model=ReportSnapshotOut, status_code=status.HTTP_201_CREATED)
def save_snapshot(pilot_id: int, request: Request, db: DB, user: Manager):
    from fastapi.encoders import jsonable_encoder

    p = _pilot(db, pilot_id, user)
    r = report.build(db, p, user)
    s = PilotReportSnapshot(hospital_id=p.hospital_id, pilot_id=p.id, generated_by_id=user.id,
                            data_classification=p.data_classification, report=jsonable_encoder(r), markdown=report.markdown(r))
    db.add(s)
    db.flush()
    audit.record(db, user, "pilot.report.snapshot", "pilot_report_snapshot", s.id, {"pilot_id": p.id}, client_ip(request))
    db.commit()
    return ReportSnapshotOut(id=s.id, pilot_id=p.id, generated_at=s.generated_at, generated_by=_ref(db, User, user.id),
                             data_classification=s.data_classification)


@router.get("/pilots/{pilot_id}/report/snapshots", response_model=list[ReportSnapshotOut])
def list_snapshots(pilot_id: int, db: DB, user: Reader):
    p = _pilot(db, pilot_id, user)
    rows = db.scalars(select(PilotReportSnapshot).where(PilotReportSnapshot.pilot_id == p.id)
                      .order_by(PilotReportSnapshot.generated_at.desc()))
    return [ReportSnapshotOut(id=s.id, pilot_id=s.pilot_id, generated_at=s.generated_at,
                              generated_by=_ref(db, User, s.generated_by_id), data_classification=s.data_classification)
            for s in rows]


@router.get("/pilot-reports/{snapshot_id}", response_class=PlainTextResponse)
def get_snapshot(snapshot_id: int, db: DB, user: Reader):
    s = get_owned(db, PilotReportSnapshot, snapshot_id, user.hospital_id, "Report")
    return PlainTextResponse(s.markdown, media_type="text/markdown; charset=utf-8")


# ---------------------------------------------------------------- organization view (status only, no operational data)


@router.get("/organizations/{org_id}/pilots", response_model=list[OrgPilotRow])
def organization_pilots(org_id: int, user: CurrentUser, db: DB):
    org = tenancy.require_org_admin(db, user, org_id)
    if not tenancy.is_org_admin(db, user, org.id):  # platform admins manage organizations but don't read their data
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    rows = db.execute(select(Pilot, Hospital.name).join(Hospital, Hospital.id == Pilot.hospital_id)
                      .where(Hospital.organization_id == org.id).order_by(Hospital.name, Pilot.pilot_start.desc())).all()
    out = []
    for p, hname in rows:
        n = db.scalar(select(func.count(PilotIssue.id)).where(PilotIssue.pilot_id == p.id,
                                                              PilotIssue.status.in_(["open", "investigating"]))) or 0
        out.append(OrgPilotRow(id=p.id, hospital_id=p.hospital_id, hospital=hname, name=p.name, status=p.status,
                               data_classification=p.data_classification, baseline_start=p.baseline_start,
                               baseline_end=p.baseline_end, pilot_start=p.pilot_start, pilot_end=p.pilot_end, open_issues=n))
    return out
