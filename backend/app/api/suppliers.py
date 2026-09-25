from datetime import timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, client_ip, get_owned, require
from app.core.permissions import MANAGE_SUPPLIERS, READ
from app.db.base import utcnow
from app.models import Consumable, MovementType, StockMovement, Supplier, SupplierProduct, User
from app.schemas.inventory import (
    SupplierCreate,
    SupplierListItem,
    SupplierOut,
    SupplierProductIn,
    SupplierProductOut,
    SupplierProductUpdate,
    SupplierUpdate,
)
from app.services import audit

router = APIRouter(prefix="/suppliers", tags=["suppliers"])
Reader = Annotated[User, Depends(require(READ))]
Manager = Annotated[User, Depends(require(MANAGE_SUPPLIERS))]


@router.get("", response_model=list[SupplierListItem])
def list_suppliers(user: Reader, db: DB, search: str | None = None, include_inactive: bool = False):
    q = select(Supplier).where(Supplier.hospital_id == user.hospital_id)
    if search:
        like = f"%{search.lower()}%"
        q = q.where(or_(func.lower(Supplier.name).like(like), func.lower(Supplier.code).like(like),
                        func.lower(func.coalesce(Supplier.city, "")).like(like)))
    if not include_inactive:
        q = q.where(Supplier.is_active.is_(True))
    suppliers = db.scalars(q.order_by(Supplier.name)).all()
    ids = [s.id for s in suppliers]
    products = dict(db.execute(
        select(SupplierProduct.supplier_id, func.count()).where(SupplierProduct.supplier_id.in_(ids))
        .group_by(SupplierProduct.supplier_id)).all())
    since = utcnow() - timedelta(days=90)
    receipts = {
        sid: (cnt, last) for sid, cnt, last in db.execute(
            select(StockMovement.supplier_id, func.count(), func.max(StockMovement.created_at))
            .where(StockMovement.supplier_id.in_(ids), StockMovement.movement_type == MovementType.RECEIPT,
                   StockMovement.created_at >= since)
            .group_by(StockMovement.supplier_id))
    }
    out = []
    for s in suppliers:
        item = SupplierListItem.model_validate(s)
        item.product_count = products.get(s.id, 0)
        item.receipts_90d, item.last_receipt_at = receipts.get(s.id, (0, None))
        out.append(item)
    return out


@router.post("", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
def create_supplier(body: SupplierCreate, request: Request, db: DB, user: Manager):
    if db.scalar(select(Supplier.id).where(Supplier.hospital_id == user.hospital_id, Supplier.code == body.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Supplier code {body.code} already exists")
    s = Supplier(hospital_id=user.hospital_id, **body.model_dump())
    db.add(s)
    db.flush()
    audit.record(db, user, "supplier.create", "supplier", s.id, body.model_dump(), client_ip(request))
    db.commit()
    return s


@router.get("/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: int, user: Reader, db: DB):
    return get_owned(db, Supplier, supplier_id, user.hospital_id, "Supplier")


@router.patch("/{supplier_id}", response_model=SupplierOut)
def update_supplier(supplier_id: int, body: SupplierUpdate, request: Request, db: DB, user: Manager):
    s = get_owned(db, Supplier, supplier_id, user.hospital_id, "Supplier")
    changes = audit.diff(s, body.model_dump(exclude_unset=True))
    if changes:
        audit.record(db, user, "supplier.update", "supplier", s.id, changes, client_ip(request))
    db.commit()
    return s


# ---------- Supplier catalogue ----------


@router.get("/{supplier_id}/products", response_model=list[SupplierProductOut])
def list_products(supplier_id: int, user: Reader, db: DB):
    get_owned(db, Supplier, supplier_id, user.hospital_id, "Supplier")
    return db.scalars(
        select(SupplierProduct).join(Consumable).where(SupplierProduct.supplier_id == supplier_id).order_by(Consumable.name)
    ).all()


def _get_product(db: DB, supplier_id: int, product_id: int, hospital_id: int) -> SupplierProduct:
    get_owned(db, Supplier, supplier_id, hospital_id, "Supplier")
    sp = db.get(SupplierProduct, product_id)
    if not sp or sp.supplier_id != supplier_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier product not found")
    return sp


def _clear_other_preferred(db: DB, consumable_id: int, keep_id: int) -> None:
    for other in db.scalars(select(SupplierProduct).where(
            SupplierProduct.consumable_id == consumable_id, SupplierProduct.id != keep_id,
            SupplierProduct.is_preferred.is_(True))):
        other.is_preferred = False


@router.post("/{supplier_id}/products", response_model=SupplierProductOut, status_code=status.HTTP_201_CREATED)
def add_product(supplier_id: int, body: SupplierProductIn, request: Request, db: DB, user: Manager):
    get_owned(db, Supplier, supplier_id, user.hospital_id, "Supplier")
    get_owned(db, Consumable, body.consumable_id, user.hospital_id, "Item")
    if db.scalar(select(SupplierProduct.id).where(SupplierProduct.supplier_id == supplier_id,
                                                  SupplierProduct.consumable_id == body.consumable_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This supplier already lists that item")
    data = body.model_dump()
    data["unit_price"] = Decimal(str(data["unit_price"]))
    sp = SupplierProduct(supplier_id=supplier_id, **data)
    db.add(sp)
    db.flush()
    if sp.is_preferred:
        _clear_other_preferred(db, sp.consumable_id, sp.id)
    audit.record(db, user, "supplier_product.create", "supplier", supplier_id, body.model_dump(), client_ip(request))
    db.commit()
    db.refresh(sp)
    return sp


@router.patch("/{supplier_id}/products/{product_id}", response_model=SupplierProductOut)
def update_product(supplier_id: int, product_id: int, body: SupplierProductUpdate, request: Request, db: DB,
                   user: Manager):
    sp = _get_product(db, supplier_id, product_id, user.hospital_id)
    data = body.model_dump(exclude_unset=True)
    if "unit_price" in data and data["unit_price"] is not None:
        data["unit_price"] = Decimal(str(data["unit_price"]))
    changes = audit.diff(sp, data)
    if changes.get("is_preferred", {}).get("to"):
        _clear_other_preferred(db, sp.consumable_id, sp.id)
    if changes:
        audit.record(db, user, "supplier_product.update", "supplier", supplier_id,
                     {"product_id": sp.id, **changes}, client_ip(request))
    db.commit()
    db.refresh(sp)
    return sp


@router.delete("/{supplier_id}/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_product(supplier_id: int, product_id: int, request: Request, db: DB, user: Manager):
    sp = _get_product(db, supplier_id, product_id, user.hospital_id)
    audit.record(db, user, "supplier_product.delete", "supplier", supplier_id,
                 {"product_id": sp.id, "consumable_id": sp.consumable_id}, client_ip(request))
    db.delete(sp)
    db.commit()
