from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import DB, require
from app.core.permissions import READ, READ_AUDIT
from app.db.base import utcnow
from app.models import (
    ACTIVE_ALERT_STATUSES,
    Alert,
    AuditLog,
    Consumable,
    Department,
    Hospital,
    MovementType,
    StockBatch,
    StockMovement,
    StockStatus,
    User,
)
from app.schemas.common import Page
from app.schemas.inventory import AuditOut, DashboardSummary, InventoryRow, NamedCount
from app.services import stock
from app.services.alerts import expiring_window

router = APIRouter(tags=["dashboard"])
Reader = Annotated[User, Depends(require(READ))]


@router.get("/dashboard/summary", response_model=DashboardSummary)
def summary(user: Reader, db: DB):
    hid = user.hospital_id
    rows = [r for r in stock.inventory_rows(db, hid) if r["is_active"]]
    status_counts = {s.value: 0 for s in StockStatus}
    for r in rows:
        status_counts[r["status"]] += 1

    levels = stock.stock_levels(db, hid)
    stock_value = sum(lv.value for lv in levels.values())
    expired_value = sum(lv.expired_value for lv in levels.values())

    hospital = db.get(Hospital, hid)
    start, end = expiring_window(hospital)
    expiring_value = db.scalar(
        select(func.coalesce(func.sum(StockBatch.quantity * StockBatch.unit_cost), 0))
        .join(Consumable, Consumable.id == StockBatch.consumable_id)
        .where(Consumable.hospital_id == hid, StockBatch.quantity > 0,
               StockBatch.expiry_date >= start, StockBatch.expiry_date <= end)
    )

    open_by_type = dict(db.execute(
        select(Alert.alert_type, func.count()).where(Alert.hospital_id == hid, Alert.status.in_(ACTIVE_ALERT_STATUSES))
        .group_by(Alert.alert_type)).all())
    open_by_sev = dict(db.execute(
        select(Alert.severity, func.count()).where(Alert.hospital_id == hid, Alert.status.in_(ACTIVE_ALERT_STATUSES))
        .group_by(Alert.severity)).all())

    since = utcnow() - timedelta(days=30)
    by_dept = db.execute(
        select(Department.name, func.sum(-StockMovement.quantity * Consumable.unit_cost))
        .select_from(StockMovement)
        .join(Department, Department.id == StockMovement.department_id)
        .join(Consumable, Consumable.id == StockMovement.consumable_id)
        .where(StockMovement.hospital_id == hid, StockMovement.created_at >= since,
               StockMovement.movement_type.in_([MovementType.ISSUE, MovementType.RETURN]))
        .group_by(Department.name).order_by(func.sum(-StockMovement.quantity * Consumable.unit_cost).desc())
    ).all()
    top = db.execute(
        select(Consumable.name, func.sum(-StockMovement.quantity * Consumable.unit_cost))
        .select_from(StockMovement)
        .join(Consumable, Consumable.id == StockMovement.consumable_id)
        .where(StockMovement.hospital_id == hid, StockMovement.created_at >= since,
               StockMovement.movement_type.in_([MovementType.ISSUE, MovementType.RETURN]))
        .group_by(Consumable.name).order_by(func.sum(-StockMovement.quantity * Consumable.unit_cost).desc()).limit(8)
    ).all()
    recent = db.scalars(
        select(StockMovement).where(StockMovement.hospital_id == hid)
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(10)
    ).all()
    order = {StockStatus.OUT_OF_STOCK: 0, StockStatus.LOW: 1}
    critical = sorted(
        [r for r in rows if r["status"] in (StockStatus.OUT_OF_STOCK, StockStatus.LOW)],
        key=lambda r: (order[r["status"]], r["usable_stock"] / max(r["reorder_level"], 1)),
    )[:8]

    return DashboardSummary(
        items_monitored=len(rows),
        status_counts=status_counts,
        stock_value=round(stock_value, 2),
        expired_value=round(expired_value, 2),
        expiring_soon_value=round(float(expiring_value or 0), 2),
        open_alerts=open_by_type,
        open_alerts_by_severity=open_by_sev,
        daily=stock.daily_series(db, hid, 30),
        consumption_by_department=[NamedCount(name=n, value=round(float(v or 0), 2)) for n, v in by_dept],
        top_consumed=[NamedCount(name=n, value=round(float(v or 0), 2)) for n, v in top],  # by consumption value
        recent_movements=recent,
        critical_items=[InventoryRow(**r) for r in critical],
    )


@router.get("/audit-logs", response_model=Page[AuditOut], tags=["audit"])
def audit_logs(
    db: DB,
    user: Annotated[User, Depends(require(READ_AUDIT))],
    action: str | None = None,
    entity_type: str | None = None,
    user_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    q = select(AuditLog).where(AuditLog.hospital_id == user.hospital_id)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                       .offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=items, total=total, page=page, page_size=page_size)
