"""V8 — hospital-scoped CSV import for onboarding (departments, suppliers, items, opening stock).

Rules: rows always go into the caller's ACTIVE hospital (there is no hospital column to fill in); every row is
validated before anything is written; one bad row ⇒ nothing is written and every error is reported with its line
number; existing records are never overwritten (a code / SKU that already exists is an error); `dry_run` validates
without writing. Opening stock goes through the V1 stock ledger (`stock.receive`), like any receipt.
"""

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Consumable, ConsumableCategory, Department, Supplier, User
from app.services import stock

MAX_ROWS = 2000

COLUMNS = {
    "departments": (["code", "name"], ["description"]),
    "suppliers": (["code", "name"], ["city", "default_lead_time_days", "email", "phone", "contact_person"]),
    "items": (["sku", "name", "unit"], ["category", "unit_cost", "reorder_level", "max_level", "description"]),
    "opening_stock": (["sku", "lot_number", "quantity"], ["expiry_date", "unit_cost", "supplier_code"]),
}


class ImportError_(ValueError):  # noqa: N801 — avoid shadowing the builtin ImportError
    pass


def _int(v: str, name: str, lo: int = 0, hi: int = 10_000_000) -> int:
    try:
        x = int(v)
    except ValueError as e:
        raise ImportError_(f"{name} must be a whole number") from e
    if not lo <= x <= hi:
        raise ImportError_(f"{name} must be between {lo} and {hi}")
    return x


def _money(v: str, name: str) -> Decimal:
    try:
        x = Decimal(v)
    except InvalidOperation as e:
        raise ImportError_(f"{name} must be a number") from e
    if x < 0 or x > Decimal("10000000"):
        raise ImportError_(f"{name} out of range")
    return x.quantize(Decimal("0.01"))


def _code(v: str, name: str, n: int = 32) -> str:
    v = v.strip().upper()
    if not v or len(v) > n or not all(c.isalnum() or c in "-_" for c in v):
        raise ImportError_(f"{name} must be 1–{n} letters, digits, '-' or '_'")
    return v


def parse(text: str, kind: str) -> list[dict]:
    if kind not in COLUMNS:
        raise ImportError_(f"Unknown import type '{kind}'")
    reader = csv.DictReader(io.StringIO(text.strip()))
    required, optional = COLUMNS[kind]
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in required if c not in header]
    if missing:
        raise ImportError_(f"Missing column(s): {', '.join(missing)}. Expected: {', '.join(required + optional)}")
    unknown = [c for c in header if c not in required + optional]
    if unknown:
        raise ImportError_(f"Unknown column(s): {', '.join(unknown)}")
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
    if not rows:
        raise ImportError_("The file has no data rows")
    if len(rows) > MAX_ROWS:
        raise ImportError_(f"At most {MAX_ROWS} rows per import")
    return rows


def run(db: Session, user: User, kind: str, text: str, dry_run: bool) -> dict:
    """Validate (and unless dry_run, write) one CSV into the user's active hospital. All-or-nothing."""
    hid = user.hospital_id
    rows = parse(text, kind)
    errors: list[dict] = []
    plan: list[tuple[int, dict]] = []
    seen: set[str] = set()
    created_categories: set[str] = set()
    cats = {c.name.lower(): c for c in db.scalars(select(ConsumableCategory).where(ConsumableCategory.hospital_id == hid))}
    key_cols = {"departments": ("code",), "suppliers": ("code",), "items": ("sku",), "opening_stock": ("sku", "lot_number")}
    for i, r in enumerate(rows, start=2):  # line 1 = header
        try:
            raw_key = "/".join(r.get(c, "").strip().upper() for c in key_cols[kind])
            if raw_key in seen:
                raise ImportError_(f"duplicate {raw_key} in this file")
            seen.add(raw_key)
            _key, val = _validate(db, hid, kind, r, cats, created_categories)
            plan.append((i, val))
        except ImportError_ as e:
            errors.append({"line": i, "error": str(e), "row": r})
    result = {"kind": kind, "rows": len(rows), "valid": len(plan), "errors": errors, "dry_run": dry_run,
              "created": 0, "created_categories": sorted(created_categories)}
    if errors or dry_run:
        return result
    for _line, val in plan:
        _write(db, user, kind, val, cats)
    db.flush()
    result["created"] = len(plan)
    return result


def _validate(db: Session, hid: int, kind: str, r: dict, cats: dict, new_cats: set) -> tuple[str, dict]:
    if kind == "departments":
        code = _code(r["code"], "code")
        if not r["name"]:
            raise ImportError_("name is required")
        if db.scalar(select(Department.id).where(Department.hospital_id == hid, Department.code == code)):
            raise ImportError_(f"department {code} already exists (existing records are never overwritten)")
        return code, {"code": code, "name": r["name"][:120], "description": r.get("description") or None}
    if kind == "suppliers":
        code = _code(r["code"], "code")
        if not r["name"]:
            raise ImportError_("name is required")
        if db.scalar(select(Supplier.id).where(Supplier.hospital_id == hid, Supplier.code == code)):
            raise ImportError_(f"supplier {code} already exists (existing records are never overwritten)")
        lead = _int(r["default_lead_time_days"], "default_lead_time_days", 0, 365) if r.get("default_lead_time_days") else 7
        return code, {"code": code, "name": r["name"][:200], "city": r.get("city") or None, "default_lead_time_days": lead,
                      "email": r.get("email") or None, "phone": r.get("phone") or None,
                      "contact_person": r.get("contact_person") or None}
    if kind == "items":
        sku = _code(r["sku"], "sku", 64)
        if not r["name"] or not r["unit"]:
            raise ImportError_("name and unit are required")
        if db.scalar(select(Consumable.id).where(Consumable.hospital_id == hid, Consumable.sku == sku)):
            raise ImportError_(f"item {sku} already exists (existing records are never overwritten)")
        cat = (r.get("category") or "").strip()
        if cat and cat.lower() not in cats:
            new_cats.add(cat)
        return sku, {"sku": sku, "name": r["name"][:200], "unit": r["unit"][:32], "category": cat or None,
                     "unit_cost": _money(r["unit_cost"], "unit_cost") if r.get("unit_cost") else Decimal("0"),
                     "reorder_level": _int(r["reorder_level"], "reorder_level") if r.get("reorder_level") else 0,
                     "max_level": _int(r["max_level"], "max_level") if r.get("max_level") else None,
                     "description": r.get("description") or None}
    # opening stock
    sku = _code(r["sku"], "sku", 64)
    item = db.scalar(select(Consumable).where(Consumable.hospital_id == hid, Consumable.sku == sku))
    if item is None:
        raise ImportError_(f"item {sku} not found in this hospital (import items first)")
    lot = r["lot_number"].strip()
    if not lot or len(lot) > 64:
        raise ImportError_("lot_number is required (≤ 64 characters)")
    qty = _int(r["quantity"], "quantity", 1)
    expiry = None
    if r.get("expiry_date"):
        try:
            expiry = date.fromisoformat(r["expiry_date"])
        except ValueError as e:
            raise ImportError_("expiry_date must be YYYY-MM-DD") from e
    supplier = None
    if r.get("supplier_code"):
        supplier = db.scalar(select(Supplier).where(Supplier.hospital_id == hid,
                                                    func.upper(Supplier.code) == r["supplier_code"].strip().upper()))
        if supplier is None:
            raise ImportError_(f"supplier {r['supplier_code']} not found in this hospital")
    cost = float(_money(r["unit_cost"], "unit_cost")) if r.get("unit_cost") else None
    return f"{sku}/{lot}", {"item": item, "lot": lot, "qty": qty, "expiry": expiry, "supplier": supplier, "cost": cost}


def _write(db: Session, user: User, kind: str, v: dict, cats: dict) -> None:
    hid = user.hospital_id
    if kind == "departments":
        db.add(Department(hospital_id=hid, **v))
    elif kind == "suppliers":
        db.add(Supplier(hospital_id=hid, notes="Imported (CSV onboarding)", **v))
    elif kind == "items":
        cat_name = v.pop("category")
        cat = None
        if cat_name:
            cat = cats.get(cat_name.lower())
            if cat is None:
                cat = ConsumableCategory(hospital_id=hid, name=cat_name[:120])
                db.add(cat)
                db.flush()
                cats[cat_name.lower()] = cat
        db.add(Consumable(hospital_id=hid, category_id=cat.id if cat else None, **v))
    else:
        stock.receive(db, user, v["item"], v["qty"], v["lot"], v["expiry"], v["supplier"], v["cost"], "OPENING-IMPORT",
                      "Opening balance (CSV import)")
