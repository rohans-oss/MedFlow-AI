"""V4 — supplier orders and supplier intelligence schemas."""

from datetime import date

from pydantic import BaseModel, Field, field_validator


class SupplierOrderIn(BaseModel):
    supplier_id: int
    consumable_id: int
    quantity_ordered: int = Field(gt=0, le=10_000_000)
    unit_price: float | None = Field(None, ge=0)  # default: supplier catalogue price
    ordered_date: date | None = None  # default: today
    expected_date: date | None = None  # default: ordered + quoted lead time
    reference: str | None = Field(None, max_length=64)
    notes: str | None = Field(None, max_length=1000)

    @field_validator("reference")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None


class SupplierOrderClose(BaseModel):
    reason: str | None = Field(None, max_length=300)


class SupplierMetrics(BaseModel):
    orders: int
    open: int
    overdue: int
    received: int
    cancelled: int
    decided: int
    otif_successes: int
    otif_rate: float | None
    otif_ci_low: float | None
    otif_ci_high: float | None
    prior: float | None
    reliability_score: float | None
    grade: str | None
    limited_evidence: bool
    on_time_rate: float | None
    late_rate: float | None
    avg_days_late: float | None
    lead_time_median: float | None
    lead_time_mean: float | None
    lead_time_p90: float | None
    lead_time_std: float | None
    quoted_lead_time: int | None
    cancellation_rate: float | None
    fill_rate: float | None
    in_full_rate: float | None
    price_pairs: int
    price_changes: int
    price_stability: float | None
    price_change_pct: float | None


class ScorecardRow(BaseModel):
    supplier_id: int
    code: str
    name: str
    city: str | None
    is_active: bool
    default_lead_time_days: int
    has_catalogue: bool
    metrics: SupplierMetrics | None


class ScoreMethod(BaseModel):
    formula: str = "score = 100 × (OTIF orders + 5 × prior) ÷ (decided orders + 5)"
    otif: str = "OTIF = delivered in full (quantity received ≥ ordered) on or before the expected date"
    decided: str = "decided = received, cancelled, or still open past its expected date"
    prior: str = "prior = hospital-wide OTIF rate over the same window (pulls small samples towards the average)"
    interval: str = "95% Wilson interval on the raw OTIF rate"
    grades: str = "A ≥ 90 · B ≥ 80 · C ≥ 65 · D < 65; fewer than 5 decided orders = limited evidence"


class Scorecards(BaseModel):
    window_days: int
    window_start: date
    window_end: date
    prior: float
    hospital: SupplierMetrics
    suppliers: list[ScorecardRow]
    method: ScoreMethod = ScoreMethod()


class OrderRow(BaseModel):
    id: int
    reference: str
    supplier_id: int
    supplier: str
    consumable_id: int
    item: str
    sku: str
    unit: str
    ordered_date: date
    expected_date: date
    quoted_lead_time_days: int
    quantity_ordered: int
    quantity_received: int
    unit_price: float
    status: str
    first_delivery_date: date | None
    completed_date: date | None
    lead_time_days: int | None
    days_late: int | None
    overdue_days: int
    close_reason: str | None
    is_synthetic: bool
    recommendation_id: int | None = None  # V5: recorded by approving a procurement recommendation


class OpenOrder(OrderRow):
    days_in_transit: int
    predicted_arrival: date
    p_late: float | None
    p_before_stockout: float | None
    before_stockout_k: int | None
    before_stockout_n: int | None
    item_risk_level: str | None = None
    item_stockout_date: date | None = None


class SupplierItemPerf(BaseModel):
    consumable_id: int
    name: str
    sku: str
    unit: str
    catalogue_price: float | None
    quoted_lead_time_days: int | None
    moq: int | None
    is_preferred: bool
    metrics: SupplierMetrics | None
    price_history: list[dict]


class SupplierDetail(BaseModel):
    supplier: dict
    window_days: int
    window_start: date
    window_end: date
    prior: float
    metrics: SupplierMetrics
    monthly: list[dict]
    lead_time_histogram: list[dict]
    items: list[SupplierItemPerf]
    recent_orders: list[OrderRow]
    method: ScoreMethod = ScoreMethod()


class ItemOption(BaseModel):
    supplier_id: int
    supplier: str
    code: str
    is_active: bool
    is_preferred: bool
    unit_price: float
    price_vs_cheapest: float | None
    moq: int
    quoted_lead_time_days: int
    typical_lead_time_days: float
    p90_lead_time_days: float
    lead_time_basis: str  # item | supplier | quoted
    evidence_basis: str  # item | supplier | none
    evidence_orders: int
    item_orders: int
    on_time_rate: float | None
    otif_rate: float | None
    reliability_score: float | None
    grade: str | None
    cancellation_rate: float | None
    fill_rate: float | None
    price_stability: float | None
    in_time_k: int | None
    in_time_n: int | None
    p_in_time: float | None
    verdict: str  # likely | uncertain | unlikely | no_evidence | no_deadline
    arrives_typically: date
    arrives_worst_case: date


class ItemRiskLink(BaseModel):
    risk_level: str
    probability: float
    usable_stock: int
    expected_stockout_date: date | None
    days_of_stock_remaining: int | None
    shortage_quantity: int
    forecast_14: float
    as_of: date
    out_of_stock: bool


class ItemOptions(BaseModel):
    item: dict
    risk: ItemRiskLink | None
    deadline_days: int | None
    options: list[ItemOption]
    open_orders: list[OpenOrder]
    summary: list[str]


class AtRiskRow(BaseModel):
    item: dict
    risk: ItemRiskLink
    deadline_days: int | None
    n_suppliers: int
    n_likely: int
    best: ItemOption | None
    open_orders: int
    overdue_orders: int
    headline: str


class SupplierEvaluation(BaseModel):
    n_test: int
    test_start: str
    test_end: str
    late_rate_test: float | None = None
    lead_time: dict | None = None
    delay: dict | None = None
    notes: list[str] = []
