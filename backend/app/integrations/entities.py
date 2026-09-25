"""V9 — the MedFlow entities a hospital system can send, and how each one is applied through the EXISTING services.

Master data (departments, suppliers, items, supplier_items) is upserted by its natural key. Transactions go through the
same code paths a person uses in the UI: consumption → `stock.issue` (FEFO), deliveries → `stock.receive` +
`supplier_orders.record_delivery`, purchase orders → `supplier_orders.create_order` / `cancel_or_close`. Inventory
snapshots are *compared* with the ledger (reconciliation) — they never silently overwrite stock.

Where the data then flows: consumption → V2 forecasts (daily issues); stock / batches → V3 risk; orders and deliveries →
V4 supplier reliability and V5 in-transit / procurement; all of it → V6 graph projection and V7 assistant tools.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.db.base import utcnow
from app.integrations import fields as F
from app.integrations.fields import Field, RowError
from app.models import (
    ACTIVE_ORDER_STATUSES,
    Consumable,
    ConsumableCategory,
    Department,
    IntegrationSource,
    ReconciliationIssue,
    StockMovement,
    Supplier,
    SupplierOrder,
    SupplierOrderStatus,
    SupplierProduct,
    SyncRun,
    User,
)
from app.services import stock, supplier_orders

UPDATED_AT = Field("updated_at", "datetime", description="Last change in the source system (used as the incremental-sync cursor)")


@dataclass
class Ctx:
    db: Session
    source: IntegrationSource
    actor: User  # the source's service account (performed_by on ledger movements)
    run: SyncRun
    tolerance: int = 0
    touched_items: set[int] = field(default_factory=set)
    info: dict[str, int] = field(default_factory=dict)

    @property
    def hid(self) -> int:
        return self.source.hospital_id

    def note(self, key: str, n: int = 1) -> None:
        self.info[key] = self.info.get(key, 0) + n


@dataclass(frozen=True)
class Entity:
    name: str
    label: str
    fields: tuple[Field, ...]
    key: Callable[[dict], str]  # natural key / external id of one validated record
    apply: Callable[[Ctx, dict], tuple[str, str, int | None]]  # → (created|updated|unchanged, target_type, target_id)
    transactional: bool = False  # a changed re-send is rejected (ledger is append-only) instead of applied
    sort_key: str | None = None  # records are applied in this field's order (chronology)
    description: str = ""

    def field(self, name: str) -> Field:
        return next(f for f in self.fields if f.name == name)


# ---------------------------------------------------------------- lookups


def _item(ctx: Ctx, code: str, fld: str = "item_code") -> Consumable:
    item = ctx.db.scalar(select(Consumable).where(Consumable.hospital_id == ctx.hid, func.upper(Consumable.sku) == code))
    if item is None:
        raise RowError(f"unknown item '{code}' (send items first, or map the item code)", F.UNKNOWN_ITEM, fld)
    return item


def _supplier(ctx: Ctx, code: str) -> Supplier:
    s = ctx.db.scalar(select(Supplier).where(Supplier.hospital_id == ctx.hid, func.upper(Supplier.code) == code))
    if s is None:
        raise RowError(f"unknown supplier '{code}' (send suppliers first)", F.UNKNOWN_SUPPLIER, "supplier_code")
    return s


def _department(ctx: Ctx, code: str) -> Department:
    d = ctx.db.scalar(select(Department).where(Department.hospital_id == ctx.hid, func.upper(Department.code) == code))
    if d is None:
        raise RowError(f"unknown department '{code}'", F.UNKNOWN_DEPARTMENT, "department_code")
    if not d.is_active:
        raise RowError(f"department '{code}' is inactive in MedFlow", F.INVALID_DEPARTMENT, "department_code")
    return d


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite returns naive UTC


def _latest_movement_at(ctx: Ctx, item: Consumable) -> datetime | None:
    # movements stamped in the future (only the synthetic demo seed produces them, for "today") are ignored, exactly as a
    # manual issue made now ignores them
    t = ctx.db.scalar(select(func.max(StockMovement.created_at)).where(StockMovement.consumable_id == item.id,
                                                                       StockMovement.created_at <= utcnow()))
    return _aware(t) if t else None


def _event_time(ctx: Ctx, value: tuple[datetime, bool], fld: str) -> datetime:
    """A transaction time. A bare date of today means 'now'; the future is rejected."""
    dt, has_time = value
    now = utcnow()
    if not has_time:
        d = F.local_date(dt)
        today = business_today()
        if d > today:
            raise RowError(f"{fld} {d} is in the future", F.INVALID_DATE, fld)
        dt = now if d == today else F.end_of_local_day(d)
    if dt > now + timedelta(minutes=5):
        raise RowError(f"{fld} {dt.isoformat()} is in the future", F.INVALID_DATE, fld)
    return min(dt, now)


def _chronology(ctx: Ctx, item: Consumable, at: datetime, fld: str) -> None:
    latest = _latest_movement_at(ctx, item)
    if latest is not None and at < latest:
        raise RowError(
            f"{fld} {at.isoformat(timespec='seconds')} is before {item.sku}'s latest ledger movement "
            f"({latest.isoformat(timespec='seconds')}); the stock ledger is append-only, so older transactions cannot be "
            "inserted — record the correction in MedFlow instead", F.OUT_OF_ORDER, fld)


def _service(fn: Callable, code: str = F.CONFLICT):
    """Run an existing service call; its HTTP-style validation errors become row errors."""
    try:
        return fn()
    except HTTPException as e:
        c = F.INSUFFICIENT_STOCK if "Insufficient" in str(e.detail) else code
        raise RowError(str(e.detail), c) from e


def _set(obj, attrs: dict) -> bool:
    changed = False
    for k, v in attrs.items():
        if v is None:
            continue
        if getattr(obj, k) != v:
            setattr(obj, k, v)
            changed = True
    return changed


def _upsert(ctx: Ctx, cls, lookup: dict, attrs: dict, target: str) -> tuple[str, str, int | None]:
    q = select(cls)
    for k, v in lookup.items():
        col = getattr(cls, k)
        q = q.where(func.upper(col) == v if isinstance(v, str) else col == v)
    obj = ctx.db.scalar(q)
    if obj is None:
        obj = cls(**lookup, **{k: v for k, v in attrs.items() if v is not None})
        ctx.db.add(obj)
        ctx.db.flush()
        return "created", target, obj.id
    changed = _set(obj, attrs)
    ctx.db.flush()
    return ("updated" if changed else "unchanged"), target, obj.id


# ---------------------------------------------------------------- master data


def apply_department(ctx: Ctx, v: dict):
    return _upsert(ctx, Department, {"hospital_id": ctx.hid, "code": v["code"]},
                   {"name": v["name"], "description": v.get("description"), "is_active": v.get("active")}, "department")


def apply_supplier(ctx: Ctx, v: dict):
    return _upsert(ctx, Supplier, {"hospital_id": ctx.hid, "code": v["code"]}, {
        "name": v["name"], "city": v.get("city"), "email": v.get("email"), "phone": v.get("phone"),
        "contact_person": v.get("contact_person"), "default_lead_time_days": v.get("default_lead_time_days"),
        "is_active": v.get("active")}, "supplier")


def apply_item(ctx: Ctx, v: dict):
    cat_id = None
    if v.get("category"):
        cat = ctx.db.scalar(select(ConsumableCategory).where(ConsumableCategory.hospital_id == ctx.hid,
                                                             func.lower(ConsumableCategory.name) == v["category"].lower()))
        if cat is None:
            cat = ConsumableCategory(hospital_id=ctx.hid, name=v["category"][:120])
            ctx.db.add(cat)
            ctx.db.flush()
            ctx.note("categories_created")
        cat_id = cat.id
    out = _upsert(ctx, Consumable, {"hospital_id": ctx.hid, "sku": v["code"]}, {
        "name": v["name"], "unit": v["unit"], "category_id": cat_id, "unit_cost": v.get("unit_cost"),
        "reorder_level": v.get("reorder_level"), "max_level": v.get("max_level"), "description": v.get("description"),
        "is_active": v.get("active")}, "consumable")
    if out[0] != "unchanged":
        ctx.touched_items.add(out[2])
    return out


def apply_supplier_item(ctx: Ctx, v: dict):
    s, item = _supplier(ctx, v["supplier_code"]), _item(ctx, v["item_code"])
    return _upsert(ctx, SupplierProduct, {"supplier_id": s.id, "consumable_id": item.id}, {
        "unit_price": v["unit_price"], "lead_time_days": v.get("lead_time_days"), "moq": v.get("moq"),
        "supplier_sku": v.get("supplier_sku"), "is_preferred": v.get("preferred")}, "supplier_product")


# ---------------------------------------------------------------- stock: snapshots → reconciliation


def balance_as_of(db: Session, item: Consumable, at: datetime) -> int:
    """On-hand (all batches, usable + expired) at time `at`: today's on-hand with every later ledger movement rewound."""
    from app.models import StockBatch

    now = int(db.scalar(select(func.coalesce(func.sum(StockBatch.quantity), 0)).where(StockBatch.consumable_id == item.id)))
    later = int(db.scalar(select(func.coalesce(func.sum(StockMovement.quantity), 0))
                          .where(StockMovement.consumable_id == item.id, StockMovement.created_at > at)))
    return now - later


def apply_inventory(ctx: Ctx, v: dict):
    item = _item(ctx, v["item_code"])
    now = utcnow()
    if v.get("as_of") is None:
        as_of = now
    else:
        dt, has_time = v["as_of"]
        as_of = dt if has_time else min(F.end_of_local_day(F.local_date(dt)), now)
        if as_of > now + timedelta(minutes=5):
            raise RowError(f"as_of {as_of.isoformat()} is in the future", F.INVALID_DATE, "as_of")
    qty = v["quantity"]
    has_history = ctx.db.scalar(select(StockMovement.id).where(StockMovement.consumable_id == item.id).limit(1))
    if not has_history:
        # No ledger yet for this item → the hospital system's count becomes the opening balance (a normal receipt).
        if qty == 0:
            return "unchanged", "consumable", item.id
        expiry = v.get("expiry_date")
        if expiry and expiry < business_today():
            raise RowError(f"opening batch already expired ({expiry})", F.INVALID_DATE, "expiry_date")
        moves = _service(lambda: stock.receive(
            ctx.db, ctx.actor, item, qty, v.get("lot_number") or f"OPENING-{ctx.source.id}", expiry, None,
            float(v["unit_cost"]) if v.get("unit_cost") is not None else None,
            f"Opening balance: {ctx.source.name}"[:64], f"Opening balance from {ctx.source.name} (integration sync)"))
        ctx.touched_items.add(item.id)
        ctx.note("opening_balances")
        return "created", "stock_movement", moves[0].id
    medflow = balance_as_of(ctx.db, item, as_of)
    diff = qty - medflow
    issue = ctx.db.scalar(select(ReconciliationIssue).where(
        ReconciliationIssue.source_id == ctx.source.id, ReconciliationIssue.consumable_id == item.id,
        ReconciliationIssue.status == "OPEN"))
    as_of_day = F.local_date(as_of)
    if abs(diff) <= ctx.tolerance:
        if issue is None:
            return "unchanged", "consumable", item.id
        issue.status, issue.resolution, issue.resolved_at, issue.updated_at = "RESOLVED", "matched_later", now, now
        issue.note = f"Matched in a later snapshot ({as_of_day}): {ctx.source.name} {qty}, MedFlow {medflow}"
        ctx.note("reconciliation_matched")
        return "updated", "reconciliation_issue", issue.id
    if issue is not None:
        if (issue.external_quantity, issue.medflow_quantity, issue.as_of) == (qty, medflow, as_of_day):
            return "unchanged", "reconciliation_issue", issue.id
        issue.external_quantity, issue.medflow_quantity, issue.difference = qty, medflow, diff
        issue.as_of, issue.run_id, issue.updated_at = as_of_day, ctx.run.id, now
        ctx.note("reconciliation_updated")
        return "updated", "reconciliation_issue", issue.id
    issue = ReconciliationIssue(hospital_id=ctx.hid, source_id=ctx.source.id, run_id=ctx.run.id, consumable_id=item.id,
                                as_of=as_of_day, external_quantity=qty, medflow_quantity=medflow, difference=diff)
    ctx.db.add(issue)
    ctx.db.flush()
    ctx.note("reconciliation_opened")
    return "created", "reconciliation_issue", issue.id


# ---------------------------------------------------------------- transactions


def apply_consumption(ctx: Ctx, v: dict):
    item = _item(ctx, v["item_code"])
    dept = _department(ctx, v["department_code"])
    at = _event_time(ctx, v["occurred_at"], "occurred_at")
    _chronology(ctx, item, at, "occurred_at")
    moves = _service(lambda: stock.issue(ctx.db, ctx.actor, item, v["quantity"], dept, v["external_id"][:64],
                                         f"Consumption from {ctx.source.name}", at=at, today=F.local_date(at)))
    ctx.touched_items.add(item.id)
    return "created", "stock_movement", moves[0].id


def _order_by_reference(ctx: Ctx, po: str) -> SupplierOrder | None:
    return ctx.db.scalar(select(SupplierOrder).where(SupplierOrder.hospital_id == ctx.hid,
                                                     func.upper(SupplierOrder.reference) == po))


CLOSED_PO = {"CANCELLED", "CANCELED", "CLOSED", "DELETED", "REJECTED"}


def apply_purchase_order(ctx: Ctx, v: dict):
    from app.models import ExternalRef

    supplier, item = _supplier(ctx, v["supplier_code"]), _item(ctx, v["item_code"])
    ordered = v["order_date"]
    if ordered > business_today():
        raise RowError(f"order_date {ordered} is in the future", F.INVALID_DATE, "order_date")
    if v.get("expected_date") and v["expected_date"] < ordered:
        raise RowError("expected_date is before order_date", F.INVALID_DATE, "expected_date")
    status = (v.get("status") or "OPEN").upper()
    order = _order_by_reference(ctx, v["po_number"])
    if order is None:
        order = _service(lambda: supplier_orders.create_order(
            ctx.db, ctx.actor, ctx.hid, supplier, item, v["quantity"], v.get("unit_price"), ordered, v.get("expected_date"),
            reference=v["po_number"], notes=f"Imported from {ctx.source.name}"))
        if status in CLOSED_PO:
            _service(lambda: supplier_orders.cancel_or_close(ctx.db, order, max(ordered, business_today()),
                                                             f"{status.title()} in {ctx.source.name}"))
        ctx.touched_items.add(item.id)
        return "created", "supplier_order", order.id
    mine = ctx.db.scalar(select(ExternalRef.id).where(ExternalRef.source_id == ctx.source.id,
                                                     ExternalRef.entity == "purchase_orders",
                                                     ExternalRef.target_id == order.id))
    if not mine:
        raise RowError(f"PO {v['po_number']} already exists in MedFlow and was not created by this source", F.CONFLICT,
                       "po_number")
    if order.supplier_id != supplier.id or order.consumable_id != item.id:
        raise RowError(f"PO {v['po_number']}: the supplier or item of an existing order cannot change", F.CONFLICT)
    active = order.status in ACTIVE_ORDER_STATUSES
    changes = {}
    if v["quantity"] != order.quantity_ordered:
        changes["quantity_ordered"] = v["quantity"]
    if v.get("expected_date") and v["expected_date"] != order.expected_date:
        changes["expected_date"] = v["expected_date"]
    if v.get("unit_price") is not None and v["unit_price"] != order.unit_price:
        if order.quantity_received:
            raise RowError(f"PO {v['po_number']}: price cannot change after deliveries were received", F.CONFLICT, "unit_price")
        changes["unit_price"] = v["unit_price"]
    if changes and not active:
        raise RowError(f"PO {v['po_number']} is already {order.status.lower()} in MedFlow; it can't be changed", F.CONFLICT)
    if "quantity_ordered" in changes and changes["quantity_ordered"] < order.quantity_received:
        raise RowError(f"PO {v['po_number']}: quantity {changes['quantity_ordered']} is below what was already received "
                       f"({order.quantity_received})", F.CONFLICT, "quantity")
    _set(order, changes)
    if "quantity_ordered" in changes and order.quantity_received >= order.quantity_ordered:
        order.status, order.completed_date = SupplierOrderStatus.RECEIVED, order.first_delivery_date or business_today()
    if status in CLOSED_PO and order.status in ACTIVE_ORDER_STATUSES:
        _service(lambda: supplier_orders.cancel_or_close(ctx.db, order, max(order.ordered_date, business_today()),
                                                         f"{status.title()} in {ctx.source.name}"))
        changes["status"] = status
    ctx.db.flush()
    if changes:
        ctx.touched_items.add(item.id)
    return ("updated" if changes else "unchanged"), "supplier_order", order.id


def apply_delivery(ctx: Ctx, v: dict):
    order = _order_by_reference(ctx, v["po_number"])
    if order is None:
        raise RowError(f"unknown purchase order '{v['po_number']}' (send purchase orders first)", F.UNKNOWN_ORDER, "po_number")
    item = order.consumable
    if v.get("item_code") and v["item_code"] != item.sku.upper():
        raise RowError(f"PO {order.reference} is for {item.sku}, not {v['item_code']}", F.CONFLICT, "item_code")
    if order.status not in ACTIVE_ORDER_STATUSES:
        raise RowError(f"PO {order.reference} is {order.status.lower()} — it can't receive deliveries", F.CONFLICT, "po_number")
    at = _event_time(ctx, v["received_at"], "received_at")
    day = F.local_date(at)
    if day < order.ordered_date:
        raise RowError("received_at is before the order date", F.INVALID_DATE, "received_at")
    if v.get("expiry_date") and v["expiry_date"] < day:
        raise RowError(f"batch expired ({v['expiry_date']}) before it was delivered", F.INVALID_DATE, "expiry_date")
    _chronology(ctx, item, at, "received_at")
    price = v.get("unit_price") if v.get("unit_price") is not None else order.unit_price
    moves = _service(lambda: stock.receive(
        ctx.db, ctx.actor, item, v["quantity"], v["lot_number"], v.get("expiry_date"), order.supplier, float(price),
        v["external_id"][:64], f"Delivery against {order.reference} from {ctx.source.name}", at=at))
    _service(lambda: supplier_orders.record_delivery(ctx.db, order, day, v["quantity"], price, moves[0]))
    ctx.touched_items.add(item.id)
    return "created", "stock_movement", moves[0].id


# ---------------------------------------------------------------- registry

ACTIVE = Field("active", "bool", description="false = deactivate in MedFlow")

ENTITIES: dict[str, Entity] = {e.name: e for e in [
    Entity("departments", "Departments", (
        Field("code", "code", True, "Department / cost-centre code", 32),
        Field("name", "text", True, "Department name", 120),
        Field("description", "text"), ACTIVE, UPDATED_AT,
    ), key=lambda v: v["code"], apply=apply_department, description="Master data, upserted by code."),
    Entity("suppliers", "Suppliers", (
        Field("code", "code", True, "Supplier / vendor code", 32),
        Field("name", "text", True, "Supplier name", 200),
        Field("city", "text", max_len=100), Field("email", "text", max_len=255), Field("phone", "text", max_len=32),
        Field("contact_person", "text"), Field("default_lead_time_days", "int", min_value=0, description="Quoted lead time"),
        ACTIVE, UPDATED_AT,
    ), key=lambda v: v["code"], apply=apply_supplier, description="Master data, upserted by code."),
    Entity("items", "Items", (
        Field("code", "code", True, "Material / item code (MedFlow SKU)", 64),
        Field("name", "text", True, "Item description", 200),
        Field("unit", "text", True, "Base unit of measure", 32),
        Field("category", "text", max_len=120, description="Created if it does not exist"),
        Field("unit_cost", "money"), Field("reorder_level", "int", min_value=0), Field("max_level", "int", min_value=0),
        Field("description", "text", max_len=2000), ACTIVE, UPDATED_AT,
    ), key=lambda v: v["code"], apply=apply_item, description="Master data, upserted by item code."),
    Entity("supplier_items", "Supplier catalogue", (
        Field("supplier_code", "code", True, max_len=32), Field("item_code", "code", True, max_len=64),
        Field("unit_price", "money", True), Field("lead_time_days", "int", min_value=0), Field("moq", "int", min_value=1),
        Field("supplier_sku", "text", max_len=64), Field("preferred", "bool"), UPDATED_AT,
    ), key=lambda v: f"{v['supplier_code']}/{v['item_code']}", apply=apply_supplier_item,
        description="Price, lead time and MOQ per supplier and item (used by V4/V5)."),
    Entity("purchase_orders", "Purchase orders", (
        Field("po_number", "code", True, "Becomes the supplier order's reference", 64),
        Field("supplier_code", "code", True, max_len=32), Field("item_code", "code", True, max_len=64),
        Field("quantity", "int", True, min_value=1), Field("order_date", "date", True),
        Field("expected_date", "date"), Field("unit_price", "money"),
        Field("status", "text", max_len=16, description="open (default) | cancelled | closed"), UPDATED_AT,
    ), key=lambda v: v["po_number"], apply=apply_purchase_order,
        description="One item per PO line. Recorded in the V4 order log — MedFlow never sends orders anywhere."),
    Entity("deliveries", "Delivery records", (
        Field("external_id", "text", True, "Goods-receipt / delivery number", 128),
        Field("po_number", "code", True, max_len=64), Field("quantity", "int", True, min_value=1),
        Field("received_at", "datetime", True), Field("lot_number", "text", True, max_len=64),
        Field("expiry_date", "date"), Field("unit_price", "money"), Field("item_code", "code", max_len=64), UPDATED_AT,
    ), key=lambda v: v["external_id"], apply=apply_delivery, transactional=True, sort_key="received_at",
        description="Received through the stock ledger against the purchase order (V1 receipt + V4 delivery)."),
    Entity("consumption", "Consumption", (
        Field("external_id", "text", True, "Transaction id in the source system", 128),
        Field("item_code", "code", True, max_len=64), Field("department_code", "code", True, max_len=32),
        Field("quantity", "int", True, min_value=1), Field("occurred_at", "datetime", True), UPDATED_AT,
    ), key=lambda v: v["external_id"], apply=apply_consumption, transactional=True, sort_key="occurred_at",
        description="Issued to the department through the stock ledger (FEFO) — the V2 forecasting signal."),
    Entity("inventory", "Inventory (stock counts)", (
        Field("item_code", "code", True, max_len=64), Field("quantity", "int", True, min_value=0),
        Field("as_of", "datetime", description="When the count was taken (default: now)"),
        Field("lot_number", "text", max_len=64, description="Opening balance only"),
        Field("expiry_date", "date", description="Opening balance only"), Field("unit_cost", "money"), UPDATED_AT,
    ), key=lambda v: f"{v['item_code']}@{(F.local_date(v['as_of'][0]) if v.get('as_of') else business_today()).isoformat()}",
        apply=apply_inventory,
        description="Compared with the ledger: differences become reconciliation issues. Items with no ledger yet get "
                    "an opening balance."),
]}

# Apply order when a source sends several entities (master data first, snapshots last).
ORDER = ["departments", "suppliers", "items", "supplier_items", "purchase_orders", "deliveries", "consumption", "inventory"]
assert sorted(ORDER) == sorted(ENTITIES)
