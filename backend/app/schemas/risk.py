"""V3 — stockout risk schemas."""

from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class RiskModelOut(ORMModel):
    id: int
    run_id: str
    name: str
    model_type: str  # xgboost_classifier | cover_rule | reorder_rule
    trained_at: datetime
    horizon_days: int
    data_start: date
    data_end: date
    eval_start: date | None
    eval_end: date | None
    n_train_rows: int
    n_positive_train: int
    dataset_hash: str
    features: list | None
    params: dict | None
    metrics: dict  # {"backtest": {...}, "validation": {...}}
    evaluation: dict | None
    feature_importance: dict | None
    warn_threshold: float
    high_threshold: float
    is_active: bool
    notes: str | None


class RiskModelDetail(BaseModel):
    model: RiskModelOut
    run_candidates: list[RiskModelOut]
    item_names: dict[int, str]  # consumable_id → name (for the event table)


class Driver(BaseModel):
    feature: str
    label: str
    value: float | None
    contribution: float  # log-odds; > 0 raises risk


class ProjectionDay(BaseModel):
    date: date
    demand: float
    stock_end: float
    unmet: float
    expired: float


class RiskItem(BaseModel):
    consumable_id: int
    name: str
    sku: str
    unit: str
    category: str | None
    usable_stock: int
    reorder_level: int
    forecast_7: float
    forecast_14: float
    forecast_30: float
    days_of_stock_remaining: int | None  # None: covers the 30-day projection
    expected_stockout_date: date | None
    shortage_quantity: int  # unmet demand within horizon_days, no deliveries assumed
    expiring_quantity: int
    lead_time_days: int | None
    order_by_date: date | None
    order_overdue: bool
    can_replenish_in_time: bool | None
    probability: float
    risk_level: str  # HIGH | MEDIUM | LOW
    out_of_stock: bool
    probability_source: str
    main_reason: str
    as_of: date
    updated_at: datetime


class RiskHistoryPoint(BaseModel):
    at: datetime
    probability: float
    risk_level: str


class RiskDetail(RiskItem):
    horizon_days: int
    reasons: list[str]
    drivers: list[Driver]
    projection: list[ProjectionDay]
    history: list[RiskHistoryPoint]
    risk_model: str | None
    forecast_model: str | None
    warn_threshold: float | None
    high_threshold: float | None


class RiskCounts(BaseModel):
    high: int
    medium: int
    low: int
    out_of_stock: int
    total: int


class RiskOverview(BaseModel):
    model: RiskModelOut | None
    forecast_model: str | None
    as_of: date | None
    horizon_days: int
    counts: RiskCounts
    items: list[RiskItem]
    message: str | None = None  # why there is no risk yet


class RiskTrainResult(BaseModel):
    run_id: str
    active_model: str
    model_type: str
    selection: str
    data_start: date
    data_end: date
    n_items: int
    n_train_rows: int
    n_positive_train: int
    n_backtest_rows: int
    n_backtest_positive: int
    n_events: int
    backtest: dict
    thresholds: dict
    seconds: float
