"""V9 — the `rest_pull` connector: MedFlow calls a hospital system's REST API on demand or on a schedule.

Contract expected from the remote API (configurable key names):
  GET {base_url}/{resource}?page=1&page_size=200&updated_since=<ISO time>  →  {"items": [...], "next_page": 2 | null}
`updated_since` is inclusive; records at the boundary come back once more and are counted as unchanged (idempotency).
Transport errors, HTTP 5xx and 429 are retried with back-off; 4xx (e.g. 401) fail the run immediately. All pages of an
entity are fetched before anything is applied, so a failure half-way leaves MedFlow and the checkpoint untouched.

The outbound credential is never stored: `config.auth_env` names an environment variable holding the bearer token.
"""

import logging
import os
import time
from collections.abc import Callable
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import utcnow
from app.integrations import engine
from app.integrations.engine import SyncError
from app.integrations.entities import ORDER
from app.integrations.sources import REFERENCE_ERP
from app.models import Hospital, IntegrationSource, SyncRun, TenantStatus, User

log = logging.getLogger(__name__)

# Overridable in tests (the FastAPI TestClient is an httpx.Client) and for back-off timing.
client_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=settings.INTEGRATION_HTTP_TIMEOUT_SECONDS)  # noqa: E731
sleep: Callable[[float], None] = time.sleep


def base_url(db: Session, source: IntegrationSource) -> str:
    cfg = source.config or {}
    base = cfg.get("base_url") or ""
    if base == REFERENCE_ERP:
        if not settings.REFERENCE_ERP_ENABLED:
            raise SyncError("The reference ERP simulator is disabled on this server (REFERENCE_ERP_ENABLED=false)")
        h = db.get(Hospital, source.hospital_id)
        return f"{settings.REFERENCE_ERP_BASE_URL.rstrip('/')}/api/reference-erp/{h.code}"
    if not base:
        raise SyncError("The source has no base_url")
    return base


def _headers(source: IntegrationSource) -> dict:
    h = {"Accept": "application/json", "User-Agent": "MedFlow-Integrations/9.0"}
    env = (source.config or {}).get("auth_env")
    if env:
        token = os.environ.get(env) or (settings.REFERENCE_ERP_TOKEN if env == "REFERENCE_ERP_TOKEN" else "")
        if not token:
            raise SyncError(f"The credential environment variable {env} is not set on the server")
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(client: httpx.Client, url: str, params: dict, headers: dict) -> dict:
    attempts = max(1, settings.INTEGRATION_HTTP_RETRIES + 1)
    last = "no response"
    for i in range(attempts):
        try:
            r = client.get(url, params=params, headers=headers)
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}: {e}"[:200]
        else:
            if r.status_code < 400:
                try:
                    return r.json()
                except ValueError as e:
                    raise SyncError(f"{url} did not return JSON") from e
            if r.status_code in (401, 403):
                raise SyncError(f"Authentication failed at {url} (HTTP {r.status_code})")
            if r.status_code != 429 and r.status_code < 500:
                raise SyncError(f"{url} answered HTTP {r.status_code}")
            last = f"HTTP {r.status_code}"
            if r.status_code == 429 and r.headers.get("Retry-After", "").isdigit():
                sleep(min(10, int(r.headers["Retry-After"])))
                continue
        if i < attempts - 1:
            sleep(0.5 * 2**i)
    raise SyncError(f"{url} unreachable after {attempts} attempts ({last})")


def fetch(db: Session, source: IntegrationSource, entity: str, since: str | None,
          client: httpx.Client | None = None) -> list[dict]:
    cfg = source.config or {}
    url = f"{base_url(db, source)}/{cfg.get('resources', {}).get(entity) or entity.replace('_', '-')}"
    headers = _headers(source)
    limit = settings.INTEGRATION_MAX_RECORDS_PER_REQUEST
    own = client is None
    client = client or client_factory()
    try:
        out: list[dict] = []
        page = 1
        while True:
            params = {"page": page, "page_size": cfg.get("page_size") or 200}
            if since:
                params[cfg.get("since_param") or "updated_since"] = since
            body = _get(client, url, params, headers)
            items = body.get(cfg.get("items_key") or "items") if isinstance(body, dict) else None
            if not isinstance(items, list):
                raise SyncError(f"{url}: response has no '{cfg.get('items_key') or 'items'}' list")
            out.extend(items)
            nxt = body.get(cfg.get("next_key") or "next_page")
            if not nxt or not items or len(out) >= limit:
                break  # beyond the limit: the checkpoint advances to what was received; the next run continues
            page = int(nxt)
            if page > 10_000:
                raise SyncError("Too many pages")
        return out[:limit]
    finally:
        if own:
            client.close()


def test_connection(db: Session, source: IntegrationSource) -> dict:
    """Checks what can be checked without changing anything."""
    if source.connector != "rest_pull":
        from app.models import IntegrationCredential

        n = db.query(IntegrationCredential).filter(IntegrationCredential.source_id == source.id,
                                                   IntegrationCredential.revoked_at.is_(None)).count()
        ok = source.connector == "upload" or n > 0
        return {"ok": ok, "message": "Upload source: nothing to connect to — upload a file." if source.connector == "upload"
                else (f"{n} active API key(s); the hospital system pushes to POST /api/ingest/v1/<entity>." if n
                      else "No active API key — create one so the hospital system can push data."), "latency_ms": None}
    t0 = time.perf_counter()
    try:
        url = f"{base_url(db, source)}/{(source.config or {}).get('health_path') or 'health'}"
        with client_factory() as c:
            body = _get(c, url, {}, _headers(source))
    except SyncError as e:
        return {"ok": False, "message": str(e), "latency_ms": round((time.perf_counter() - t0) * 1000)}
    sim = isinstance(body, dict) and body.get("simulated")
    return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000),
            "message": f"Connected to {body.get('system', 'the remote system') if isinstance(body, dict) else url}"
                       + (" — SIMULATED reference ERP, not a real hospital system" if sim else "")}


def sync_source(db: Session, source: IntegrationSource, user: User | None, trigger: str, entities: list[str] | None = None,
                full: bool = False, dry_run: bool = False, commit: bool = True) -> list[SyncRun]:
    """Pull every configured entity (in dependency order). One run per entity; a failed fetch is a FAILED run."""
    if source.connector != "rest_pull":
        raise SyncError("Only rest_pull sources are synced by MedFlow; api_push sources send data themselves and upload "
                        "sources take files")
    if not source.enabled:
        raise SyncError("This source is disabled")
    wanted = [e for e in ORDER if e in source.entities and (entities is None or e in entities)]
    if not wanted:
        raise SyncError("None of the requested entities is configured on this source")
    runs = []
    with client_factory() as client:
        for entity in wanted:
            since = None if full else engine.get_checkpoint(db, source, entity)
            mode = "initial" if since is None else "incremental"
            try:
                records = fetch(db, source, entity, since, client)
            except SyncError as e:
                runs.append(engine.fail_run(db, source, entity, trigger, user, str(e), mode, since))
            else:
                runs.append(engine.run_sync(db, source, entity, records, trigger=trigger, user=user, mode=mode,
                                            dry_run=dry_run, checkpoint_before=since))
            if commit:
                db.commit()
    return runs


def due_sources(db: Session, now=None) -> list[IntegrationSource]:
    """Enabled, scheduled rest_pull sources of active hospitals whose interval has elapsed."""
    now = now or utcnow()
    out = []
    q = (select(IntegrationSource).join(Hospital, Hospital.id == IntegrationSource.hospital_id)
         .where(IntegrationSource.enabled.is_(True), IntegrationSource.connector == "rest_pull",
                Hospital.status == TenantStatus.ACTIVE).order_by(IntegrationSource.hospital_id, IntegrationSource.id))
    for s in db.scalars(q):
        every = (s.config or {}).get("schedule_minutes")
        if not every:
            continue
        last = s.last_run_at
        if last is not None and last.tzinfo is None:
            from datetime import UTC

            last = last.replace(tzinfo=UTC)
        if last is None or now - last >= timedelta(minutes=every):
            out.append(s)
    return out
