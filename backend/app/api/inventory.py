from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, client_ip, get_owned, require
from app.core.config import settings
from app.core.permissions import READ, STOCK_ISSUE, STOCK_ISSUE_OWN_DEPT, STOCK_RECEIVE, permissions_for
from app.core.security import business_today
from app.models import (
    ACTIVE_ORDER_STATUSES,
    Consumable,
    Department,
    MovementType,
    StockBatch,
    StockMovement,
    StockStatus,
    Supplier,
    SupplierOrder,
    SupplierProduct,
    User,
)
from app.schemas.common import Page
from app.schemas.inventory import (
    AdjustRequest,
    BatchOut,
    InventoryDetail,
    InventoryRow,
    IssueRequest,
    MovementOut,
    MovementResult,
    ReceiveRequest,
    ReturnRequest,
    SupplierProductOut,
    WastageRequest,
)
from app.services import alerts, audit, stock, supplier_orders


def _day_start(d: date) -> datetime:
    """Start of a business-timezone day, as UTC (SQLite stores naive UTC)."""
    return datetime.combine(d, datetime.min.time(), tzinfo=ZoneInfo(settings.TIMEZONE)).astimezone(UTC)


router = APIRouter(prefix="/inventory", tags=["inventory"])
Reader = Annotated[User, Depends(require(READ))]
Receiver = Annotated[User, Depends(require(STOCK_RECEIVE))]
Issuer = Annotated[User, Depends(require(STOCK_ISSUE, STOCK_ISSUE_OWN_DEPT))]


@router.get("", response_model=list[InventoryRow])
def list_inventory(
    user: Reader, db: DB,
    search: str | None = None,
    category_id: int | None = None,
    status_filter: Annotated[StockStatus | None, Query(alias="status")] = None,
    include_inactive: bool = False,
):
    rows = stock.inventory_rows(db, user.hospital_id)
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["name"].lower() or s in r["sku"].lower()]
    if category_id:
        rows = [r for r in rows if r["category_id"] == category_id]
    if status_filter:
        rows = [r for r in rows if r["status"] == status_filter]
    if not include_inactive:
        rows = [r for r in rows if r["is_active"]]
    return rows


def _batch_out(b: StockBatch, today: date) -> BatchOut:
    out = BatchOut.model_validate(b)
    if b.expiry_date:
        out.days_to_expiry = (b.expiry_date - today).days
        out.is_expired = out.days_to_expiry < 0
    return out


@router.get("/batches", response_model=list[BatchOut])
def list_batches(user: Reader, db: DB, consumable_id: int, include_empty: bool = False):
    get_owned(db, Consumable, consumable_id, user.hospital_id, "Item")
    q = select(StockBatch).where(StockBatch.consumable_id == consumable_id)
    if not include_empty:
        q = q.where(StockBatch.quantity > 0)
    today = business_today()
    return [_batch_out(b, today) for b in db.scalars(q.order_by(StockBatch.expiry_date.asc().nulls_last()))]


@router.get("/movements", response_model=Page[MovementOut])
def list_movements(
    user: Reader, db: DB,
    consumable_id: int | None = None,
    department_id: int | None = None,
    supplier_id: int | None = None,
    movement_type: MovementType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    q = select(StockMovement).where(StockMovement.hospital_id == user.hospital_id)
    if consumable_id:
        q = q.where(StockMovement.consumable_id == consumable_id)
    if department_id:
        q = q.where(StockMovement.department_id == department_id)
    if supplier_id:
        q = q.where(StockMovement.supplier_id == supplier_id)
    if movement_type:
        q = q.where(StockMovement.movement_type == movement_type)
    if date_from:
        q = q.where(StockMovement.created_at >= _day_start(date_from))
    if date_to:
        q = q.where(StockMovement.created_at < _day_start(date_to + timedelta(days=1)))
    if search:
        like = f"%{search.lower()}%"
        q = q.join(Consumable, Consumable.id == StockMovement.consumable_id).where(
            or_(func.lower(Consumable.name).like(like), func.lower(Consumable.sku).like(like),
                func.lower(func.coalesce(StockMovement.reference, "")).like(like))
        )
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(
        q.order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/{consumable_id}", response_model=InventoryDetail)
def inventory_detail(consumable_id: int, user: Reader, db: DB):
    item = get_owned(db, Consumable, consumable_id, user.hospital_id, "Item")
    row = stock.inventory_rows(db, user.hospital_id, [item.id])[0]
    today = business_today()
    batches = db.scalars(
        select(StockBatch).where(StockBatch.consumable_id == item.id, StockBatch.quantity > 0)
        .order_by(StockBatch.expiry_date.asc().nulls_last())
    ).all()
    suppliers = db.scalars(
        select(SupplierProduct).where(SupplierProduct.consumable_id == item.id)
        .order_by(SupplierProduct.is_preferred.desc(), SupplierProduct.unit_price)
    ).all()
    moves = db.scalars(
        select(StockMovement).where(StockMovement.consumable_id == item.id)
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(25)
    ).all()
    daily = stock.daily_series(db, user.hospital_id, 30, item.id)
    avg = sum(d["issued"] for d in daily) / 30
    return InventoryDetail(
        item=item,
        stock=row,
        batches=[_batch_out(b, today) for b in batches],
        suppliers=[SupplierProductOut.model_validate(s) for s in suppliers],
        recent_movements=moves,
        daily=daily,
        avg_daily_issue_30d=round(avg, 2),
        days_of_cover=round(row["usable_stock"] / avg, 1) if avg > 0 else None,
    )


# ---------------- Stock operations ----------------


def _finish(db: DB, user: User, item: Consumable, moves: list[StockMovement], action: str, details: dict, ip) -> MovementResult:
    alerts.evaluate(db, user.hospital_id, [item.id])
    audit.record(db, user, action, "consumable", item.id,
                 {**details, "movement_ids": [m.id for m in moves]}, ip)
    db.commit()
    row = stock.inventory_rows(db, user.hospital_id, [item.id])[0]
    for m in moves:
        db.refresh(m)
    return MovementResult(movements=moves, usable_stock=row["usable_stock"], status=row["status"])


def _check_dept_scope(user: User, department_id: int) -> None:
    perms = permissions_for(user.role)
    if STOCK_ISSUE not in perms and user.department_id != department_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only issue or return stock for your own department")


def _batch_and_item(db: DB, batch_id: int, hospital_id: int) -> tuple[StockBatch, Consumable]:
    batch = db.get(StockBatch, batch_id)
    item = db.get(Consumable, batch.consumable_id) if batch else None
    if not batch or not item or item.hospital_id != hospital_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    return batch, item


@router.post("/receive", response_model=MovementResult, status_code=status.HTTP_201_CREATED)
def receive(body: ReceiveRequest, request: Request, db: DB, user: Receiver):
    item = get_owned(db, Consumable, body.consumable_id, user.hospital_id, "Item")
    supplier = get_owned(db, Supplier, body.supplier_id, user.hospital_id, "Supplier") if body.supplier_id else None
    order = None
    if body.supplier_order_id is not None:  # V4: delivery against a supplier order
        order = get_owned(db, SupplierOrder, body.supplier_order_id, user.hospital_id, "Supplier order")
        if order.consumable_id != item.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The supplier order is for a different item")
        if supplier is not None and supplier.id != order.supplier_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The supplier order is with a different supplier")
        if order.status not in ACTIVE_ORDER_STATUSES:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Order {order.reference} is {order.status.lower()}")
        supplier = order.supplier
    unit_cost = body.unit_cost if body.unit_cost is not None or order is None else float(order.unit_price)
    moves = stock.receive(db, user, item, body.quantity, body.lot_number, body.expiry_date, supplier,
                          unit_cost, body.reference or (order.reference if order else None), body.notes)
    if order is not None:
        supplier_orders.record_delivery(db, order, business_today(), body.quantity, unit_cost, moves[0])
    return _finish(db, user, item, moves, "stock.receive", body.model_dump(), client_ip(request))


@router.post("/issue", response_model=MovementResult, status_code=status.HTTP_201_CREATED)
def issue(body: IssueRequest, request: Request, db: DB, user: Issuer):
    _check_dept_scope(user, body.department_id)
    item = get_owned(db, Consumable, body.consumable_id, user.hospital_id, "Item")
    dept = get_owned(db, Department, body.department_id, user.hospital_id, "Department")
    if not dept.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Department is inactive")
    moves = stock.issue(db, user, item, body.quantity, dept, body.reference, body.notes)
    return _finish(db, user, item, moves, "stock.issue", body.model_dump(), client_ip(request))


@router.post("/return", response_model=MovementResult, status_code=status.HTTP_201_CREATED)
def return_stock(body: ReturnRequest, request: Request, db: DB, user: Issuer):
    _check_dept_scope(user, body.department_id)
    batch, item = _batch_and_item(db, body.batch_id, user.hospital_id)
    dept = get_owned(db, Department, body.department_id, user.hospital_id, "Department")
    moves = stock.return_stock(db, user, batch, item, body.quantity, dept, body.reason)
    return _finish(db, user, item, moves, "stock.return", body.model_dump(), client_ip(request))


@router.post("/wastage", response_model=MovementResult, status_code=status.HTTP_201_CREATED)
def wastage(body: WastageRequest, request: Request, db: DB, user: Receiver):
    batch, item = _batch_and_item(db, body.batch_id, user.hospital_id)
    moves = stock.wastage(db, user, batch, item, body.quantity, body.reason)
    return _finish(db, user, item, moves, "stock.wastage", body.model_dump(), client_ip(request))


@router.post("/adjust", response_model=MovementResult, status_code=status.HTTP_201_CREATED)
def adjust(body: AdjustRequest, request: Request, db: DB, user: Receiver):
    batch, item = _batch_and_item(db, body.batch_id, user.hospital_id)
    before = batch.quantity
    moves = stock.adjust(db, user, batch, item, body.counted_quantity, body.reason)
    return _finish(db, user, item, moves, "stock.adjust", {**body.model_dump(), "system_quantity": before},
                   client_ip(request))
