from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import case, func, select

from app.api.deps import DB, client_ip, require
from app.core.permissions import MANAGE_ALERTS, READ
from app.db.base import utcnow
from app.models import ACTIVE_ALERT_STATUSES, Alert, AlertSeverity, AlertStatus, AlertType, User
from app.schemas.common import Page
from app.schemas.inventory import AlertOut, EvaluateResult
from app.services import alerts as engine
from app.services import audit

router = APIRouter(prefix="/alerts", tags=["alerts"])
Reader = Annotated[User, Depends(require(READ))]
Manager = Annotated[User, Depends(require(MANAGE_ALERTS))]

_SEVERITY_ORDER = case(
    (Alert.severity == AlertSeverity.CRITICAL, 0),
    (Alert.severity == AlertSeverity.HIGH, 1),
    (Alert.severity == AlertSeverity.MEDIUM, 2),
    else_=3,
)


@router.get("", response_model=Page[AlertOut])
def list_alerts(
    user: Reader, db: DB,
    status_filter: Annotated[str, Query(alias="status", pattern="^(active|OPEN|ACKNOWLEDGED|RESOLVED|all)$")] = "active",
    alert_type: AlertType | None = None,
    severity: AlertSeverity | None = None,
    consumable_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    q = select(Alert).where(Alert.hospital_id == user.hospital_id)
    if status_filter == "active":
        q = q.where(Alert.status.in_(ACTIVE_ALERT_STATUSES))
    elif status_filter != "all":
        q = q.where(Alert.status == status_filter)
    if alert_type:
        q = q.where(Alert.alert_type == alert_type)
    if severity:
        q = q.where(Alert.severity == severity)
    if consumable_id:
        q = q.where(Alert.consumable_id == consumable_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(
        q.order_by(_SEVERITY_ORDER, Alert.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


def _get(db: DB, alert_id: int, hospital_id: int) -> Alert:
    a = db.get(Alert, alert_id)
    if not a or a.hospital_id != hospital_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    return a


@router.post("/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge(alert_id: int, request: Request, db: DB, user: Manager):
    a = _get(db, alert_id, user.hospital_id)
    if a.status != AlertStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Alert is already {a.status.lower()}")
    a.status, a.acknowledged_at, a.acknowledged_by_id = AlertStatus.ACKNOWLEDGED, utcnow(), user.id
    audit.record(db, user, "alert.acknowledge", "alert", a.id, {"title": a.title}, client_ip(request))
    db.commit()
    return a


@router.post("/{alert_id}/resolve", response_model=AlertOut)
def resolve(alert_id: int, request: Request, db: DB, user: Manager):
    """Manually close an alert. If the underlying condition still holds, the engine will raise a fresh one."""
    a = _get(db, alert_id, user.hospital_id)
    if a.status == AlertStatus.RESOLVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Alert is already resolved")
    a.status, a.resolved_at, a.resolved_by_id = AlertStatus.RESOLVED, utcnow(), user.id
    audit.record(db, user, "alert.resolve", "alert", a.id, {"title": a.title}, client_ip(request))
    db.commit()
    return a


@router.post("/evaluate", response_model=EvaluateResult)
def evaluate(request: Request, db: DB, user: Manager):
    stats = engine.evaluate(db, user.hospital_id)
    audit.record(db, user, "alert.evaluate", "hospital", user.hospital_id, stats, client_ip(request))
    db.commit()
    open_total = db.scalar(select(func.count()).where(Alert.hospital_id == user.hospital_id,
                                                       Alert.status.in_(ACTIVE_ALERT_STATUSES)))
    return EvaluateResult(**stats, open_total=open_total)
