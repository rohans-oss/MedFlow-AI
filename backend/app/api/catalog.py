from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, client_ip, get_owned, require
from app.core.permissions import MANAGE_CATALOG, READ
from app.models import Consumable, ConsumableCategory, User
from app.schemas.inventory import (
    CategoryIn,
    CategoryOut,
    ConsumableCreate,
    ConsumableOut,
    ConsumableUpdate,
)
from app.services import alerts, audit

router = APIRouter(tags=["catalog"])
Reader = Annotated[User, Depends(require(READ))]
Manager = Annotated[User, Depends(require(MANAGE_CATALOG))]


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(user: Reader, db: DB):
    return db.scalars(
        select(ConsumableCategory).where(ConsumableCategory.hospital_id == user.hospital_id).order_by(ConsumableCategory.name)
    ).all()


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryIn, request: Request, db: DB, user: Manager):
    dup = db.scalar(select(ConsumableCategory.id).where(
        ConsumableCategory.hospital_id == user.hospital_id, func.lower(ConsumableCategory.name) == body.name.lower()))
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT, "Category already exists")
    cat = ConsumableCategory(hospital_id=user.hospital_id, **body.model_dump())
    db.add(cat)
    db.flush()
    audit.record(db, user, "category.create", "category", cat.id, body.model_dump(), client_ip(request))
    db.commit()
    return cat


@router.patch("/categories/{cat_id}", response_model=CategoryOut)
def update_category(cat_id: int, body: CategoryIn, request: Request, db: DB, user: Manager):
    cat = get_owned(db, ConsumableCategory, cat_id, user.hospital_id, "Category")
    changes = audit.diff(cat, body.model_dump(exclude_unset=True))
    if changes:
        audit.record(db, user, "category.update", "category", cat.id, changes, client_ip(request))
    db.commit()
    return cat


@router.get("/consumables", response_model=list[ConsumableOut])
def list_consumables(user: Reader, db: DB, search: str | None = None, category_id: int | None = None,
                     include_inactive: bool = False):
    q = select(Consumable).where(Consumable.hospital_id == user.hospital_id)
    if search:
        like = f"%{search.lower()}%"
        q = q.where(or_(func.lower(Consumable.name).like(like), func.lower(Consumable.sku).like(like)))
    if category_id:
        q = q.where(Consumable.category_id == category_id)
    if not include_inactive:
        q = q.where(Consumable.is_active.is_(True))
    return db.scalars(q.order_by(Consumable.name)).all()


def _check_levels(reorder: int, max_level: int | None) -> None:
    if max_level is not None and max_level > 0 and max_level < reorder:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Max level must be ≥ reorder level")


@router.post("/consumables", response_model=ConsumableOut, status_code=status.HTTP_201_CREATED)
def create_consumable(body: ConsumableCreate, request: Request, db: DB, user: Manager):
    if db.scalar(select(Consumable.id).where(Consumable.hospital_id == user.hospital_id, Consumable.sku == body.sku)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"SKU {body.sku} already exists")
    if body.category_id:
        get_owned(db, ConsumableCategory, body.category_id, user.hospital_id, "Category")
    _check_levels(body.reorder_level, body.max_level)
    item = Consumable(hospital_id=user.hospital_id, **body.model_dump())
    db.add(item)
    db.flush()
    alerts.evaluate(db, user.hospital_id, [item.id])  # new item has 0 stock → OUT_OF_STOCK alert
    audit.record(db, user, "consumable.create", "consumable", item.id, body.model_dump(), client_ip(request))
    db.commit()
    db.refresh(item)
    return item


@router.get("/consumables/{item_id}", response_model=ConsumableOut)
def get_consumable(item_id: int, user: Reader, db: DB):
    return get_owned(db, Consumable, item_id, user.hospital_id, "Item")


@router.patch("/consumables/{item_id}", response_model=ConsumableOut)
def update_consumable(item_id: int, body: ConsumableUpdate, request: Request, db: DB, user: Manager):
    item = get_owned(db, Consumable, item_id, user.hospital_id, "Item")
    data = body.model_dump(exclude_unset=True)
    if data.get("category_id"):
        get_owned(db, ConsumableCategory, data["category_id"], user.hospital_id, "Category")
    _check_levels(data.get("reorder_level", item.reorder_level), data.get("max_level", item.max_level))
    changes = audit.diff(item, data)
    if changes:
        audit.record(db, user, "consumable.update", "consumable", item.id, changes, client_ip(request))
        db.flush()
        alerts.evaluate(db, user.hospital_id, [item.id])  # reorder level / active flag may change alerts
    db.commit()
    db.refresh(item)
    return item
