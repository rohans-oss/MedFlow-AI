"""V9 — integration sources: creation (with a service account), configuration validation and monitoring."""

import re
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlparse

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import utcnow
from app.integrations.entities import ENTITIES, ORDER
from app.models import (
    IntegrationCredential,
    IntegrationSource,
    ReconciliationIssue,
    SyncRun,
    User,
)

CONNECTORS = ("upload", "api_push", "rest_pull")
SYSTEM_TYPES = ("erp", "inventory", "procurement", "spreadsheet", "other")
REFERENCE_ERP = "reference-erp"
ENV_NAME = re.compile(r"^(MEDFLOW_INTEGRATION_[A-Z0-9_]{1,60}|REFERENCE_ERP_TOKEN)$")
PATH = re.compile(r"^[A-Za-z0-9_\-/.]{1,120}$")


class ConfigError(ValueError):
    pass


def _unusable_password() -> str:
    # a valid bcrypt hash of a random 256-bit secret nobody ever sees (the account is also inactive)
    return bcrypt.hashpw(secrets.token_urlsafe(32).encode(), bcrypt.gensalt(rounds=4)).decode()


def ensure_service_user(db: Session, source: IntegrationSource) -> User:
    """Each source acts through its own account (performed_by on ledger movements). It is inactive — nobody can sign
    in with it — and has no hospital membership, so it grants no access to anything."""
    u = User(email=f"integration-{uuid.uuid4().hex[:12]}@system.medflow.local", full_name=f"Integration: {source.name}"[:200],
             hashed_password=_unusable_password(), is_active=False, hospital_id=None, role=None)
    db.add(u)
    db.flush()
    source.service_user_id = u.id
    return u


def validate_config(connector: str, config: dict | None) -> dict:
    """Normalise a source's non-secret configuration. Secrets are never accepted here."""
    c = dict(config or {})
    out: dict = {}
    tol = c.pop("reconciliation_tolerance", 0)
    if not isinstance(tol, int) or isinstance(tol, bool) or not 0 <= tol <= 1_000_000:
        raise ConfigError("reconciliation_tolerance must be a whole number ≥ 0")
    out["reconciliation_tolerance"] = tol
    for k in ("api_key", "token", "password", "secret", "bearer"):
        if any(k in key.lower() for key in c if key != "auth_env"):
            raise ConfigError("Secrets are not stored in the source configuration — put the token in an environment "
                              "variable and give its name in auth_env")
    if connector != "rest_pull":
        if c:
            raise ConfigError(f"Unknown setting(s) for a {connector} source: {', '.join(sorted(c))}")
        return out
    base = str(c.pop("base_url", "") or "").strip().rstrip("/")
    if base != REFERENCE_ERP:
        u = urlparse(base)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ConfigError("base_url must be an http(s) URL (or 'reference-erp' for the built-in simulator)")
        if u.hostname.lower() not in [h.lower() for h in settings.INTEGRATION_ALLOWED_HOSTS]:
            raise ConfigError(f"Host '{u.hostname}' is not in the operator's allowlist (INTEGRATION_ALLOWED_HOSTS)")
    out["base_url"] = base
    auth_env = c.pop("auth_env", None)
    if auth_env is not None and auth_env != "":
        if not isinstance(auth_env, str) or not ENV_NAME.match(auth_env):
            raise ConfigError("auth_env must name an environment variable MEDFLOW_INTEGRATION_… (or REFERENCE_ERP_TOKEN)")
        out["auth_env"] = auth_env
    resources = c.pop("resources", None) or {}
    if not isinstance(resources, dict) or any(k not in ENTITIES or not isinstance(v, str) or not PATH.match(v)
                                              for k, v in resources.items()):
        raise ConfigError("resources must map entity names to relative paths")
    if base == REFERENCE_ERP:  # the simulator's resource names (materials, vendors, stock…)
        from app.integrations.reference_erp import RESOURCE_OF

        resources = {**RESOURCE_OF, **resources}
    out["resources"] = resources
    for key, lo, hi, default in (("page_size", 1, 1000, 200), ("schedule_minutes", 5, 7 * 24 * 60, None)):
        v = c.pop(key, default)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi):
            raise ConfigError(f"{key} must be between {lo} and {hi}")
        out[key] = v
    for key, default in (("since_param", "updated_since"), ("items_key", "items"), ("next_key", "next_page"),
                         ("health_path", "health")):
        v = str(c.pop(key, default) or default)
        if not PATH.match(v):
            raise ConfigError(f"{key} is not valid")
        out[key] = v
    if c:
        raise ConfigError(f"Unknown setting(s): {', '.join(sorted(c))}")
    return out


def validate_entities(entities: list[str]) -> list[str]:
    bad = [e for e in entities if e not in ENTITIES]
    if bad:
        raise ConfigError(f"Unknown entit{'y' if len(bad) == 1 else 'ies'}: {', '.join(bad)}")
    if not entities:
        raise ConfigError("Choose at least one entity")
    return [e for e in ORDER if e in entities]


def create_source(db: Session, hospital_id: int, user: User | None, name: str, system_type: str, connector: str,
                  entities: list[str], config: dict | None, enabled: bool = True, is_simulated: bool = False) -> IntegrationSource:
    if connector not in CONNECTORS:
        raise ConfigError(f"connector must be one of {', '.join(CONNECTORS)}")
    if system_type not in SYSTEM_TYPES:
        raise ConfigError(f"system_type must be one of {', '.join(SYSTEM_TYPES)}")
    if not name.strip():
        raise ConfigError("name is required")
    if db.scalar(select(IntegrationSource.id).where(IntegrationSource.hospital_id == hospital_id,
                                                    func.lower(IntegrationSource.name) == name.strip().lower())):
        raise ConfigError(f"A source named '{name.strip()}' already exists")
    cfg = validate_config(connector, config)
    src = IntegrationSource(hospital_id=hospital_id, name=name.strip()[:120], system_type=system_type, connector=connector,
                            entities=validate_entities(entities), config=cfg, enabled=enabled,
                            is_simulated=is_simulated or cfg.get("base_url") == REFERENCE_ERP,
                            created_by_id=user.id if user else None)
    db.add(src)
    db.flush()
    ensure_service_user(db, src)
    return src


# ---------------------------------------------------------------- monitoring


def freshness(source: IntegrationSource, now=None) -> dict:
    now = now or utcnow()
    last = source.last_success_at
    if last is not None and last.tzinfo is None:
        from datetime import UTC

        last = last.replace(tzinfo=UTC)
    sched = (source.config or {}).get("schedule_minutes")
    limit = timedelta(minutes=sched * 3) if sched else timedelta(hours=24)
    age = (now - last) if last else None
    return {"age_hours": round(age.total_seconds() / 3600, 1) if age is not None else None,
            "stale_after_hours": round(limit.total_seconds() / 3600, 1),
            "stale": age is None or age > limit}


def source_health(source: IntegrationSource, latest_statuses: list[str], fresh: dict) -> str:
    """Worst state over the latest run of each entity the source sends."""
    if not source.enabled:
        return "disabled"
    if not latest_statuses:
        return "never_run"
    if "FAILED" in latest_statuses:
        return "failing"
    if "PARTIAL" in latest_statuses or fresh["stale"]:
        return "degraded"
    return "healthy"


def overview(db: Session, hospital_id: int) -> dict:
    now = utcnow()
    since = now - timedelta(days=7)
    sources = db.scalars(select(IntegrationSource).where(IntegrationSource.hospital_id == hospital_id)
                         .order_by(IntegrationSource.name)).all()
    ids = [s.id for s in sources]
    week = {sid: (rec, cre, upd, unc, rej, n) for sid, rec, cre, upd, unc, rej, n in db.execute(
        select(SyncRun.source_id, func.sum(SyncRun.records_received), func.sum(SyncRun.records_created),
               func.sum(SyncRun.records_updated), func.sum(SyncRun.records_unchanged), func.sum(SyncRun.records_rejected),
               func.count(SyncRun.id))
        .where(SyncRun.hospital_id == hospital_id, SyncRun.started_at >= since, SyncRun.mode != "dry_run")
        .group_by(SyncRun.source_id))}
    open_issues = dict(db.execute(select(ReconciliationIssue.source_id, func.count(ReconciliationIssue.id))
                                  .where(ReconciliationIssue.hospital_id == hospital_id,
                                         ReconciliationIssue.status == "OPEN")
                                  .group_by(ReconciliationIssue.source_id)).all())
    keys = dict(db.execute(select(IntegrationCredential.source_id, func.count(IntegrationCredential.id))
                           .where(IntegrationCredential.hospital_id == hospital_id,
                                  IntegrationCredential.revoked_at.is_(None))
                           .group_by(IntegrationCredential.source_id)).all())
    latest: dict[int, dict[str, SyncRun]] = {i: {} for i in ids}
    for r in db.scalars(select(SyncRun).where(SyncRun.hospital_id == hospital_id, SyncRun.mode != "dry_run")
                        .order_by(SyncRun.started_at.desc(), SyncRun.id.desc()).limit(2000)):
        latest[r.source_id].setdefault(r.entity, r)
    rows = []
    for s in sources:
        fresh = freshness(s, now)
        w = week.get(s.id, (0, 0, 0, 0, 0, 0))
        rows.append({
            "id": s.id, "name": s.name, "system_type": s.system_type, "connector": s.connector, "enabled": s.enabled,
            "is_simulated": s.is_simulated, "entities": s.entities,
            "health": source_health(s, [r.status for e, r in latest[s.id].items() if e in s.entities], fresh), "last_run_at": s.last_run_at,
            "last_success_at": s.last_success_at, "last_failure_at": s.last_failure_at, "last_error": s.last_error,
            "freshness": fresh, "schedule_minutes": (s.config or {}).get("schedule_minutes"),
            "runs_7d": int(w[5] or 0), "received_7d": int(w[0] or 0), "created_7d": int(w[1] or 0),
            "updated_7d": int(w[2] or 0), "unchanged_7d": int(w[3] or 0), "rejected_7d": int(w[4] or 0),
            "open_reconciliation": int(open_issues.get(s.id, 0)), "active_keys": int(keys.get(s.id, 0)),
            "entity_status": [{"entity": e, "status": latest[s.id][e].status, "at": latest[s.id][e].started_at,
                               "run_id": latest[s.id][e].id, "rejected": latest[s.id][e].records_rejected}
                              for e in s.entities if e in latest[s.id]],
        })
    enabled = [r for r in rows if r["enabled"]]
    return {
        "sources": rows,
        "totals": {
            "sources": len(rows), "enabled": len(enabled),
            "healthy": sum(r["health"] == "healthy" for r in rows), "degraded": sum(r["health"] == "degraded" for r in rows),
            "failing": sum(r["health"] == "failing" for r in rows), "never_run": sum(r["health"] == "never_run" for r in rows),
            "received_7d": sum(r["received_7d"] for r in rows), "rejected_7d": sum(r["rejected_7d"] for r in rows),
            "created_7d": sum(r["created_7d"] for r in rows), "updated_7d": sum(r["updated_7d"] for r in rows),
            "open_reconciliation": sum(r["open_reconciliation"] for r in rows),
        },
        "reference_erp_enabled": settings.REFERENCE_ERP_ENABLED,
    }
