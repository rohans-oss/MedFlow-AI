"""SQLAlchemy ORM models. Every tenant-owned table carries hospital_id (directly or via its parent)
so Version 8 multi-tenancy is an extension, not a rewrite."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow
from app.models.enums import (  # noqa: F401  (re-export)
    ACTIVE_ALERT_STATUSES,
    ACTIVE_ORDER_STATUSES,
    AlertSeverity,
    AlertStatus,
    AlertType,
    MovementType,
    OrgRole,
    ProcedureStatus,
    RecommendationStatus,
    RiskLevel,
    Role,
    StockStatus,
    SupplierOrderStatus,
    TenantStatus,
)


class Organization(TimestampMixin, Base):
    """V8 — a hospital group / customer organization. Every hospital belongs to exactly one."""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=TenantStatus.ACTIVE)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Hospital(TimestampMixin, Base):
    __tablename__ = "hospitals"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=TenantStatus.ACTIVE)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(32), unique=True)
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    bed_count: Mapped[int | None] = mapped_column(Integer)
    expiry_warning_days: Mapped[int] = mapped_column(Integer, default=60)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    organization: Mapped[Organization] = relationship(lazy="joined")


class Department(TimestampMixin, Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("hospital_id", "code", name="uq_departments_hospital_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # V8: hospital_id / role / department_id are the ACTIVE hospital context — a cache of one of the user's ACTIVE
    # hospital_memberships, changed only by the verified switch endpoint (and re-checked on every request).
    # NULL = no hospital selected (e.g. a platform admin with no membership). Access comes from memberships.
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id", ondelete="SET NULL"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(32), default=Role.VIEWER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    department: Mapped[Department | None] = relationship(lazy="joined")


class ConsumableCategory(TimestampMixin, Base):
    __tablename__ = "consumable_categories"
    __table_args__ = (UniqueConstraint("hospital_id", "name", name="uq_categories_hospital_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)


class Consumable(TimestampMixin, Base):
    __tablename__ = "consumables"
    __table_args__ = (UniqueConstraint("hospital_id", "sku", name="uq_consumables_hospital_sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("consumable_categories.id", ondelete="SET NULL"), index=True
    )
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(32), default="unit")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    reorder_level: Mapped[int] = mapped_column(Integer, default=0)
    max_level: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    category: Mapped[ConsumableCategory | None] = relationship(lazy="joined")


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("hospital_id", "code", name="uq_suppliers_hospital_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(32))
    contact_person: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    gstin: Mapped[str | None] = mapped_column(String(15))
    default_lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SupplierProduct(TimestampMixin, Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "consumable_id", name="uq_supplier_products_supplier_consumable"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), index=True)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id", ondelete="CASCADE"), index=True)
    supplier_sku: Mapped[str | None] = mapped_column(String(64))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    moq: Mapped[int] = mapped_column(Integer, default=1)
    is_preferred: Mapped[bool] = mapped_column(Boolean, default=False)

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    consumable: Mapped[Consumable] = relationship(lazy="joined")


class StockBatch(TimestampMixin, Base):
    __tablename__ = "stock_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    lot_number: Mapped[str] = mapped_column(String(64))
    expiry_date: Mapped[date | None] = mapped_column(Date, index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    initial_quantity: Mapped[int] = mapped_column(Integer, default=0)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    supplier: Mapped[Supplier | None] = relationship(lazy="joined")


class StockMovement(Base):
    """Append-only stock ledger. Never updated or deleted."""

    __tablename__ = "stock_movements"
    __table_args__ = (Index("ix_stock_movements_consumable_created", "consumable_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id", ondelete="CASCADE"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("stock_batches.id", ondelete="SET NULL"))
    movement_type: Mapped[str] = mapped_column(String(16), index=True)
    quantity: Mapped[int] = mapped_column(Integer)  # signed: + in, - out
    balance_after: Mapped[int] = mapped_column(Integer)  # item on-hand (all batches) after this movement
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    reference: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(Text)
    performed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    consumable: Mapped[Consumable] = relationship(lazy="joined")
    batch: Mapped[StockBatch | None] = relationship(lazy="joined")
    department: Mapped[Department | None] = relationship(lazy="joined")
    supplier: Mapped[Supplier | None] = relationship(lazy="joined")
    performed_by: Mapped[User | None] = relationship(lazy="joined")


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_hospital_status", "hospital_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))
    alert_type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=AlertStatus.OPEN)
    consumable_id: Mapped[int | None] = mapped_column(ForeignKey("consumables.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("stock_batches.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    acknowledged_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    consumable: Mapped[Consumable | None] = relationship(lazy="joined")
    batch: Mapped[StockBatch | None] = relationship(lazy="joined")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_hospital_created", "hospital_id", "created_at"),
                      Index("ix_audit_logs_organization_created", "organization_id", "created_at"))

    id: Mapped[int] = mapped_column(primary_key=True)
    # V8: hospital-level events carry hospital_id (+ organization_id); organization-level events only organization_id;
    # platform-level events neither.
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User | None] = relationship(lazy="joined")


# ---------------------------------------------------------------------------
# Version 2 — demand forecasting
# ---------------------------------------------------------------------------


class ModelVersion(Base):
    """One trained (or baseline) forecasting model from a training run.

    A run trains every candidate (baselines + XGBoost) on the same data and scores them on the same holdout;
    exactly one model per hospital is `is_active` and serves forecasts.
    """

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("hospital_id", "name", name="uq_model_versions_hospital_name"),
        Index("ix_model_versions_hospital_run", "hospital_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))  # e.g. xgb_v3, ma7_v3
    model_type: Mapped[str] = mapped_column(String(32))  # xgboost | moving_average_7 | historical_average
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    trained_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    data_start: Mapped[date] = mapped_column(Date)
    data_end: Mapped[date] = mapped_column(Date)  # last day of data used (inclusive) = "data through"
    test_start: Mapped[date] = mapped_column(Date)
    test_end: Mapped[date] = mapped_column(Date)
    horizon_days: Mapped[int] = mapped_column(Integer)
    n_series: Mapped[int] = mapped_column(Integer)
    n_train_rows: Mapped[int] = mapped_column(Integer)
    dataset_hash: Mapped[str] = mapped_column(String(64))
    features: Mapped[list | None] = mapped_column(JSON)
    params: Mapped[dict | None] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)  # holdout: mae, rmse, wape, bias, n_points
    feature_importance: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    artifact: Mapped[bytes | None] = mapped_column(LargeBinary)  # serialized booster (xgboost only)
    notes: Mapped[str | None] = mapped_column(Text)


class ModelItemMetric(Base):
    """Per-item holdout (backtest) error and forecast explanation for a model version."""

    __tablename__ = "model_item_metrics"
    __table_args__ = (UniqueConstraint("model_version_id", "consumable_id", name="uq_model_item_metrics_model_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id", ondelete="CASCADE"), index=True)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id", ondelete="CASCADE"), index=True)
    actual_total: Mapped[float] = mapped_column(Float)
    predicted_total: Mapped[float] = mapped_column(Float)
    mae: Mapped[float] = mapped_column(Float)
    rmse: Mapped[float] = mapped_column(Float)
    wape: Mapped[float | None] = mapped_column(Float)  # None when actual_total == 0
    bias: Mapped[float | None] = mapped_column(Float)
    residual_std: Mapped[float] = mapped_column(Float)  # daily error std (units), used for intervals
    n_points: Mapped[int] = mapped_column(Integer)
    daily: Mapped[list | None] = mapped_column(JSON)  # [{date, actual, predicted}] over the holdout
    drivers: Mapped[dict | None] = mapped_column(JSON)  # {"7": {feature: units}, "14": ..., "30": ..., "base": {...}}


class Forecast(Base):
    """Daily point forecast for an item produced by a model version (sum over departments)."""

    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint("model_version_id", "consumable_id", "forecast_date", name="uq_forecasts_model_item_date"),
        Index("ix_forecasts_consumable_date", "consumable_id", "forecast_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id", ondelete="CASCADE"), index=True)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id", ondelete="CASCADE"))
    forecast_date: Mapped[date] = mapped_column(Date)
    horizon: Mapped[int] = mapped_column(Integer)  # 1 = first day after data_end
    predicted: Mapped[float] = mapped_column(Float)


# ---------------------------------------------------------------------------
# Version 2B — procedure-aware forecasting (procedure COUNTS only — never patient data)
# ---------------------------------------------------------------------------


class ProcedureType(TimestampMixin, Base):
    """A kind of procedure a department performs (e.g. hernia repair)."""

    __tablename__ = "procedure_types"
    __table_args__ = (UniqueConstraint("hospital_id", "code", name="uq_procedure_types_hospital_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    avg_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)  # demo data label

    department: Mapped[Department] = relationship(lazy="joined")


class ProcedureItemMapping(TimestampMixin, Base):
    """Expected consumable usage per procedure ("kit"). Hospital-configurable; demo values are synthetic."""

    __tablename__ = "procedure_item_mappings"
    __table_args__ = (
        UniqueConstraint("procedure_type_id", "consumable_id", name="uq_procedure_item_mappings_type_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    procedure_type_id: Mapped[int] = mapped_column(ForeignKey("procedure_types.id", ondelete="CASCADE"), index=True)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id", ondelete="CASCADE"), index=True)
    quantity_per_procedure: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)

    procedure_type: Mapped[ProcedureType] = relationship(lazy="joined")
    consumable: Mapped[Consumable] = relationship(lazy="joined")


class ProcedureSchedule(TimestampMixin, Base):
    """How many procedures of a type a department has on a date. One non-cancelled row per (type, dept, date)."""

    __tablename__ = "procedure_schedules"
    __table_args__ = (
        Index("ix_procedure_schedules_hospital_date", "hospital_id", "scheduled_date"),
        Index(
            "uq_procedure_schedules_active_slot", "procedure_type_id", "department_id", "scheduled_date",
            unique=True,
            postgresql_where=text("status <> 'CANCELLED'"),
            sqlite_where=text("status <> 'CANCELLED'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))
    procedure_type_id: Mapped[int] = mapped_column(ForeignKey("procedure_types.id", ondelete="CASCADE"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    scheduled_date: Mapped[date] = mapped_column(Date)
    count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=ProcedureStatus.SCHEDULED)
    notes: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    procedure_type: Mapped[ProcedureType] = relationship(lazy="joined")
    department: Mapped[Department] = relationship(lazy="joined")


# ---------------------------------------------------------------- V3: stockout risk intelligence


class RiskModelVersion(Base):
    """A trained (or rule-based) stockout-risk model and its evaluation. One `is_active` per hospital."""

    __tablename__ = "risk_model_versions"
    __table_args__ = (Index("ix_risk_model_versions_hospital_active", "hospital_id", "is_active"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))  # stockout_xgb_v3, cover_rule_v3, reorder_rule_v3
    model_type: Mapped[str] = mapped_column(String(32))  # xgboost_classifier | cover_rule | reorder_rule
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    trained_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    horizon_days: Mapped[int] = mapped_column(Integer)  # label: stockout within this many days
    data_start: Mapped[date] = mapped_column(Date)
    data_end: Mapped[date] = mapped_column(Date)
    eval_start: Mapped[date | None] = mapped_column(Date)  # first as-of date of the ledger backtest
    eval_end: Mapped[date | None] = mapped_column(Date)
    n_train_rows: Mapped[int] = mapped_column(Integer, default=0)
    n_positive_train: Mapped[int] = mapped_column(Integer, default=0)
    dataset_hash: Mapped[str] = mapped_column(String(64))
    features: Mapped[list | None] = mapped_column(JSON)
    params: Mapped[dict | None] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)  # {"backtest": {...}, "validation": {...}}
    evaluation: Mapped[dict | None] = mapped_column(JSON)  # events, PR curve, calibration
    feature_importance: Mapped[dict | None] = mapped_column(JSON)
    warn_threshold: Mapped[float] = mapped_column(Float)  # probability ≥ this ⇒ MEDIUM (warning)
    high_threshold: Mapped[float] = mapped_column(Float)  # probability ≥ this ⇒ HIGH
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    artifact: Mapped[bytes | None] = mapped_column(LargeBinary)  # serialized booster (xgboost only)
    notes: Mapped[str | None] = mapped_column(Text)


class StockoutPrediction(Base):
    """Append-only snapshot of an item's stockout risk. The latest row per item is the current risk."""

    __tablename__ = "stockout_predictions"
    __table_args__ = (
        Index("ix_stockout_predictions_hospital_created", "hospital_id", "created_at"),
        Index("ix_stockout_predictions_item_created", "consumable_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id", ondelete="CASCADE"))
    risk_model_version_id: Mapped[int | None] = mapped_column(ForeignKey("risk_model_versions.id", ondelete="SET NULL"))
    forecast_model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    as_of: Mapped[date] = mapped_column(Date)  # business date the projection starts from
    horizon_days: Mapped[int] = mapped_column(Integer)
    usable_stock: Mapped[int] = mapped_column(Integer)
    forecast_7: Mapped[float] = mapped_column(Float)
    forecast_14: Mapped[float] = mapped_column(Float)
    forecast_30: Mapped[float] = mapped_column(Float)
    days_of_stock_remaining: Mapped[int | None] = mapped_column(Integer)  # None: covers the whole projection
    expected_stockout_date: Mapped[date | None] = mapped_column(Date)
    shortage_quantity: Mapped[int] = mapped_column(Integer, default=0)  # unmet demand within horizon_days
    expiring_quantity: Mapped[int] = mapped_column(Integer, default=0)  # usable units expected to expire unused
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    order_by_date: Mapped[date | None] = mapped_column(Date)
    can_replenish_in_time: Mapped[bool | None] = mapped_column(Boolean)
    probability: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(16), default=RiskLevel.LOW)
    probability_source: Mapped[str] = mapped_column(String(32))  # model type that produced the probability
    reasons: Mapped[list | None] = mapped_column(JSON)
    drivers: Mapped[list | None] = mapped_column(JSON)
    projection: Mapped[list | None] = mapped_column(JSON)  # [{date, demand, stock_end, unmet, expired}]


# ---------------------------------------------------------------- V4: supplier intelligence (order evidence)


class SupplierOrder(Base, TimestampMixin):
    """An order placed with a supplier for one item. The evidence base for supplier reliability (V4).

    Tracking only: recording what was ordered, when it was due and what arrived. Purchase recommendations and
    approval workflows are V5.
    """

    __tablename__ = "supplier_orders"
    __table_args__ = (
        Index("ix_supplier_orders_hospital_status", "hospital_id", "status"),
        Index("ix_supplier_orders_supplier_item", "supplier_id", "consumable_id"),
        UniqueConstraint("hospital_id", "reference", name="uq_supplier_orders_hospital_reference"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id"))
    reference: Mapped[str] = mapped_column(String(64))
    ordered_date: Mapped[date] = mapped_column(Date)
    quoted_lead_time_days: Mapped[int] = mapped_column(Integer)
    expected_date: Mapped[date] = mapped_column(Date)  # ordered_date + quoted lead time (or as agreed)
    quantity_ordered: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(16), default=SupplierOrderStatus.OPEN)
    quantity_received: Mapped[int] = mapped_column(Integer, default=0)
    first_delivery_date: Mapped[date | None] = mapped_column(Date)
    completed_date: Mapped[date | None] = mapped_column(Date)  # date it was fully received or closed
    cancelled_date: Mapped[date | None] = mapped_column(Date)
    close_reason: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # V5: set when the order was recorded by approving a procurement recommendation
    recommendation_id: Mapped[int | None] = mapped_column(
        ForeignKey("procurement_recommendations.id", ondelete="SET NULL"), index=True
    )

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    consumable: Mapped[Consumable] = relationship(lazy="joined")


class SupplierDelivery(Base):
    """A delivery against a supplier order. Linked to the stock receipt when it went through the ledger;
    imported historical deliveries (before the ledger started) have no movement."""

    __tablename__ = "supplier_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("supplier_orders.id", ondelete="CASCADE"), index=True)
    received_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock_movement_id: Mapped[int | None] = mapped_column(ForeignKey("stock_movements.id", ondelete="SET NULL"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("stock_batches.id", ondelete="SET NULL"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcurementSettings(Base):
    """V5 — the hospital's expected-cost model parameters (one row per hospital; defaults until first saved)."""

    __tablename__ = "procurement_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), unique=True)
    service_level: Mapped[float] = mapped_column(Float, default=0.95)
    review_period_days: Mapped[int] = mapped_column(Integer, default=7)
    horizon_days: Mapped[int] = mapped_column(Integer, default=30)
    stockout_cost_multiplier: Mapped[float] = mapped_column(Float, default=5.0)
    holding_cost_rate: Mapped[float] = mapped_column(Float, default=0.25)
    disposal_cost_pct: Mapped[float] = mapped_column(Float, default=0.10)
    order_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=250)
    allow_split: Mapped[bool] = mapped_column(Boolean, default=True)
    budget_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    simulations: Mapped[int] = mapped_column(Integer, default=500)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ProcurementRecommendation(Base):
    """V5 — a recommended purchase for one item, with every scenario that was evaluated and the numbers behind it.

    Recommend-only: approving records supplier order(s) in MedFlow (V4 log). Nothing is sent to a supplier or to a
    purchasing system. Scenarios, cost breakdown and settings are snapshotted so a decision can be audited later.
    """

    __tablename__ = "procurement_recommendations"
    __table_args__ = (Index("ix_procurement_recommendations_hospital_status", "hospital_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    as_of: Mapped[date] = mapped_column(Date)
    run_id: Mapped[str] = mapped_column(String(32), index=True)  # recommendations generated together
    status: Mapped[str] = mapped_column(String(16), default=RecommendationStatus.PENDING)
    generated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    stockout_prediction_id: Mapped[int | None] = mapped_column(ForeignKey("stockout_predictions.id", ondelete="SET NULL"))
    risk_level: Mapped[str | None] = mapped_column(String(8))
    need_by_date: Mapped[date | None] = mapped_column(Date)
    order_by_date: Mapped[date | None] = mapped_column(Date)
    scenario_key: Mapped[str] = mapped_column(String(64))  # the recommended scenario
    lines: Mapped[list] = mapped_column(JSON)  # [{supplier_id, supplier, quantity, unit_price}] — empty = no order
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    purchase_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    expected_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    cost_breakdown: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)  # stockout probability, shortage, arrival … of the recommended scenario
    scenarios: Mapped[list] = mapped_column(JSON)  # every evaluated scenario
    replenishment: Mapped[dict] = mapped_column(JSON)  # V5.1 calculation
    in_transit: Mapped[list] = mapped_column(JSON)  # V5.2 open orders with arrival probability
    explanation: Mapped[list] = mapped_column(JSON)  # plain-English lines built from the numbers
    settings_snapshot: Mapped[dict] = mapped_column(JSON)
    solver: Mapped[dict] = mapped_column(JSON)  # OR-Tools status, objective, budget
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    modified: Mapped[bool] = mapped_column(Boolean, default=False)
    final_lines: Mapped[list | None] = mapped_column(JSON)
    final_evaluation: Mapped[dict | None] = mapped_column(JSON)  # the approved plan re-evaluated (when modified)

    consumable: Mapped[Consumable] = relationship(lazy="joined")


class GraphSyncRun(Base):
    """V6 — one PostgreSQL → knowledge-graph projection run (the graph is derived data; this is its audit trail)."""

    __tablename__ = "graph_sync_runs"
    __table_args__ = (Index("ix_graph_sync_runs_hospital_started", "hospital_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")  # RUNNING | SUCCESS | FAILED
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual | auto | train
    backend: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[dict | None] = mapped_column(JSON)  # PostgreSQL data version the projection was built from
    node_counts: Mapped[dict | None] = mapped_column(JSON)  # expected (projection) per label
    edge_counts: Mapped[dict | None] = mapped_column(JSON)
    graph_node_counts: Mapped[dict | None] = mapped_column(JSON)  # found in the graph after writing (verification)
    graph_edge_counts: Mapped[dict | None] = mapped_column(JSON)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    removed_nodes: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    triggered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class AssistantConversation(Base):
    """V7 — one AI-assistant conversation (private to its user; hospital-scoped)."""

    __tablename__ = "assistant_conversations"
    __table_args__ = (Index("ix_assistant_conversations_user_updated", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    context: Mapped[dict | None] = mapped_column(JSON)  # the item / supplier the conversation is about ("it")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    messages: Mapped[list["AssistantMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="AssistantMessage.id")


class AssistantMessage(Base):
    """V7 — one question or answer, with the tool calls that produced it (the assistant's audit trail)."""

    __tablename__ = "assistant_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    answer: Mapped[dict | None] = mapped_column(JSON)  # structured answer (title, summary, points, notes, links, follow-ups)
    tool_calls: Mapped[list | None] = mapped_column(JSON)  # [{tool, args, ok, facts, error, ms}]
    provider: Mapped[str | None] = mapped_column(String(32))  # deterministic | openai_compatible | anthropic | scripted
    model: Mapped[str | None] = mapped_column(String(100))
    intent: Mapped[str | None] = mapped_column(String(40))
    grounded: Mapped[bool | None] = mapped_column(Boolean)
    fallback_reason: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[AssistantConversation] = relationship(back_populates="messages")


class HospitalMembership(TimestampMixin, Base):
    """V8 — a user's access to one hospital, with the role (and department) they hold there. The source of truth for
    hospital access; one account can belong to several hospitals."""

    __tablename__ = "hospital_memberships"
    __table_args__ = (UniqueConstraint("user_id", "hospital_id", name="uq_hospital_memberships_user_hospital"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default=TenantStatus.ACTIVE)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="joined")
    hospital: Mapped[Hospital] = relationship(lazy="joined")
    department: Mapped[Department | None] = relationship(lazy="joined")


class OrganizationMembership(TimestampMixin, Base):
    """V8 — organization-level role (organization admin). Grants organization reporting / onboarding / member
    administration for that organization's hospitals — not their operational data (that needs a hospital membership)."""

    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_org_memberships_user_org"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default=OrgRole.ORG_ADMIN)
    status: Mapped[str] = mapped_column(String(16), default=TenantStatus.ACTIVE)

    user: Mapped[User] = relationship(lazy="joined")
    organization: Mapped[Organization] = relationship(lazy="joined")


# ---------------------------------------------------------------- V9 — integrations & data exchange


class IntegrationSource(TimestampMixin, Base):
    """V9 — one hospital system that exchanges data with MedFlow (an ERP, an inventory or procurement system, a
    spreadsheet feed). Hospital-scoped. Secrets are never stored here: inbound API keys live hashed in
    integration_credentials; an outbound credential is referenced by the NAME of an environment variable."""

    __tablename__ = "integration_sources"
    __table_args__ = (UniqueConstraint("hospital_id", "name", name="uq_integration_sources_hospital_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    system_type: Mapped[str] = mapped_column(String(32), default="other")  # erp | inventory | procurement | spreadsheet | other
    connector: Mapped[str] = mapped_column(String(16))  # upload | api_push | rest_pull
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    entities: Mapped[list] = mapped_column(JSON, default=list)  # entities this source may send
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # non-secret settings (base_url, auth_env, schedule, tolerance…)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)  # talks to the reference simulator, not a real system
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    service_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class IntegrationCredential(Base):
    """V9 — an inbound API key for one source. Only a keyed hash (HMAC-SHA256) is stored; the key is shown once."""

    __tablename__ = "integration_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("integration_sources.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    key_prefix: Mapped[str] = mapped_column(String(24), unique=True)  # public part used to find the key
    key_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntegrationMapping(TimestampMixin, Base):
    """V9 — how one source's fields map onto one MedFlow entity (medflow field → source field) + defaults/date format."""

    __tablename__ = "integration_mappings"
    __table_args__ = (UniqueConstraint("source_id", "entity", name="uq_integration_mappings_source_entity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("integration_sources.id", ondelete="CASCADE"), index=True)
    entity: Mapped[str] = mapped_column(String(32))
    field_map: Mapped[dict] = mapped_column(JSON, default=dict)
    defaults: Mapped[dict] = mapped_column(JSON, default=dict)
    date_format: Mapped[str | None] = mapped_column(String(32))  # e.g. %d/%m/%Y; ISO 8601 when empty
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class SyncRun(Base):
    """V9 — one import / sync of one entity from one source: the audit trail of every data exchange."""

    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_source_started", "source_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("integration_sources.id", ondelete="CASCADE"))
    entity: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16), default="incremental")  # initial | incremental | retry | dry_run
    trigger: Mapped[str] = mapped_column(String(16))  # upload | api | schedule | manual | retry
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")  # RUNNING | SUCCESS | PARTIAL | FAILED
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    records_created: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    records_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[dict | None] = mapped_column(JSON)  # {"fatal": str | None, "reasons": {reason: count}}
    checkpoint_before: Mapped[str | None] = mapped_column(String(64))
    checkpoint_after: Mapped[str | None] = mapped_column(String(64))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    parent_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id", ondelete="SET NULL"))
    triggered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    source: Mapped[IntegrationSource] = relationship(lazy="joined")


class SyncRecord(Base):
    """V9 — a rejected incoming record, kept (raw + reasons) so it can be inspected and retried."""

    __tablename__ = "sync_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    external_id: Mapped[str | None] = mapped_column(String(128))
    raw: Mapped[dict] = mapped_column(JSON)
    errors: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="REJECTED")  # REJECTED | RETRIED
    retried_in_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id", ondelete="SET NULL"))


class ExternalRef(Base):
    """V9 — idempotency: which MedFlow row an external record became, and the hash of its content."""

    __tablename__ = "external_refs"
    __table_args__ = (UniqueConstraint("source_id", "entity", "external_id", name="uq_external_refs_source_entity_ext"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("integration_sources.id", ondelete="CASCADE"))
    entity: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    record_hash: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[int | None] = mapped_column(Integer)
    first_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id", ondelete="SET NULL"))
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncCheckpoint(Base):
    """V9 — incremental sync position per source and entity (e.g. the largest updated_at received)."""

    __tablename__ = "sync_checkpoints"
    __table_args__ = (UniqueConstraint("source_id", "entity", name="uq_sync_checkpoints_source_entity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("integration_sources.id", ondelete="CASCADE"))
    entity: Mapped[str] = mapped_column(String(32))
    cursor: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReconciliationIssue(Base):
    """V9 — the hospital system's stock count differs from MedFlow's ledger. Never auto-corrected: a person decides."""

    __tablename__ = "reconciliation_issues"
    __table_args__ = (Index("ix_reconciliation_issues_hospital_status", "hospital_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("integration_sources.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id", ondelete="SET NULL"))
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id", ondelete="CASCADE"))
    as_of: Mapped[date] = mapped_column(Date)
    external_quantity: Mapped[int] = mapped_column(Integer)
    medflow_quantity: Mapped[int] = mapped_column(Integer)
    difference: Mapped[int] = mapped_column(Integer)  # external − MedFlow
    status: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN | RESOLVED
    resolution: Mapped[str | None] = mapped_column(String(24))  # adjusted | external_wrong | accepted | matched_later
    note: Mapped[str | None] = mapped_column(Text)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    consumable: Mapped[Consumable] = relationship(lazy="joined")
    source: Mapped[IntegrationSource] = relationship(lazy="joined")


# ---------------------------------------------------------------- V10: real hospital pilot & business validation


class Pilot(TimestampMixin, Base):
    """V10 — a time-boxed pilot of MedFlow in ONE hospital, with a baseline period before it.

    Metrics are computed on demand from the existing V1–V9 data (nothing is copied), separately for the baseline and
    the pilot period. `data_classification` says whether results may ever be called observed: pilots in demo hospitals
    are always "synthetic"."""

    __tablename__ = "pilots"
    __table_args__ = (
        UniqueConstraint("hospital_id", "name", name="uq_pilots_hospital_name"),
        CheckConstraint("baseline_start <= baseline_end", name="baseline_order"),
        CheckConstraint("pilot_start <= pilot_end", name="pilot_order"),
        CheckConstraint("baseline_end < pilot_start", name="baseline_before_pilot"),
        Index("ix_pilots_hospital_status", "hospital_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned|active|paused|completed|cancelled
    data_classification: Mapped[str] = mapped_column(String(16), default="observed")  # observed | synthetic
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    baseline_start: Mapped[date] = mapped_column(Date)
    baseline_end: Mapped[date] = mapped_column(Date)
    pilot_start: Mapped[date] = mapped_column(Date)  # the pilot's start date
    pilot_end: Mapped[date] = mapped_column(Date)  # planned end date
    actual_end: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    external_factors: Mapped[list] = mapped_column(JSON, default=list)  # [{factor, note, period, recorded_by, at}]
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class PilotParticipant(Base):
    """V10 — a participating department or user of a pilot (exactly one of the two)."""

    __tablename__ = "pilot_participants"
    __table_args__ = (
        CheckConstraint("(department_id IS NULL) <> (user_id IS NULL)", name="one_kind"),
        UniqueConstraint("pilot_id", "department_id", "user_id", name="uq_pilot_participants"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id", ondelete="CASCADE"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))


class PilotDataSource(Base):
    """V10 — a V9 integration source whose data belongs to the pilot (data quality, reliability, reconciliation)."""

    __tablename__ = "pilot_data_sources"
    __table_args__ = (UniqueConstraint("pilot_id", "source_id", name="uq_pilot_data_sources"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("integration_sources.id", ondelete="CASCADE"))


class PilotIssue(TimestampMixin, Base):
    """V10 — a problem found during the pilot. Never changes operational data; people investigate and resolve."""

    __tablename__ = "pilot_issues"
    __table_args__ = (Index("ix_pilot_issues_pilot_status", "pilot_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), default="medium")  # low | medium | high | critical
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual | sync_run | reconciliation | feedback
    source_ref: Mapped[str | None] = mapped_column(String(64))  # e.g. "sync_run:12"
    impact: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | investigating | resolved | ignored
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PilotFeedback(Base):
    """V10 — was a recommendation / workflow useful? (operational learning, not a review system)"""

    __tablename__ = "pilot_feedback"
    __table_args__ = (Index("ix_pilot_feedback_pilot_created", "pilot_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    target_type: Mapped[str] = mapped_column(String(32))  # recommendation | forecast | stockout_risk | supplier | integration | workflow
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("procurement_recommendations.id", ondelete="SET NULL"))
    rating: Mapped[str] = mapped_column(String(16))  # useful | somewhat_useful | not_useful
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecommendationView(Base):
    """V10 — a person opened a V5 procurement recommendation (the "viewed" step of the decision funnel)."""

    __tablename__ = "recommendation_views"
    __table_args__ = (Index("ix_recommendation_views_hospital_viewed", "hospital_id", "viewed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("procurement_recommendations.id", ondelete="CASCADE"),
                                                   index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PilotReadiness(Base):
    """V10 — a person's confirmation of one readiness-checklist item (automatic checks are computed, not stored)."""

    __tablename__ = "pilot_readiness"
    __table_args__ = (UniqueConstraint("pilot_id", "item_key", name="uq_pilot_readiness_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id", ondelete="CASCADE"))
    item_key: Mapped[str] = mapped_column(String(48))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PilotReportSnapshot(Base):
    """V10 — an immutable saved pilot report (the numbers as they were when it was generated)."""

    __tablename__ = "pilot_report_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id", ondelete="CASCADE"), index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    data_classification: Mapped[str] = mapped_column(String(16))
    report: Mapped[dict] = mapped_column(JSON)
    markdown: Mapped[str] = mapped_column(Text)


__all__ = [
    "Pilot",
    "PilotParticipant",
    "PilotDataSource",
    "PilotIssue",
    "PilotFeedback",
    "RecommendationView",
    "PilotReadiness",
    "PilotReportSnapshot",
    "IntegrationSource",
    "IntegrationCredential",
    "IntegrationMapping",
    "SyncRun",
    "SyncRecord",
    "ExternalRef",
    "SyncCheckpoint",
    "ReconciliationIssue",
    "Organization",
    "HospitalMembership",
    "OrganizationMembership",
    "AssistantConversation",
    "AssistantMessage",
    "Base",
    "Hospital",
    "Department",
    "User",
    "ConsumableCategory",
    "Consumable",
    "Supplier",
    "SupplierProduct",
    "StockBatch",
    "StockMovement",
    "Alert",
    "AuditLog",
    "ModelVersion",
    "ModelItemMetric",
    "Forecast",
    "ProcedureType",
    "ProcedureItemMapping",
    "ProcedureSchedule",
    "RiskModelVersion",
    "StockoutPrediction",
    "SupplierOrder",
    "SupplierDelivery",
    "ProcurementSettings",
    "ProcurementRecommendation",
    "GraphSyncRun",
]


import app.db.tenant_guard  # noqa: E402,F401  (V8: registers the tenant-consistency listener)
