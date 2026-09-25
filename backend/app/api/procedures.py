"""V2B — procedure types, procedure schedule and procedure → item mappings.

Stores procedure COUNTS per department and day. No patient data. Mapping quantities in the demo are
synthetic "kit" assumptions and can be replaced with each hospital's own values.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DB, client_ip, get_owned, require
from app.core.permissions import MANAGE_PROCEDURES, MANAGE_PROCEDURES_OWN_DEPT, READ, permissions_for
from app.core.security import business_today
from app.models import (
    Consumable,
    Department,
    ProcedureItemMapping,
    ProcedureSchedule,
    ProcedureStatus,
    ProcedureType,
    User,
)
from app.schemas.common import Page
from app.schemas.procedures import (
    CancelIn,
    MappingIn,
    MappingOut,
    MappingUpdate,
    ProcedureSummary,
    ProcedureTypeIn,
    ProcedureTypeOut,
    ProcedureTypeUpdate,
    ScheduleIn,
    ScheduleOut,
    ScheduleUpdate,
    UpcomingByType,
)
from app.services import audit

router = APIRouter(prefix="/procedures", tags=["procedures"])
Reader = Annotated[User, Depends(require(READ))]
Manager = Annotated[User, Depends(require(MANAGE_PROCEDURES, MANAGE_PROCEDURES_OWN_DEPT))]

MAX_DATE_RANGE_DAYS = 366


def _check_scope(user: User, department_id: int) -> None:
    """Admins manage every department; department managers only their own."""
    if MANAGE_PROCEDURES not in permissions_for(user.role) and user.department_id != department_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only manage procedures for your own department")


def _active_department(db: Session, dept_id: int, hospital_id: int) -> Department:
    dept = get_owned(db, Department, dept_id, hospital_id, "Department")
    if not dept.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Department is inactive")
    return dept


def _unprocessable(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, msg)


# ============================================================ procedure types


def _type_out(db: Session, t: ProcedureType) -> ProcedureTypeOut:
    today = business_today()
    mapping_count = db.scalar(select(func.count(ProcedureItemMapping.id)).where(
        ProcedureItemMapping.procedure_type_id == t.id, ProcedureItemMapping.is_active.is_(True))) or 0
    upcoming = db.scalar(select(func.coalesce(func.sum(ProcedureSchedule.count), 0)).where(
        ProcedureSchedule.procedure_type_id == t.id, ProcedureSchedule.status != ProcedureStatus.CANCELLED,
        ProcedureSchedule.scheduled_date >= today, ProcedureSchedule.scheduled_date < today + timedelta(days=30))) or 0
    out = ProcedureTypeOut.model_validate(t)
    out.mapping_count, out.upcoming_count = int(mapping_count), int(upcoming)
    return out


@router.get("/types", response_model=list[ProcedureTypeOut])
def list_types(user: Reader, db: DB, include_inactive: bool = False, department_id: int | None = None):
    q = select(ProcedureType).where(ProcedureType.hospital_id == user.hospital_id)
    if not include_inactive:
        q = q.where(ProcedureType.is_active.is_(True))
    if department_id:
        q = q.where(ProcedureType.department_id == department_id)
    return [_type_out(db, t) for t in db.scalars(q.order_by(ProcedureType.name))]


@router.post("/types", response_model=ProcedureTypeOut, status_code=status.HTTP_201_CREATED)
def create_type(body: ProcedureTypeIn, request: Request, db: DB, user: Manager):
    _active_department(db, body.department_id, user.hospital_id)
    _check_scope(user, body.department_id)
    if db.scalar(select(ProcedureType.id).where(ProcedureType.hospital_id == user.hospital_id,
                                                ProcedureType.code == body.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Procedure code {body.code} already exists")
    t = ProcedureType(hospital_id=user.hospital_id, **body.model_dump())
    db.add(t)
    db.flush()
    audit.record(db, user, "procedure_type.create", "procedure_type", t.id, body.model_dump(), client_ip(request))
    db.commit()
    db.refresh(t)
    return _type_out(db, t)


@router.patch("/types/{type_id}", response_model=ProcedureTypeOut)
def update_type(type_id: int, body: ProcedureTypeUpdate, request: Request, db: DB, user: Manager):
    t = get_owned(db, ProcedureType, type_id, user.hospital_id, "Procedure type")
    _check_scope(user, t.department_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("department_id") is not None:
        _active_department(db, data["department_id"], user.hospital_id)
        _check_scope(user, data["department_id"])
    elif "department_id" in data:
        data.pop("department_id")
    changes = audit.diff(t, data)
    if changes:
        audit.record(db, user, "procedure_type.update", "procedure_type", t.id, changes, client_ip(request))
    db.commit()
    db.refresh(t)
    return _type_out(db, t)


# ============================================================ schedule


def _validate_slot(db: Session, hospital_id: int, type_id: int, dept_id: int, day, status_: str, exclude_id: int | None):
    today = business_today()
    if abs((day - today).days) > MAX_DATE_RANGE_DAYS:
        raise _unprocessable("Date must be within one year of today")
    if status_ == ProcedureStatus.SCHEDULED and day < today:
        raise _unprocessable("A past date cannot be SCHEDULED — record it as COMPLETED")
    if status_ == ProcedureStatus.COMPLETED and day > today:
        raise _unprocessable("A future date cannot be COMPLETED")
    q = select(ProcedureSchedule.id).where(
        ProcedureSchedule.hospital_id == hospital_id, ProcedureSchedule.procedure_type_id == type_id,
        ProcedureSchedule.department_id == dept_id, ProcedureSchedule.scheduled_date == day,
        ProcedureSchedule.status != ProcedureStatus.CANCELLED,
    )
    if exclude_id:
        q = q.where(ProcedureSchedule.id != exclude_id)
    dup = db.scalar(q)
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This procedure is already scheduled for that department and date (entry #{dup}); edit its count instead")


@router.get("/schedule", response_model=Page[ScheduleOut])
def list_schedule(
    user: Reader, db: DB,
    date_from: date | None = None,
    date_to: date | None = None,
    department_id: int | None = None,
    procedure_type_id: int | None = None,
    status_filter: Annotated[str, Query(alias="status", pattern="^(active|SCHEDULED|COMPLETED|CANCELLED|all)$")] = "all",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    q = select(ProcedureSchedule).where(ProcedureSchedule.hospital_id == user.hospital_id)
    if date_from:
        q = q.where(ProcedureSchedule.scheduled_date >= date_from)
    if date_to:
        q = q.where(ProcedureSchedule.scheduled_date <= date_to)
    if department_id:
        q = q.where(ProcedureSchedule.department_id == department_id)
    if procedure_type_id:
        q = q.where(ProcedureSchedule.procedure_type_id == procedure_type_id)
    if status_filter == "active":
        q = q.where(ProcedureSchedule.status != ProcedureStatus.CANCELLED)
    elif status_filter != "all":
        q = q.where(ProcedureSchedule.status == status_filter)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(ProcedureSchedule.scheduled_date, ProcedureSchedule.id)
                       .offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/schedule", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
def create_schedule(body: ScheduleIn, request: Request, db: DB, user: Manager):
    t = get_owned(db, ProcedureType, body.procedure_type_id, user.hospital_id, "Procedure type")
    if not t.is_active:
        raise _unprocessable("Procedure type is inactive")
    dept_id = body.department_id or t.department_id
    _active_department(db, dept_id, user.hospital_id)
    _check_scope(user, dept_id)
    _validate_slot(db, user.hospital_id, t.id, dept_id, body.scheduled_date, body.status, None)
    row = ProcedureSchedule(hospital_id=user.hospital_id, procedure_type_id=t.id, department_id=dept_id,
                            scheduled_date=body.scheduled_date, count=body.count, status=body.status,
                            notes=body.notes, created_by_id=user.id)
    db.add(row)
    db.flush()
    audit.record(db, user, "procedure_schedule.create", "procedure_schedule", row.id,
                 {**body.model_dump(), "department_id": dept_id}, client_ip(request))
    db.commit()
    db.refresh(row)
    return row


def _get_row(db: Session, row_id: int, user: User) -> ProcedureSchedule:
    row = get_owned(db, ProcedureSchedule, row_id, user.hospital_id, "Schedule entry")
    _check_scope(user, row.department_id)
    return row


@router.patch("/schedule/{row_id}", response_model=ScheduleOut)
def update_schedule(row_id: int, body: ScheduleUpdate, request: Request, db: DB, user: Manager):
    row = _get_row(db, row_id, user)
    if row.status == ProcedureStatus.CANCELLED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cancelled entries cannot be edited; create a new entry")
    data = body.model_dump(exclude_unset=True)
    new_day = data.get("scheduled_date") or row.scheduled_date
    new_status = data.get("status") or row.status
    _validate_slot(db, user.hospital_id, row.procedure_type_id, row.department_id, new_day, new_status, row.id)
    changes = audit.diff(row, {k: v for k, v in data.items() if v is not None or k == "notes"})
    if changes:
        audit.record(db, user, "procedure_schedule.update", "procedure_schedule", row.id, changes, client_ip(request))
    db.commit()
    db.refresh(row)
    return row


@router.post("/schedule/{row_id}/cancel", response_model=ScheduleOut)
def cancel_schedule(row_id: int, body: CancelIn, request: Request, db: DB, user: Manager):
    row = _get_row(db, row_id, user)
    if row.status == ProcedureStatus.CANCELLED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already cancelled")
    if row.status == ProcedureStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Completed procedures cannot be cancelled; correct the count instead")
    row.status = ProcedureStatus.CANCELLED
    if body.reason:
        row.notes = f"{row.notes + ' · ' if row.notes else ''}Cancelled: {body.reason}"
    audit.record(db, user, "procedure_schedule.cancel", "procedure_schedule", row.id, {"reason": body.reason},
                 client_ip(request))
    db.commit()
    db.refresh(row)
    return row


@router.get("/summary", response_model=ProcedureSummary)
def summary(user: Reader, db: DB, days: int = Query(14, ge=1, le=90)):
    today = business_today()
    rows = db.execute(
        select(ProcedureType.id, ProcedureType.code, ProcedureType.name, Department.name,
               func.sum(ProcedureSchedule.count))
        .join(ProcedureType, ProcedureType.id == ProcedureSchedule.procedure_type_id)
        .join(Department, Department.id == ProcedureSchedule.department_id)
        .where(ProcedureSchedule.hospital_id == user.hospital_id, ProcedureSchedule.status != ProcedureStatus.CANCELLED,
               ProcedureSchedule.scheduled_date >= today, ProcedureSchedule.scheduled_date < today + timedelta(days=days))
        .group_by(ProcedureType.id, ProcedureType.code, ProcedureType.name, Department.name)
        .order_by(func.sum(ProcedureSchedule.count).desc())
    ).all()
    through = db.scalar(select(func.max(ProcedureSchedule.scheduled_date)).where(
        ProcedureSchedule.hospital_id == user.hospital_id, ProcedureSchedule.status == ProcedureStatus.SCHEDULED))
    synthetic = db.scalar(select(func.count(ProcedureSchedule.id)).where(
        ProcedureSchedule.hospital_id == user.hospital_id, ProcedureSchedule.is_synthetic.is_(True))) or 0
    by_type = [UpcomingByType(procedure_type_id=i, code=c, name=n, department=d, count=int(s)) for i, c, n, d, s in rows]
    return ProcedureSummary(days=days, total_scheduled=sum(b.count for b in by_type), by_type=by_type,
                            schedule_through=through, synthetic_rows=int(synthetic))


# ============================================================ procedure → item mappings


@router.get("/mappings", response_model=list[MappingOut])
def list_mappings(user: Reader, db: DB, procedure_type_id: int | None = None, consumable_id: int | None = None,
                  include_inactive: bool = False):
    q = select(ProcedureItemMapping).where(ProcedureItemMapping.hospital_id == user.hospital_id)
    if procedure_type_id:
        q = q.where(ProcedureItemMapping.procedure_type_id == procedure_type_id)
    if consumable_id:
        q = q.where(ProcedureItemMapping.consumable_id == consumable_id)
    if not include_inactive:
        q = q.where(ProcedureItemMapping.is_active.is_(True))
    q = q.join(ProcedureType, ProcedureType.id == ProcedureItemMapping.procedure_type_id).join(
        Consumable, Consumable.id == ProcedureItemMapping.consumable_id)
    return db.scalars(q.order_by(ProcedureType.name, Consumable.name)).all()


@router.post("/mappings", response_model=MappingOut, status_code=status.HTTP_201_CREATED)
def create_mapping(body: MappingIn, request: Request, db: DB, user: Manager):
    t = get_owned(db, ProcedureType, body.procedure_type_id, user.hospital_id, "Procedure type")
    _check_scope(user, t.department_id)
    item = get_owned(db, Consumable, body.consumable_id, user.hospital_id, "Item")
    if not item.is_active:
        raise _unprocessable("Item is inactive")
    if db.scalar(select(ProcedureItemMapping.id).where(ProcedureItemMapping.procedure_type_id == t.id,
                                                       ProcedureItemMapping.consumable_id == item.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This item is already mapped to that procedure; edit the existing mapping")
    m = ProcedureItemMapping(hospital_id=user.hospital_id, procedure_type_id=t.id, consumable_id=item.id,
                             quantity_per_procedure=Decimal(str(body.quantity_per_procedure)), notes=body.notes,
                             is_active=body.is_active)
    db.add(m)
    db.flush()
    audit.record(db, user, "procedure_mapping.create", "procedure_mapping", m.id, body.model_dump(), client_ip(request))
    db.commit()
    db.refresh(m)
    return m


@router.patch("/mappings/{mapping_id}", response_model=MappingOut)
def update_mapping(mapping_id: int, body: MappingUpdate, request: Request, db: DB, user: Manager):
    m = get_owned(db, ProcedureItemMapping, mapping_id, user.hospital_id, "Mapping")
    _check_scope(user, m.procedure_type.department_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("quantity_per_procedure") is not None:
        data["quantity_per_procedure"] = Decimal(str(data["quantity_per_procedure"]))
    elif "quantity_per_procedure" in data:
        data.pop("quantity_per_procedure")
    changes = audit.diff(m, data)
    if changes:
        audit.record(db, user, "procedure_mapping.update", "procedure_mapping", m.id, changes, client_ip(request))
    db.commit()
    db.refresh(m)
    return m
