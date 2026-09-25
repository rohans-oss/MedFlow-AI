"""V9 — field definitions, value parsing and row errors (shared by every entity and connector)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.core.config import settings

# Reason codes (counted per run in sync_runs.error_summary["reasons"])
MISSING_FIELD = "missing_field"
INVALID_VALUE = "invalid_value"
INVALID_DATE = "invalid_date"
NEGATIVE_QUANTITY = "negative_quantity"
UNKNOWN_ITEM = "unknown_item"
UNKNOWN_SUPPLIER = "unknown_supplier"
UNKNOWN_DEPARTMENT = "unknown_department"
INVALID_DEPARTMENT = "invalid_department"
UNKNOWN_ORDER = "unknown_purchase_order"
DUPLICATE = "duplicate_record"
CHANGED_TRANSACTION = "changed_transaction"
OUT_OF_ORDER = "out_of_order"
INSUFFICIENT_STOCK = "insufficient_stock"
CONFLICT = "conflict"


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # code | text | int | money | date | datetime | bool
    required: bool = False
    description: str = ""
    max_len: int = 200
    min_value: int | None = None


class RowError(Exception):
    """One or more problems with one incoming record. `errors` = [{"field", "code", "message"}]."""

    def __init__(self, errors: list[dict] | str, code: str = INVALID_VALUE, field: str | None = None):
        if isinstance(errors, str):
            errors = [{"field": field, "code": code, "message": errors}]
        super().__init__("; ".join(e["message"] for e in errors))
        self.errors = errors


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.TIMEZONE)


def blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def parse_date(v, fmt: str | None) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if fmt:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(s[:10])
    except ValueError as e:
        raise ValueError(f"'{s}' is not a date ({fmt or 'YYYY-MM-DD'})") from e


def parse_datetime(v, fmt: str | None) -> tuple[datetime, bool]:
    """→ (aware UTC datetime, has_time). A bare date is returned as local midnight with has_time=False."""
    if isinstance(v, datetime):
        dt, has_time = v, True
    elif isinstance(v, date):
        dt, has_time = datetime.combine(v, time()), False
    else:
        s = str(v).strip()
        dt = None
        has_time = True
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            has_time = len(s) > 10
        except ValueError:
            if fmt:
                for f in (fmt, f"{fmt} %H:%M", f"{fmt} %H:%M:%S"):
                    try:
                        dt = datetime.strptime(s, f)
                        has_time = f != fmt
                        break
                    except ValueError:
                        continue
        if dt is None:
            raise ValueError(f"'{s}' is not a date/time (ISO 8601{', or ' + fmt if fmt else ''})")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())  # naive times are hospital-local
    return dt.astimezone(UTC), has_time


def parse_value(f: Field, v, date_format: str | None):
    """Parse one mapped value. Raises ValueError with a readable message."""
    if f.kind in ("code",):
        s = str(v).strip().upper()
        if len(s) > f.max_len or not all(c.isalnum() or c in "-_./" for c in s):
            raise ValueError(f"must be 1–{f.max_len} letters, digits, '-', '_', '.' or '/'")
        return s
    if f.kind == "text":
        s = str(v).strip()
        return s[: f.max_len]
    if f.kind == "int":
        try:
            d = Decimal(str(v).strip().replace(",", ""))
        except InvalidOperation as e:
            raise ValueError("must be a whole number") from e
        if d != d.to_integral_value():
            raise ValueError("must be a whole number")
        x = int(d)
        if f.min_value is not None and x < f.min_value:
            raise ValueError("must not be negative" if f.min_value == 0 else f"must be at least {f.min_value}")
        if abs(x) > 100_000_000:
            raise ValueError("is out of range")
        return x
    if f.kind == "money":
        try:
            d = Decimal(str(v).strip().replace(",", ""))
        except InvalidOperation as e:
            raise ValueError("must be a number") from e
        if not d.is_finite() or d < 0 or d > Decimal("10000000"):
            raise ValueError("must be between 0 and 10,000,000")
        return d.quantize(Decimal("0.01"))
    if f.kind == "date":
        return parse_date(v, date_format)
    if f.kind == "datetime":
        return parse_datetime(v, date_format)
    if f.kind == "bool":
        s = str(v).strip().lower()
        if s in ("1", "true", "yes", "y", "active", "x"):
            return True
        if s in ("0", "false", "no", "n", "inactive", "blocked", ""):
            return False
        raise ValueError("must be true/false")
    raise AssertionError(f.kind)


def local_date(dt: datetime) -> date:
    return dt.astimezone(_tz()).date()


def end_of_local_day(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=_tz()).astimezone(UTC)
