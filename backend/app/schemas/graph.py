"""V6 — knowledge-graph schemas (nested graph results are plain objects; mirrored in frontend/lib/types.ts)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SyncRunOut(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    trigger: str
    backend: str
    verified: bool
    duration_ms: int | None
    removed_nodes: int
    node_counts: dict[str, int] | None
    edge_counts: dict[str, int] | None
    graph_node_counts: dict[str, int] | None
    graph_edge_counts: dict[str, int] | None
    error: str | None


class GraphStatus(BaseModel):
    backend: str
    url: str
    configured: bool
    available: bool
    error: str | None
    current: bool | None  # graph matches PostgreSQL (None when unknown)
    changed: list[str]
    last_success: SyncRunOut | None
    runs: list[SyncRunOut]
    note: str


class QueryIn(BaseModel):
    item_id: int | None = None
    supplier_id: int | None = None


class GraphResult(BaseModel):
    """Explain / impact / query / schema payloads + the sync run they were read from."""

    model_config = {"extra": "allow"}
    synced_at: datetime | None = None
    sync_run_id: int | None = None
    data: dict[str, Any]


class QueryDef(BaseModel):
    name: str
    title: str
    params: list[str]
    cypher: str
