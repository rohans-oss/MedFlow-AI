"""V5 — procurement intelligence schemas.

Scenario / replenishment structures are nested plain objects produced by `app.procurement.engine` (documented in
docs/api.md and mirrored in frontend/lib/types.ts); they are stored as JSON on the recommendation for audit.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SettingsValues(BaseModel):
    service_level: float = Field(ge=0.5, le=0.999)
    review_period_days: int = Field(ge=1, le=28)
    horizon_days: int = Field(ge=14, le=30)
    stockout_cost_multiplier: float = Field(ge=0, le=100)
    holding_cost_rate: float = Field(ge=0, le=2)
    disposal_cost_pct: float = Field(ge=0, le=5)
    order_cost: float = Field(ge=0, le=1_000_000)
    allow_split: bool
    budget_limit: float | None = Field(None, gt=0, le=1_000_000_000)
    simulations: int = Field(ge=100, le=3000)


class SettingsOut(BaseModel):
    values: SettingsValues
    defaults: SettingsValues
    explain: dict[str, str]
    formula: list[str]
    is_default: bool
    updated_at: datetime | None
    updated_by: str | None


class AttentionRow(BaseModel):
    item: dict[str, Any]
    risk_level: str | None
    risk_probability: float | None
    v3_stockout_date: date | None
    usable_stock: int
    avg_daily_demand: float
    in_transit_orders: int
    in_transit_quantity: int
    expected_in_transit: float
    overdue_orders: int
    need_by_date: date | None
    order_by_date: date | None
    horizon_days: int
    p_stockout_no_order: float
    p_stockout_14_no_order: float
    expected_shortage_no_order: float
    required_quantity: int
    moq_adjusted_quantity: int
    reference_supplier: str
    safety_stock: float
    reasons: list[str]
    pending_recommendation_id: int | None = None
    pending_summary: str | None = None


class Attention(BaseModel):
    as_of: date
    max_horizon_days: int
    items: list[AttentionRow]
    skipped: list[dict[str, Any]]
    settings: dict[str, Any]


class Plan(BaseModel):
    """Live V5 plan for one item: replenishment, in-transit, every scenario evaluated, recommended key, explanation."""

    item: dict[str, Any]
    as_of: date
    horizon_days: int
    risk: dict[str, Any] | None
    demand: dict[str, Any]
    reference_supplier: str
    replenishment: dict[str, Any]
    replenishment_by_supplier: dict[str, dict[str, Any]]
    need_by_days: int
    need_by_date: date | None
    in_transit: list[dict[str, Any]]
    scenarios: list[dict[str, Any]]
    solver: dict[str, Any]
    recommended_key: str
    explanation: list[str]
    budget_note: str | None = None


class DelayIn(BaseModel):
    supplier_id: int
    days: int = Field(ge=0, le=60)


class WhatIfIn(BaseModel):
    delays: list[DelayIn] = Field(default_factory=list, max_length=10)
    demand_change_pct: float = Field(0, ge=-90, le=300)


class WhatIfOut(BaseModel):
    item: dict[str, Any]
    delays: dict[str, int]
    demand_change_pct: float
    horizon_days: int
    need_by_date: date | None
    baseline_recommended: str
    whatif_best: str
    replanned_recommended: str
    scenarios: list[dict[str, Any]]
    summary: list[str]


class GenerateIn(BaseModel):
    item_ids: list[int] | None = Field(None, max_length=500)  # default: every item that needs attention


class LineIn(BaseModel):
    supplier_id: int
    quantity: int = Field(gt=0, le=10_000_000)
    unit_price: float | None = Field(None, ge=0)  # default: catalogue price


class ApproveIn(BaseModel):
    lines: list[LineIn] | None = Field(None, max_length=4)  # None = approve as recommended; [] = approve "no order"
    reason: str | None = Field(None, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class RecommendationRow(BaseModel):
    id: int
    run_id: str
    item: dict[str, Any]
    status: str
    created_at: datetime
    as_of: date
    is_current: bool  # generated today (approvable)
    risk_level: str | None
    need_by_date: date | None
    order_by_date: date | None
    lines: list[dict[str, Any]]
    quantity: int
    purchase_value: float
    expected_cost: float
    p_stockout: float | None
    expected_shortage: float | None
    no_order_p_stockout: float | None
    headline: str
    generated_by: str | None
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    modified: bool
    final_lines: list[dict[str, Any]] | None
    orders: list[dict[str, Any]]


class RecommendationDetail(RecommendationRow):
    cost_breakdown: dict[str, Any]
    metrics: dict[str, Any]
    scenarios: list[dict[str, Any]]
    replenishment: dict[str, Any]
    in_transit: list[dict[str, Any]]
    explanation: list[str]
    settings_snapshot: dict[str, Any]
    solver: dict[str, Any]
    scenario_key: str
    final_evaluation: dict[str, Any] | None


class GenerateOut(BaseModel):
    run_id: str
    created: int
    superseded: int
    orders_recommended: int
    purchase_value: float
    solver: dict[str, Any]
    skipped: list[dict[str, Any]]
    recommendations: list[RecommendationRow]


class ArrivalEvaluation(BaseModel):
    n: int
    orders: int | None = None
    windows: list[str] | None = None
    observed_rate: float | None = None
    mean_predicted: float | None = None
    brier_model: float | None = None
    brier_quote_certain: float | None = None
    calibration: list[dict[str, Any]] | None = None
