"""Stock ledger: all changes to batch quantities go through here, and every change writes a StockMovement."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.db.base import utcnow
from app.models import (
    ACTIVE_ALERT_STATUSES,
    Alert,
    Consumable,
    Department,
    MovementType,
    StockBatch,
    StockMovement,
    StockStatus,
    Supplier,
    User,
)


@dataclass
class StockLevel:
    usable: int = 0
    expired: int = 0
    value: float = 0.0
    expired_value: float = 0.0
    batch_count: int = 0
    next_expiry: date | None = None


def stock_levels(db: Session, hospital_id: int, consumable_ids: list[int] | None = None) -> dict[int, StockLevel]:
    """Aggregate batch quantities per consumable, splitting usable vs expired stock."""
    today = business_today()
    is_expired = (StockBatch.expiry_date.is_not(None)) & (StockBatch.expiry_date < today)
    q = (
        select(
            StockBatch.consumable_id,
            func.coalesce(func.sum(case((is_expired, 0), else_=StockBatch.quantity)), 0),
            func.coalesce(func.sum(case((is_expired, StockBatch.quantity), else_=0)), 0),
            func.coalesce(func.sum(case((is_expired, 0), else_=StockBatch.quantity * StockBatch.unit_cost)), 0),
            func.coalesce(func.sum(case((is_expired, StockBatch.quantity * StockBatch.unit_cost), else_=0)), 0),
            func.count(StockBatch.id),
            func.min(case((is_expired, None), else_=StockBatch.expiry_date)),
        )
        .join(Consumable, Consumable.id == StockBatch.consumable_id)
        .where(Consumable.hospital_id == hospital_id, StockBatch.quantity > 0)
        .group_by(StockBatch.consumable_id)
    )
    if consumable_ids is not None:
        q = q.where(StockBatch.consumable_id.in_(consumable_ids))
    out: dict[int, StockLevel] = {}
    for cid, usable, expired, value, expired_value, count, next_exp in db.execute(q):
        out[cid] = StockLevel(int(usable), int(expired), float(value), float(expired_value), int(count), next_exp)
    return out


def status_for(usable: int, reorder_level: int, max_level: int | None) -> StockStatus:
    if usable <= 0:
        return StockStatus.OUT_OF_STOCK
    if usable <= reorder_level:
        return StockStatus.LOW
    if max_level is not None and max_level > 0 and usable > max_level:
        return StockStatus.OVERSTOCK
    return StockStatus.OK


def inventory_rows(db: Session, hospital_id: int, consumable_ids: list[int] | None = None) -> list[dict]:
    q = select(Consumable).where(Consumable.hospital_id == hospital_id)
    if consumable_ids is not None:
        q = q.where(Consumable.id.in_(consumable_ids))
    items = db.scalars(q.order_by(Consumable.name)).all()
    ids = [i.id for i in items]
    levels = stock_levels(db, hospital_id, ids)
    last_moves = dict(
        db.execute(
            select(StockMovement.consumable_id, func.max(StockMovement.created_at))
            .where(StockMovement.hospital_id == hospital_id, StockMovement.consumable_id.in_(ids))
            .group_by(StockMovement.consumable_id)
        ).all()
    )
    alert_counts = dict(
        db.execute(
            select(Alert.consumable_id, func.count(Alert.id))
            .where(Alert.hospital_id == hospital_id, Alert.status.in_(ACTIVE_ALERT_STATUSES),
                   Alert.consumable_id.in_(ids))
            .group_by(Alert.consumable_id)
        ).all()
    )
    rows = []
    for c in items:
        lv = levels.get(c.id, StockLevel())
        rows.append(
            {
                "consumable_id": c.id,
                "sku": c.sku,
                "name": c.name,
                "unit": c.unit,
                "category": c.category.name if c.category else None,
                "category_id": c.category_id,
                "reorder_level": c.reorder_level,
                "max_level": c.max_level,
                "unit_cost": float(c.unit_cost),
                "is_active": c.is_active,
                "usable_stock": lv.usable,
                "expired_stock": lv.expired,
                "stock_value": round(lv.value, 2),
                "status": status_for(lv.usable, c.reorder_level, c.max_level),
                "batch_count": lv.batch_count,
                "next_expiry": lv.next_expiry,
                "last_movement_at": last_moves.get(c.id),
                "open_alerts": alert_counts.get(c.id, 0),
            }
        )
    return rows


# ---------------- Ledger operations ----------------


def _on_hand(db: Session, consumable_id: int) -> int:
    return int(
        db.scalar(select(func.coalesce(func.sum(StockBatch.quantity), 0)).where(StockBatch.consumable_id == consumable_id))
    )


def _lock_consumable(db: Session, consumable: Consumable) -> None:
    # Row lock serialises concurrent movements for the same item (no-op on SQLite).
    db.execute(select(Consumable.id).where(Consumable.id == consumable.id).with_for_update())


def _movement(db: Session, user: User, consumable: Consumable, mtype: MovementType, qty: int, **kw) -> StockMovement:
    db.flush()
    m = StockMovement(
        hospital_id=consumable.hospital_id,
        consumable_id=consumable.id,
        movement_type=mtype,
        quantity=qty,
        balance_after=_on_hand(db, consumable.id),
        performed_by_id=user.id,
        created_at=kw.pop("created_at", None) or utcnow(),
        **kw,
    )
    db.add(m)
    db.flush()
    return m


def _bad(msg: str, code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    return HTTPException(code, msg)


def receive(
    db: Session, user: User, consumable: Consumable, quantity: int, lot_number: str,
    expiry_date: date | None, supplier: Supplier | None, unit_cost: float | None,
    reference: str | None, notes: str | None, at: datetime | None = None,
) -> list[StockMovement]:
    if not consumable.is_active:
        raise _bad("Cannot receive stock for an inactive item")
    if expiry_date and expiry_date < business_today() and at is None:
        raise _bad("Cannot receive a batch that is already expired")
    _lock_consumable(db, consumable)
    cost = Decimal(str(unit_cost)) if unit_cost is not None else consumable.unit_cost
    batch = StockBatch(
        consumable_id=consumable.id,
        supplier_id=supplier.id if supplier else None,
        lot_number=lot_number.strip(),
        expiry_date=expiry_date,
        quantity=quantity,
        initial_quantity=quantity,
        unit_cost=cost,
        received_at=at or utcnow(),
    )
    db.add(batch)
    db.flush()
    return [
        _movement(db, user, consumable, MovementType.RECEIPT, quantity, batch_id=batch.id,
                  supplier_id=batch.supplier_id, reference=reference, reason=notes, created_at=at)
    ]


def issue(
    db: Session, user: User, consumable: Consumable, quantity: int, department: Department,
    reference: str | None, notes: str | None, at: datetime | None = None, today: date | None = None,
) -> list[StockMovement]:
    """Issue to a department, picking batches First-Expiry-First-Out and skipping expired batches."""
    _lock_consumable(db, consumable)
    today = today or business_today()
    batches = db.scalars(
        select(StockBatch)
        .where(
            StockBatch.consumable_id == consumable.id,
            StockBatch.quantity > 0,
            (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date >= today),
        )
        .order_by(StockBatch.expiry_date.asc().nulls_last(), StockBatch.received_at.asc(), StockBatch.id.asc())
        .with_for_update(of=StockBatch)
    ).all()
    available = sum(b.quantity for b in batches)
    if available < quantity:
        raise _bad(f"Insufficient usable stock: requested {quantity}, available {available} {consumable.unit}")
    remaining = quantity
    moves = []
    for b in batches:
        if remaining == 0:
            break
        take = min(b.quantity, remaining)
        b.quantity -= take
        remaining -= take
        moves.append(
            _movement(db, user, consumable, MovementType.ISSUE, -take, batch_id=b.id,
                      department_id=department.id, reference=reference, reason=notes, created_at=at)
        )
    return moves


def return_stock(
    db: Session, user: User, batch: StockBatch, consumable: Consumable, quantity: int,
    department: Department, reason: str | None,
) -> list[StockMovement]:
    _lock_consumable(db, consumable)
    issued = -int(
        db.scalar(
            select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
                StockMovement.batch_id == batch.id,
                StockMovement.department_id == department.id,
                StockMovement.movement_type.in_([MovementType.ISSUE, MovementType.RETURN]),
            )
        )
    )
    if quantity > issued:
        raise _bad(f"{department.name} has only {max(issued, 0)} outstanding from lot {batch.lot_number}; cannot return {quantity}")
    batch.quantity += quantity
    return [
        _movement(db, user, consumable, MovementType.RETURN, quantity, batch_id=batch.id,
                  department_id=department.id, reason=reason)
    ]


def wastage(
    db: Session, user: User, batch: StockBatch, consumable: Consumable, quantity: int, reason: str,
    at: datetime | None = None,
) -> list[StockMovement]:
    _lock_consumable(db, consumable)
    if quantity > batch.quantity:
        raise _bad(f"Lot {batch.lot_number} has only {batch.quantity} {consumable.unit}")
    batch.quantity -= quantity
    return [_movement(db, user, consumable, MovementType.WASTAGE, -quantity, batch_id=batch.id, reason=reason, created_at=at)]


def adjust(
    db: Session, user: User, batch: StockBatch, consumable: Consumable, counted: int, reason: str,
    at: datetime | None = None,
) -> list[StockMovement]:
    _lock_consumable(db, consumable)
    delta = counted - batch.quantity
    if delta == 0:
        raise _bad("Counted quantity equals system quantity; nothing to adjust")
    batch.quantity = counted
    return [_movement(db, user, consumable, MovementType.ADJUSTMENT, delta, batch_id=batch.id, reason=reason, created_at=at)]


def daily_series(db: Session, hospital_id: int, days: int = 30, consumable_id: int | None = None) -> list[dict]:
    """Daily issued (net of returns) and received quantities for the last `days` days."""
    start = business_today() - timedelta(days=days - 1)
    from zoneinfo import ZoneInfo

    from app.core.config import settings

    tz = ZoneInfo(settings.TIMEZONE)
    since = datetime.combine(start, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    q = select(StockMovement.created_at, StockMovement.movement_type, StockMovement.quantity, Consumable.unit_cost).join(
        Consumable, Consumable.id == StockMovement.consumable_id
    ).where(
        StockMovement.hospital_id == hospital_id,
        StockMovement.created_at >= since,
        StockMovement.movement_type.in_([MovementType.ISSUE, MovementType.RETURN, MovementType.RECEIPT]),
    )
    if consumable_id is not None:
        q = q.where(StockMovement.consumable_id == consumable_id)
    buckets = {start + timedelta(days=i): {"issued": 0, "received": 0, "issued_value": 0.0} for i in range(days)}
    for created, mtype, qty, cost in db.execute(q):
        if created.tzinfo is None:  # SQLite returns naive datetimes (stored as UTC)
            created = created.replace(tzinfo=UTC)
        d = created.astimezone(tz).date()
        if d not in buckets:
            continue
        if mtype == MovementType.RECEIPT:
            buckets[d]["received"] += qty
        else:
            buckets[d]["issued"] += -qty
            buckets[d]["issued_value"] += -qty * float(cost)
    return [{"date": d, **v, "issued_value": round(v["issued_value"], 2)} for d, v in sorted(buckets.items())]
