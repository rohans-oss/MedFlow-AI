"""V9 — reference ERP simulator: a LOCAL, clearly labelled stand-in for a hospital ERP, used to exercise and demonstrate
the `rest_pull` connector end to end over real HTTP. It is NOT a hospital system and MedFlow is not connected to one.

* Off by default (`REFERENCE_ERP_ENABLED=false` → 404), requires a bearer token (`REFERENCE_ERP_TOKEN`), and only serves
  demo hospitals (`hospitals.is_demo`) — it never exposes a real hospital's data.
* It speaks an ERP-like vocabulary (material_code, vendor_code, cost_center, po_number, grn_number…) so the field
  mapping layer is actually exercised.
* Its master data mirrors the demo hospital's MedFlow catalogue; on top it injects, once per hospital per day, a few
  labelled changes: a new vendor (HLS) and material (GLV-NIT-L) with a catalogue price, an ERP price for CAP-BOUF,
  three consumption postings, one purchase order with a partial goods receipt, stock counts that differ from the ledger
  for two items (+30 GLV-EXM-M, −12 SYR-5ML) and two deliberately invalid postings (unknown material, negative quantity)
  so validation and reconciliation can be seen working.
* The daily feed is generated on first request and kept in memory (restarting the API regenerates it).
"""

import hmac
import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import business_today
from app.db.base import utcnow
from app.db.session import get_db
from app.integrations.entities import balance_as_of
from app.models import (
    Consumable,
    ConsumableCategory,
    Department,
    Hospital,
    StockBatch,
    Supplier,
    SupplierProduct,
)

RESOURCES = ("departments", "vendors", "materials", "vendor-materials", "stock", "consumption", "purchase-orders",
             "deliveries")
NEW_VENDOR = ("HLS", "Healthline Surgical Supplies (simulated)")
NEW_MATERIAL = ("GLV-NIT-L", "Examination gloves, nitrile, large", "pair")
PRICE_ITEM = "CAP-BOUF"
DELTAS = {"GLV-EXM-M": 30, "SYR-5ML": -12}
CONSUME = [("GLV-EXM-M", 20), ("SYR-5ML", 15)]

_cache: dict[tuple[int, date], dict] = {}


def reset_cache() -> None:
    _cache.clear()


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _st(active: bool) -> str:
    return "active" if active else "blocked"


def erp_price(p: Decimal) -> Decimal:
    """The ERP's list price rule (x.99) — a fixed point, so re-syncing never compounds."""
    return Decimal(math.floor(p)) + Decimal("0.99")


def build(db: Session, hospital: Hospital, now: datetime | None = None) -> dict:
    now = (now or utcnow()).replace(microsecond=0)
    today = business_today()
    base_t, t = _iso(now - timedelta(days=1)), _iso(now)
    ymd = today.strftime("%y%m%d")
    hid = hospital.id
    depts = db.scalars(select(Department).where(Department.hospital_id == hid).order_by(Department.code)).all()
    sups = db.scalars(select(Supplier).where(Supplier.hospital_id == hid).order_by(Supplier.code)).all()
    items = db.scalars(select(Consumable).where(Consumable.hospital_id == hid).order_by(Consumable.sku)).all()
    cats = {c.id: c.name for c in db.scalars(select(ConsumableCategory).where(ConsumableCategory.hospital_id == hid))}
    sps = db.execute(select(SupplierProduct, Supplier.code, Consumable.sku)
                     .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
                     .join(Consumable, Consumable.id == SupplierProduct.consumable_id)
                     .where(Supplier.hospital_id == hid).order_by(Supplier.code, Consumable.sku)).all()
    usable = dict(db.execute(select(StockBatch.consumable_id, func.sum(StockBatch.quantity))
                             .join(Consumable, Consumable.id == StockBatch.consumable_id)
                             .where(Consumable.hospital_id == hid, (StockBatch.expiry_date.is_(None))
                                    | (StockBatch.expiry_date >= today)).group_by(StockBatch.consumable_id)).all())
    by_sku = {c.sku: c for c in items}
    feed: dict[str, list[dict]] = {r: [] for r in RESOURCES}
    feed["departments"] = [{"cost_center": d.code, "cost_center_name": d.name, "status": _st(d.is_active),
                            "updated_at": base_t} for d in depts]
    feed["vendors"] = [{"vendor_code": s.code, "vendor_name": s.name, "city": s.city, "lead_days": s.default_lead_time_days,
                        "email": s.email, "phone": s.phone, "status": _st(s.is_active), "updated_at": base_t} for s in sups]
    for c in items:
        price, upd = c.unit_cost, base_t
        if c.sku == PRICE_ITEM and erp_price(c.unit_cost) != c.unit_cost:
            price, upd = erp_price(c.unit_cost), t
        feed["materials"].append({"material_code": c.sku, "material_description": c.name, "uom": c.unit,
                                  "material_group": cats.get(c.category_id), "std_price": str(price),
                                  "reorder_point": c.reorder_level, "max_stock": c.max_level, "status": _st(c.is_active),
                                  "updated_at": upd})
    feed["vendor-materials"] = [{"vendor_code": code, "material_code": sku, "net_price": str(sp.unit_price),
                                 "lead_days": sp.lead_time_days, "min_order_qty": sp.moq, "updated_at": base_t}
                                for sp, code, sku in sps]
    # ---- injected changes (labelled simulated)
    if NEW_VENDOR[0] not in {s.code for s in sups}:
        feed["vendors"].append({"vendor_code": NEW_VENDOR[0], "vendor_name": NEW_VENDOR[1], "city": hospital.city,
                                "lead_days": 5, "email": None, "phone": None, "status": "active", "updated_at": t})
    gloves_group = next((n for n in cats.values() if "glove" in n.lower()), None)
    if NEW_MATERIAL[0] not in by_sku:
        feed["materials"].append({"material_code": NEW_MATERIAL[0], "material_description": NEW_MATERIAL[1],
                                  "uom": NEW_MATERIAL[2], "material_group": gloves_group, "std_price": "7.20",
                                  "reorder_point": 150, "max_stock": 900, "status": "active", "updated_at": t})
    if not any(code == NEW_VENDOR[0] and sku == NEW_MATERIAL[0] for _sp, code, sku in sps):
        feed["vendor-materials"].append({"vendor_code": NEW_VENDOR[0], "material_code": NEW_MATERIAL[0], "net_price": "6.90",
                                         "lead_days": 5, "min_order_qty": 50, "updated_at": t})
    consumed: dict[str, int] = {}
    active_depts = [d.code for d in depts if d.is_active]
    targets = [(s, q) for s, q in CONSUME if s in by_sku]
    extra = next((c.sku for c in items if c.is_active and c.sku not in dict(CONSUME) and usable.get(c.id, 0) >= 40), None)
    if extra:
        targets.append((extra, 10))
    n = 0
    for i, (sku, want) in enumerate(targets):
        q = min(want, int(usable.get(by_sku[sku].id, 0)) // 3)
        if q <= 0 or not active_depts:
            continue
        n += 1
        consumed[sku] = consumed.get(sku, 0) + q
        feed["consumption"].append({"txn_id": f"TXN-{hospital.code}-{ymd}-{n:03d}", "material_code": sku,
                                    "cost_center": active_depts[i % len(active_depts)], "qty": q, "posted_at": t,
                                    "updated_at": t})
    if active_depts:  # two postings the ERP should never have sent (validation demo)
        feed["consumption"].append({"txn_id": f"TXN-{hospital.code}-{ymd}-901", "material_code": "MAT-00999",
                                    "cost_center": active_depts[0], "qty": 5, "posted_at": t, "updated_at": t})
        if "SYR-5ML" in by_sku:
            feed["consumption"].append({"txn_id": f"TXN-{hospital.code}-{ymd}-902", "material_code": "SYR-5ML",
                                        "cost_center": active_depts[0], "qty": -3, "posted_at": t, "updated_at": t})
    po = f"PO45{ymd}{hid % 100:02d}"
    feed["purchase-orders"].append({"po_number": po, "vendor_code": NEW_VENDOR[0], "material_code": NEW_MATERIAL[0],
                                    "order_qty": 200, "net_price": "6.90", "po_date": today.isoformat(),
                                    "delivery_date": (today + timedelta(days=5)).isoformat(), "po_status": "OPEN",
                                    "updated_at": t})
    feed["deliveries"].append({"grn_number": f"GRN-{ymd}-{hid % 100:02d}01", "po_number": po,
                               "material_code": NEW_MATERIAL[0], "received_qty": 80, "received_on": t,
                               "batch": f"HLS-{ymd}", "expiry": (today + timedelta(days=730)).isoformat(), "updated_at": t})
    received = {NEW_MATERIAL[0]: 80}
    snap = _iso(now + timedelta(seconds=1))
    for c in items:
        if not c.is_active:
            continue
        qty = balance_as_of(db, c, now + timedelta(seconds=1)) - consumed.get(c.sku, 0) + received.get(c.sku, 0)
        if c.sku in DELTAS and qty + DELTAS[c.sku] >= 0:
            qty += DELTAS[c.sku]
        feed["stock"].append({"material_code": c.sku, "qty_on_hand": qty, "snapshot_at": snap, "updated_at": t})
    if NEW_MATERIAL[0] not in by_sku:
        feed["stock"].append({"material_code": NEW_MATERIAL[0], "qty_on_hand": 80, "snapshot_at": snap, "updated_at": t})
    return {"generated_at": t, "feed": feed}


def feed_for(db: Session, hospital: Hospital) -> dict:
    key = (hospital.id, business_today())
    if key not in _cache:
        _cache[key] = build(db, hospital)
    return _cache[key]


# ---------------------------------------------------------------- HTTP

router = APIRouter(prefix="/reference-erp", tags=["reference-erp (simulated)"])


def _guard(request: Request) -> None:
    if not settings.REFERENCE_ERP_ENABLED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not settings.REFERENCE_ERP_TOKEN:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Set REFERENCE_ERP_TOKEN to use the reference ERP simulator")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:].encode(), settings.REFERENCE_ERP_TOKEN.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token", headers={"WWW-Authenticate": "Bearer"})


def _hospital(db: Session, code: str) -> Hospital:
    h = db.scalar(select(Hospital).where(Hospital.code == code))
    if h is None or not h.is_demo:  # only demo hospitals: the simulator never serves real hospital data
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown plant")
    return h


@router.get("/{hospital_code}/health", dependencies=[Depends(_guard)])
def health(hospital_code: str, db: Session = Depends(get_db)):
    h = _hospital(db, hospital_code)
    return {"system": "MedFlow reference ERP (SIMULATED)", "simulated": True, "plant": h.code,
            "generated_at": feed_for(db, h)["generated_at"], "resources": list(RESOURCES)}


@router.get("/{hospital_code}/{resource}", dependencies=[Depends(_guard)])
def resource(hospital_code: str, resource: str, db: Session = Depends(get_db), page: int = Query(1, ge=1),
             page_size: int = Query(200, ge=1, le=1000), updated_since: str | None = None):
    if resource not in RESOURCES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown resource")
    h = _hospital(db, hospital_code)
    rows = feed_for(db, h)["feed"][resource]
    if updated_since:
        try:
            since = datetime.fromisoformat(updated_since.replace("Z", "+00:00"))
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "updated_since must be ISO 8601") from e
        rows = [r for r in rows if datetime.fromisoformat(r["updated_at"]) >= since]
    rows = sorted(rows, key=lambda r: r["updated_at"])  # stable: ascending change time
    start = (page - 1) * page_size
    chunk = rows[start:start + page_size]
    return {"items": chunk, "next_page": page + 1 if start + page_size < len(rows) else None, "total": len(rows),
            "simulated": True}


# Field mappings for the simulator's vocabulary (MedFlow field → ERP field): used by the demo seed, tests and docs.
MAPPINGS: dict[str, dict] = {
    "departments": {"code": "cost_center", "name": "cost_center_name", "active": "status"},
    "suppliers": {"code": "vendor_code", "name": "vendor_name", "default_lead_time_days": "lead_days", "active": "status"},
    "items": {"code": "material_code", "name": "material_description", "unit": "uom", "category": "material_group",
              "unit_cost": "std_price", "reorder_level": "reorder_point", "max_level": "max_stock", "active": "status"},
    "supplier_items": {"supplier_code": "vendor_code", "item_code": "material_code", "unit_price": "net_price",
                       "lead_time_days": "lead_days", "moq": "min_order_qty"},
    "purchase_orders": {"supplier_code": "vendor_code", "item_code": "material_code", "quantity": "order_qty",
                        "unit_price": "net_price", "order_date": "po_date", "expected_date": "delivery_date",
                        "status": "po_status"},
    "deliveries": {"external_id": "grn_number", "item_code": "material_code", "quantity": "received_qty",
                   "received_at": "received_on", "lot_number": "batch", "expiry_date": "expiry"},
    "consumption": {"external_id": "txn_id", "item_code": "material_code", "department_code": "cost_center",
                    "quantity": "qty", "occurred_at": "posted_at"},
    "inventory": {"item_code": "material_code", "quantity": "qty_on_hand", "as_of": "snapshot_at"},
}
RESOURCE_OF = {"departments": "departments", "suppliers": "vendors", "items": "materials",
               "supplier_items": "vendor-materials", "purchase_orders": "purchase-orders", "deliveries": "deliveries",
               "consumption": "consumption", "inventory": "stock"}
