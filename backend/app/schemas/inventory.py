from datetime import date, datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import AlertSeverity, AlertStatus, AlertType, MovementType, StockStatus
from app.schemas.common import ORMModel

# ---------- Categories ----------


class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = None


class CategoryOut(ORMModel):
    id: int
    name: str
    description: str | None


# ---------- Consumables ----------


class ConsumableBase(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    category_id: int | None = None
    description: str | None = None
    unit: str = Field("unit", min_length=1, max_length=32)
    unit_cost: float = Field(0, ge=0)
    reorder_level: int = Field(0, ge=0)
    max_level: int | None = Field(None, ge=0)
    is_active: bool = True


class ConsumableCreate(ConsumableBase):
    sku: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")

    @field_validator("sku")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class ConsumableUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    category_id: int | None = None
    description: str | None = None
    unit: str | None = Field(None, min_length=1, max_length=32)
    unit_cost: float | None = Field(None, ge=0)
    reorder_level: int | None = Field(None, ge=0)
    max_level: int | None = Field(None, ge=0)
    is_active: bool | None = None


class ConsumableOut(ORMModel):
    id: int
    sku: str
    name: str
    description: str | None
    unit: str
    unit_cost: float
    reorder_level: int
    max_level: int | None
    is_active: bool
    category: CategoryOut | None


# ---------- Suppliers ----------


class SupplierBase(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    contact_person: str | None = Field(None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=32)
    address: str | None = None
    city: str | None = Field(None, max_length=100)
    gstin: str | None = Field(None, pattern=r"^[0-9A-Z]{15}$")
    default_lead_time_days: int = Field(7, ge=0, le=365)
    notes: str | None = None
    is_active: bool = True

    @field_validator("email", "gstin", "phone", "contact_person", "city", "address", "notes", mode="before")
    @classmethod
    def blank_to_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


class SupplierCreate(SupplierBase):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("code")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class SupplierUpdate(SupplierBase):
    name: str | None = Field(None, min_length=2, max_length=200)  # type: ignore[assignment]
    default_lead_time_days: int | None = Field(None, ge=0, le=365)  # type: ignore[assignment]
    is_active: bool | None = None  # type: ignore[assignment]


class SupplierOut(ORMModel):
    id: int
    code: str
    name: str
    contact_person: str | None
    email: str | None
    phone: str | None
    address: str | None
    city: str | None
    gstin: str | None
    default_lead_time_days: int
    notes: str | None
    is_active: bool


class SupplierListItem(SupplierOut):
    product_count: int = 0
    receipts_90d: int = 0
    last_receipt_at: datetime | None = None


class SupplierProductIn(BaseModel):
    consumable_id: int
    supplier_sku: str | None = Field(None, max_length=64)
    unit_price: float = Field(gt=0)
    lead_time_days: int = Field(7, ge=0, le=365)
    moq: int = Field(1, ge=1)
    is_preferred: bool = False


class SupplierProductUpdate(BaseModel):
    supplier_sku: str | None = Field(None, max_length=64)
    unit_price: float | None = Field(None, gt=0)
    lead_time_days: int | None = Field(None, ge=0, le=365)
    moq: int | None = Field(None, ge=1)
    is_preferred: bool | None = None


class SupplierRef(ORMModel):
    id: int
    code: str
    name: str


class ConsumableRef(ORMModel):
    id: int
    sku: str
    name: str
    unit: str


class SupplierProductOut(ORMModel):
    id: int
    supplier_sku: str | None
    unit_price: float
    lead_time_days: int
    moq: int
    is_preferred: bool
    supplier: SupplierRef
    consumable: ConsumableRef


# ---------- Stock ----------


class BatchOut(ORMModel):
    id: int
    lot_number: str
    expiry_date: date | None
    quantity: int
    initial_quantity: int
    unit_cost: float
    received_at: datetime
    supplier: SupplierRef | None
    is_expired: bool = False
    days_to_expiry: int | None = None


class DepartmentRef(ORMModel):
    id: int
    name: str
    code: str


class UserRef(ORMModel):
    id: int
    full_name: str


class BatchRef(ORMModel):
    id: int
    lot_number: str
    expiry_date: date | None


class MovementOut(ORMModel):
    id: int
    movement_type: MovementType
    quantity: int
    balance_after: int
    reference: str | None
    reason: str | None
    created_at: datetime
    consumable: ConsumableRef
    batch: BatchRef | None
    department: DepartmentRef | None
    supplier: SupplierRef | None
    performed_by: UserRef | None


class ReceiveRequest(BaseModel):
    consumable_id: int
    quantity: int = Field(gt=0, le=10_000_000)
    lot_number: str = Field(min_length=1, max_length=64)
    expiry_date: date | None = None
    supplier_id: int | None = None
    unit_cost: float | None = Field(None, ge=0)
    reference: str | None = Field(None, max_length=100)
    notes: str | None = None
    supplier_order_id: int | None = None  # V4: receive against an open supplier order (records the delivery)


class IssueRequest(BaseModel):
    consumable_id: int
    quantity: int = Field(gt=0, le=10_000_000)
    department_id: int
    reference: str | None = Field(None, max_length=100)
    notes: str | None = None


class ReturnRequest(BaseModel):
    batch_id: int
    quantity: int = Field(gt=0, le=10_000_000)
    department_id: int
    reason: str | None = None


class WastageRequest(BaseModel):
    batch_id: int
    quantity: int = Field(gt=0, le=10_000_000)
    reason: str = Field(min_length=3)


class AdjustRequest(BaseModel):
    batch_id: int
    counted_quantity: int = Field(ge=0, le=10_000_000)
    reason: str = Field(min_length=3)


class MovementResult(BaseModel):
    movements: list[MovementOut]
    usable_stock: int
    status: StockStatus


class InventoryRow(BaseModel):
    consumable_id: int
    sku: str
    name: str
    unit: str
    category: str | None
    category_id: int | None
    reorder_level: int
    max_level: int | None
    unit_cost: float
    is_active: bool
    usable_stock: int
    expired_stock: int
    stock_value: float
    status: StockStatus
    batch_count: int
    next_expiry: date | None
    last_movement_at: datetime | None
    open_alerts: int


class DailyPoint(BaseModel):
    date: date
    issued: int
    received: int
    issued_value: float = 0


class InventoryDetail(BaseModel):
    item: ConsumableOut
    stock: InventoryRow
    batches: list[BatchOut]
    suppliers: list[SupplierProductOut]
    recent_movements: list[MovementOut]
    daily: list[DailyPoint]
    avg_daily_issue_30d: float
    days_of_cover: float | None


# ---------- Alerts ----------


class AlertOut(ORMModel):
    id: int
    alert_type: AlertType
    severity: AlertSeverity
    status: AlertStatus
    title: str
    message: str
    created_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    consumable: ConsumableRef | None
    batch: BatchRef | None


class EvaluateResult(BaseModel):
    created: int
    updated: int
    resolved: int
    open_total: int


# ---------- Audit ----------


class AuditOut(ORMModel):
    id: int
    action: str
    entity_type: str
    entity_id: int | None
    details: dict | None
    ip_address: str | None
    created_at: datetime
    user: UserRef | None


# ---------- Dashboard ----------


class NamedCount(BaseModel):
    name: str
    value: float


class DashboardSummary(BaseModel):
    items_monitored: int
    status_counts: dict[str, int]
    stock_value: float
    expired_value: float
    expiring_soon_value: float
    open_alerts: dict[str, int]
    open_alerts_by_severity: dict[str, int]
    daily: list[DailyPoint]
    consumption_by_department: list[NamedCount]
    top_consumed: list[NamedCount]
    recent_movements: list[MovementOut]
    critical_items: list[InventoryRow]
