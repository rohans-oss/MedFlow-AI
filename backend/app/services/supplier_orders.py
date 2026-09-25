"""V4 — supplier order log: record orders placed with suppliers and what actually arrived.

This is evidence for supplier reliability, not a purchasing workflow: there is no approval step and nothing is
ordered automatically (recommendations are V5). Deliveries that go through the stock ledger are linked to their
RECEIPT movement; imported historical deliveries (before the ledger started) carry no movement.
"""

from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ACTIVE_ORDER_STATUSES,
    Consumable,
    StockMovement,
    Supplier,
    SupplierDelivery,
    SupplierOrder,
    SupplierOrderStatus,
    SupplierProduct,
    User,
)


def _bad(msg: str, code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    return HTTPException(code, msg)


def quoted_lead_time(db: Session, supplier: Supplier, item: Consumable) -> int:
    sp = db.scalar(select(SupplierProduct).where(SupplierProduct.supplier_id == supplier.id,
                                                 SupplierProduct.consumable_id == item.id))
    return sp.lead_time_days if sp else supplier.default_lead_time_days


def next_reference(db: Session, hospital_id: int, prefix: str = "SO") -> str:
    n = db.scalar(select(func.count(SupplierOrder.id)).where(SupplierOrder.hospital_id == hospital_id)) or 0
    while True:
        n += 1
        ref = f"{prefix}-{n:05d}"
        if not db.scalar(select(SupplierOrder.id).where(SupplierOrder.hospital_id == hospital_id,
                                                        SupplierOrder.reference == ref)):
            return ref


def create_order(db: Session, user: User | None, hospital_id: int, supplier: Supplier, item: Consumable, quantity: int,
                 unit_price: Decimal | float | None, ordered_date: date, expected_date: date | None = None,
                 reference: str | None = None, notes: str | None = None, is_synthetic: bool = False) -> SupplierOrder:
    if not supplier.is_active:
        raise _bad("Supplier is inactive")
    if not item.is_active:
        raise _bad("Item is inactive")
    if quantity <= 0:
        raise _bad("Quantity must be positive")
    lead = quoted_lead_time(db, supplier, item)
    if unit_price is None:
        sp = db.scalar(select(SupplierProduct).where(SupplierProduct.supplier_id == supplier.id,
                                                     SupplierProduct.consumable_id == item.id))
        unit_price = sp.unit_price if sp else item.unit_cost
    expected = expected_date or ordered_date + timedelta(days=lead)
    if expected < ordered_date:
        raise _bad("Expected date cannot be before the order date")
    order = SupplierOrder(
        hospital_id=hospital_id, supplier_id=supplier.id, consumable_id=item.id,
        reference=reference or next_reference(db, hospital_id), ordered_date=ordered_date,
        quoted_lead_time_days=lead, expected_date=expected, quantity_ordered=quantity,
        unit_price=Decimal(str(unit_price)), status=SupplierOrderStatus.OPEN, notes=notes, is_synthetic=is_synthetic,
        created_by_id=user.id if user else None,
    )
    db.add(order)
    db.flush()
    return order


def record_delivery(db: Session, order: SupplierOrder, received_date: date, quantity: int,
                    unit_price: Decimal | float | None = None, movement: StockMovement | None = None,
                    is_synthetic: bool = False) -> SupplierDelivery:
    if order.status not in ACTIVE_ORDER_STATUSES:
        raise _bad(f"Order {order.reference} is {order.status.lower()} — it can't receive deliveries",
                   status.HTTP_409_CONFLICT)
    if received_date < order.ordered_date:
        raise _bad("A delivery can't arrive before the order was placed")
    d = SupplierDelivery(
        hospital_id=order.hospital_id, order_id=order.id, received_date=received_date, quantity=quantity,
        unit_price=Decimal(str(unit_price)) if unit_price is not None else order.unit_price,
        stock_movement_id=movement.id if movement else None, batch_id=movement.batch_id if movement else None,
        is_synthetic=is_synthetic,
    )
    db.add(d)
    order.quantity_received += quantity
    if order.first_delivery_date is None or received_date < order.first_delivery_date:
        order.first_delivery_date = received_date
    if order.quantity_received >= order.quantity_ordered:
        order.status = SupplierOrderStatus.RECEIVED
        order.completed_date = received_date
    else:
        order.status = SupplierOrderStatus.PARTIAL
    db.flush()
    return d


def cancel_or_close(db: Session, order: SupplierOrder, on: date, reason: str | None) -> SupplierOrder:
    """OPEN → CANCELLED; PARTIAL → RECEIVED (closed short: the rest will not come)."""
    if order.status not in ACTIVE_ORDER_STATUSES:
        raise _bad(f"Order {order.reference} is already {order.status.lower()}", status.HTTP_409_CONFLICT)
    if on < order.ordered_date:
        raise _bad("Date is before the order date")
    if order.status == SupplierOrderStatus.OPEN:
        order.status, order.cancelled_date = SupplierOrderStatus.CANCELLED, on
    else:
        order.status = SupplierOrderStatus.RECEIVED
    order.completed_date = on
    order.close_reason = reason
    db.flush()
    return order
