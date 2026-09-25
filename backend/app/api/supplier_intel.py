"""V4 — supplier order log + supplier intelligence API.

/supplier-orders           record orders placed with suppliers, list them, cancel / close short (tracking only)
/supplier-intelligence     scorecards, supplier detail, item comparison (with the V3 deadline), open orders, evaluation
Deliveries are recorded by receiving stock against an order: POST /inventory/receive with `supplier_order_id`.
Nothing here places or recommends an order — that is V5.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.api.deps import DB, client_ip, get_owned, require
from app.core.permissions import MANAGE_SUPPLIERS, READ
from app.core.security import business_today
from app.models import ACTIVE_ORDER_STATUSES, Consumable, Supplier, SupplierOrder, User
from app.schemas.common import Page
from app.schemas.supplier_intel import (
    AtRiskRow,
    ItemOptions,
    OpenOrder,
    OrderRow,
    Scorecards,
    SupplierDetail,
    SupplierEvaluation,
    SupplierOrderClose,
    SupplierOrderIn,
)
from app.services import alerts, audit, supplier_orders
from app.supplier_intel import service
from app.supplier_intel.predict import backtest

orders_router = APIRouter(prefix="/supplier-orders", tags=["supplier orders"])
intel_router = APIRouter(prefix="/supplier-intelligence", tags=["supplier intelligence"])
Reader = Annotated[User, Depends(require(READ))]
Buyer = Annotated[User, Depends(require(MANAGE_SUPPLIERS))]
Window = Annotated[int, Query(ge=30, le=730)]


def _daily(db: DB, hospital_id: int) -> None:
    """Once per day overdue orders change without any event: reuse the V3 lazy daily refresh (alerts included)."""
    from app.api.risk import ensure_current

    ensure_current(db, hospital_id)


# ---------------------------------------------------------------- orders


@orders_router.get("", response_model=Page[OrderRow])
def list_orders(
    user: Reader, db: DB,
    status_filter: Annotated[str, Query(alias="status", pattern="^(active|overdue|OPEN|PARTIAL|RECEIVED|CANCELLED|all)$")] = "all",
    supplier_id: int | None = None, consumable_id: int | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
):
    today = business_today()
    q = select(SupplierOrder).where(SupplierOrder.hospital_id == user.hospital_id)
    if status_filter in ("active", "overdue"):
        q = q.where(SupplierOrder.status.in_(ACTIVE_ORDER_STATUSES))
        if status_filter == "overdue":
            q = q.where(SupplierOrder.expected_date < today)
    elif status_filter != "all":
        q = q.where(SupplierOrder.status == status_filter)
    if supplier_id:
        q = q.where(SupplierOrder.supplier_id == supplier_id)
    if consumable_id:
        q = q.where(SupplierOrder.consumable_id == consumable_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(SupplierOrder.ordered_date.desc(), SupplierOrder.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[service.order_row(o, today) for o in rows], total=total, page=page, page_size=page_size)


@orders_router.post("", response_model=OrderRow, status_code=status.HTTP_201_CREATED)
def create_order(body: SupplierOrderIn, request: Request, db: DB, user: Buyer):
    today = business_today()
    sup = get_owned(db, Supplier, body.supplier_id, user.hospital_id, "Supplier")
    item = get_owned(db, Consumable, body.consumable_id, user.hospital_id, "Item")
    ordered = body.ordered_date or today
    if ordered > today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Order date can't be in the future")
    if ordered < today - timedelta(days=366):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Order date is more than a year ago")
    if body.reference and db.scalar(select(SupplierOrder.id).where(SupplierOrder.hospital_id == user.hospital_id,
                                                                   SupplierOrder.reference == body.reference)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Order reference {body.reference} already exists")
    o = supplier_orders.create_order(db, user, user.hospital_id, sup, item, body.quantity_ordered, body.unit_price,
                                     ordered, body.expected_date, body.reference, body.notes)
    alerts.evaluate(db, user.hospital_id, [item.id], refresh_risk=False)
    audit.record(db, user, "supplier_order.create", "supplier_order", o.id,
                 {"reference": o.reference, "supplier": sup.code, "sku": item.sku, "quantity": o.quantity_ordered,
                  "expected_date": o.expected_date.isoformat()}, client_ip(request))
    db.commit()
    db.refresh(o)
    return service.order_row(o, today)


@orders_router.post("/{order_id}/cancel", response_model=OrderRow)
def cancel_order(order_id: int, body: SupplierOrderClose, request: Request, db: DB, user: Buyer):
    """OPEN → CANCELLED. PARTIAL → closed short (the outstanding balance will not be supplied)."""
    o = get_owned(db, SupplierOrder, order_id, user.hospital_id, "Supplier order")
    before = o.status
    supplier_orders.cancel_or_close(db, o, business_today(), body.reason)
    alerts.evaluate(db, user.hospital_id, [o.consumable_id], refresh_risk=False)
    audit.record(db, user, "supplier_order.cancel" if before == "OPEN" else "supplier_order.close_short",
                 "supplier_order", o.id, {"reference": o.reference, "reason": body.reason}, client_ip(request))
    db.commit()
    db.refresh(o)
    return service.order_row(o, business_today())


# ---------------------------------------------------------------- intelligence


@intel_router.get("/scorecards", response_model=Scorecards)
def scorecards(user: Reader, db: DB, window_days: Window = service.WINDOW_DAYS):
    return service.scorecards(db, user.hospital_id, window_days)


@intel_router.get("/suppliers/{supplier_id}", response_model=SupplierDetail)
def supplier_detail(supplier_id: int, user: Reader, db: DB, window_days: Window = service.WINDOW_DAYS):
    sup = get_owned(db, Supplier, supplier_id, user.hospital_id, "Supplier")
    return service.supplier_detail(db, user.hospital_id, sup, window_days)


@intel_router.get("/items/{item_id}", response_model=ItemOptions)
def item_options(item_id: int, user: Reader, db: DB, window_days: Window = service.WINDOW_DAYS):
    item = get_owned(db, Consumable, item_id, user.hospital_id, "Item")
    _daily(db, user.hospital_id)
    return service.item_options(db, user.hospital_id, item, window_days)


@intel_router.get("/at-risk", response_model=list[AtRiskRow])
def at_risk(user: Reader, db: DB, window_days: Window = service.WINDOW_DAYS):
    _daily(db, user.hospital_id)
    return service.at_risk(db, user.hospital_id, window_days)


@intel_router.get("/open-orders", response_model=list[OpenOrder])
def open_orders(user: Reader, db: DB):
    _daily(db, user.hospital_id)
    return service.open_orders(db, user.hospital_id)


@intel_router.get("/evaluation", response_model=SupplierEvaluation)
def evaluation(user: Reader, db: DB):
    recs = [service._rec(o) for o in service.load_orders(db, user.hospital_id)]
    out = backtest(recs, business_today())
    out["notes"] = [
        "Each delivered order after a 90-day warm-up is predicted from orders whose outcome was known on its order date.",
        "Lead time: quoted = catalogue lead time; history = median (and 90th percentile) of past actual lead times.",
        "Delay: probability that the first delivery comes after the expected date; rates are shrunk towards the "
        "level above (hospital → supplier → supplier × item).",
    ]
    return out
