"""V10 — pilot schemas."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel

PilotStatus = Literal["planned", "active", "paused", "completed", "cancelled"]


class PilotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # e.g. a hospital_id in the body is rejected, never used

    name: str = Field(min_length=3, max_length=120)
    description: str | None = Field(None, max_length=4000)
    baseline_start: date
    baseline_end: date
    pilot_start: date
    pilot_end: date
    owner_id: int | None = None
    notes: str | None = Field(None, max_length=4000)
    department_ids: list[int] = []
    user_ids: list[int] = []
    source_ids: list[int] = []


class PilotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=3, max_length=120)
    description: str | None = Field(None, max_length=4000)
    baseline_start: date | None = None
    baseline_end: date | None = None
    pilot_start: date | None = None
    pilot_end: date | None = None
    owner_id: int | None = None
    notes: str | None = Field(None, max_length=4000)
    status: PilotStatus | None = None
    department_ids: list[int] | None = None
    user_ids: list[int] | None = None
    source_ids: list[int] | None = None


class Ref(BaseModel):
    id: int
    name: str


class PilotOut(ORMModel):
    id: int
    hospital_id: int
    organization_id: int
    hospital_name: str
    name: str
    description: str | None
    status: str
    data_classification: str
    owner: Ref | None
    baseline_start: date
    baseline_end: date
    pilot_start: date
    pilot_end: date
    actual_end: date | None
    notes: str | None
    external_factors: list[dict[str, Any]]
    departments: list[Ref]
    users: list[Ref]
    data_sources: list[Ref]
    open_issues: int
    created_at: datetime
    updated_at: datetime


class FactorIn(BaseModel):
    factor: Literal["seasonal_demand", "department_change", "supplier_change", "data_source_change",
                    "inventory_policy_change", "staffing_change", "other"]
    note: str = Field(min_length=3, max_length=500)
    period: Literal["baseline", "pilot", "both"] = "pilot"


class Metric(BaseModel):
    key: str
    label: str
    section: str
    value: float | int | None
    unit: str
    n: int | None
    min_n: int
    sufficient: bool
    formula: str
    source: str
    note: str | None
    models: list[dict[str, Any]] | None = None


class PeriodInfo(BaseModel):
    kind: str
    start: date
    end: date
    days: int
    ledger_end: date | None
    ledger_days: int
    partial: bool


class SourceReliability(BaseModel):
    id: int
    name: str
    connector: str
    system_type: str
    is_simulated: bool
    enabled: bool
    runs: int
    successful_runs: int
    failed_runs: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    records_processed: int
    records_rejected: int
    avg_duration_s: float | None
    runs_per_active_day: float | None
    schedule_minutes: int | None
    freshness: dict[str, Any]


class PeriodMetrics(BaseModel):
    period: PeriodInfo
    metrics: list[Metric]
    integration_sources: list[SourceReliability]
    rejection_reasons: dict[str, int]
    recent_runs: list[dict[str, Any]]


class Dashboard(BaseModel):
    pilot: PilotOut
    classification_label: str
    period: PeriodMetrics
    issues: dict[str, int]
    readiness: dict[str, int]


class ComparisonRow(BaseModel):
    key: str
    label: str
    section: str
    unit: str
    baseline: float | int | None
    pilot: float | int | None
    baseline_n: int | None
    pilot_n: int | None
    min_n: int
    normalized: str | None
    difference: float | None
    difference_pp: float | None
    relative_change: float | None
    status: str


class Comparison(BaseModel):
    label: str
    causality_statement: str
    classification_label: str
    baseline: PeriodInfo
    pilot: PeriodInfo
    rows: list[ComparisonRow]
    external_factors: list[dict[str, Any]]
    notes: list[str]


class IssueCreate(BaseModel):
    category: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(None, max_length=4000)
    source: Literal["manual", "sync_run", "reconciliation", "feedback"] = "manual"
    source_ref: str | None = Field(None, max_length=64)
    impact: str | None = Field(None, max_length=2000)
    assigned_to_id: int | None = None


class IssueUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "investigating", "resolved", "ignored"] | None = None
    severity: Literal["low", "medium", "high", "critical"] | None = None
    assigned_to_id: int | None = None
    resolution: str | None = Field(None, max_length=4000)
    impact: str | None = Field(None, max_length=2000)
    description: str | None = Field(None, max_length=4000)


class IssueOut(ORMModel):
    id: int
    pilot_id: int
    category: str
    severity: str
    title: str
    description: str | None
    source: str
    source_ref: str | None
    impact: str | None
    status: str
    assigned_to: Ref | None
    created_by: Ref | None
    resolution: str | None
    resolved_by: Ref | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class FeedbackIn(BaseModel):
    target_type: Literal["recommendation", "forecast", "stockout_risk", "supplier", "integration", "workflow"]
    recommendation_id: int | None = None
    rating: Literal["useful", "somewhat_useful", "not_useful"]
    reasons: list[Literal["correct_recommendation", "wrong_quantity", "wrong_supplier", "data_problem", "timing_problem",
                          "missing_context", "other"]] = []
    comment: str | None = Field(None, max_length=1000)


class FeedbackOut(ORMModel):
    id: int
    pilot_id: int
    user: Ref | None
    target_type: str
    recommendation_id: int | None
    rating: str
    reasons: list[str]
    comment: str | None
    created_at: datetime


class FeedbackSummary(BaseModel):
    items: list[FeedbackOut]
    by_rating: dict[str, int]
    by_reason: dict[str, int]
    by_target: dict[str, int]


class DecisionRow(BaseModel):
    id: int
    item: str
    sku: str
    created_at: datetime
    as_of: date
    risk_level: str | None
    quantity: int
    lines: list[dict[str, Any]]
    has_order: bool
    viewed: bool
    views: int
    first_viewed_at: datetime | None
    outcome: str
    final_lines: list[dict[str, Any]] | None
    decided_by: Ref | None
    decided_at: datetime | None
    reason: str | None
    hours_to_decision: float | None


class ReadinessItem(BaseModel):
    key: str
    label: str
    mode: str
    hint: str
    done: bool
    auto_ok: bool | None
    evidence: str | None
    confirmed: bool
    note: str | None
    confirmed_by_id: int | None
    confirmed_at: datetime | None


class ReadinessGroup(BaseModel):
    group: str
    items: list[ReadinessItem]


class Readiness(BaseModel):
    groups: list[ReadinessGroup]
    done: int
    total: int
    statement: str


class ReadinessConfirm(BaseModel):
    confirmed: bool
    note: str | None = Field(None, max_length=1000)


class ReportSnapshotOut(ORMModel):
    id: int
    pilot_id: int
    generated_at: datetime
    generated_by: Ref | None
    data_classification: str


class OrgPilotRow(BaseModel):
    id: int
    hospital_id: int
    hospital: str
    name: str
    status: str
    data_classification: str
    baseline_start: date
    baseline_end: date
    pilot_start: date
    pilot_end: date
    open_issues: int
