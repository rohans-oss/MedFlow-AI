"""V9 — integrations & data exchange schemas."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

Connector = Literal["upload", "api_push", "rest_pull"]
SystemType = Literal["erp", "inventory", "procurement", "spreadsheet", "other"]


class EntityField(BaseModel):
    name: str
    kind: str
    required: bool
    description: str


class EntityInfo(BaseModel):
    name: str
    label: str
    description: str
    transactional: bool
    fields: list[EntityField]


class SourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    system_type: SystemType = "other"
    connector: Connector
    entities: list[str] = Field(min_length=1)
    config: dict[str, Any] = {}
    enabled: bool = True


class SourceUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    system_type: SystemType | None = None
    entities: list[str] | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class SourceOut(ORMModel):
    id: int
    name: str
    system_type: str
    connector: str
    enabled: bool
    entities: list[str]
    config: dict[str, Any]
    is_simulated: bool
    last_run_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None
    created_at: datetime


class Freshness(BaseModel):
    age_hours: float | None
    stale_after_hours: float
    stale: bool


class EntityStatus(BaseModel):
    entity: str
    status: str
    at: datetime
    run_id: int
    rejected: int


class SourceMonitor(BaseModel):
    id: int
    name: str
    system_type: str
    connector: str
    enabled: bool
    is_simulated: bool
    entities: list[str]
    health: str
    last_run_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None
    freshness: Freshness
    schedule_minutes: int | None
    runs_7d: int
    received_7d: int
    created_7d: int
    updated_7d: int
    unchanged_7d: int
    rejected_7d: int
    open_reconciliation: int
    active_keys: int
    entity_status: list[EntityStatus]


class MonitorTotals(BaseModel):
    sources: int
    enabled: int
    healthy: int
    degraded: int
    failing: int
    never_run: int
    received_7d: int
    rejected_7d: int
    created_7d: int
    updated_7d: int
    open_reconciliation: int


class Overview(BaseModel):
    sources: list[SourceMonitor]
    totals: MonitorTotals
    reference_erp_enabled: bool


class MappingOut(BaseModel):
    entity: str
    field_map: dict[str, str]
    defaults: dict[str, Any]
    date_format: str | None
    customized: bool


class MappingUpdate(BaseModel):
    field_map: dict[str, str] = {}
    defaults: dict[str, Any] = {}
    date_format: str | None = Field(None, max_length=32)


class CredentialOut(ORMModel):
    id: int
    label: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class CredentialCreate(BaseModel):
    label: str = Field("API key", min_length=1, max_length=120)


class CredentialCreated(CredentialOut):
    api_key: str  # shown once


class UserRef(ORMModel):
    id: int
    full_name: str


class SyncRunOut(ORMModel):
    id: int
    source_id: int
    source_name: str
    entity: str
    mode: str
    trigger: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    records_received: int
    records_created: int
    records_updated: int
    records_unchanged: int
    records_rejected: int
    error_summary: dict[str, Any] | None
    checkpoint_before: str | None
    checkpoint_after: str | None
    file_name: str | None
    file_sha256: str | None
    parent_run_id: int | None
    triggered_by: UserRef | None


class SyncRecordOut(ORMModel):
    id: int
    row_number: int
    external_id: str | None
    raw: dict[str, Any]
    errors: list[dict[str, Any]]
    status: str
    retried_in_run_id: int | None


class SyncRunDetail(SyncRunOut):
    rejected: list[SyncRecordOut]


class SyncRequest(BaseModel):
    entities: list[str] | None = None
    full: bool = False  # ignore checkpoints (initial / full resync)
    dry_run: bool = False


class TestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None


class ReconItem(BaseModel):
    id: int
    sku: str
    name: str
    unit: str


class ReconciliationOut(ORMModel):
    id: int
    source_id: int
    source_name: str
    run_id: int | None
    item: ReconItem
    as_of: date
    external_quantity: int
    medflow_quantity: int
    difference: int
    medflow_now: int
    status: str
    resolution: str | None
    note: str | None
    resolved_by: UserRef | None
    resolved_at: datetime | None
    created_at: datetime


class ResolveRequest(BaseModel):
    action: Literal["adjust", "external_wrong", "accept"]
    note: str = Field(min_length=3, max_length=500)


class IngestRequest(BaseModel):
    records: list[dict[str, Any]]
    dry_run: bool = False


class IngestResult(BaseModel):
    run_id: int
    status: str
    entity: str
    dry_run: bool
    received: int
    created: int
    updated: int
    unchanged: int
    rejected: int
    rejected_records: list[SyncRecordOut]
    info: dict[str, int] = {}
