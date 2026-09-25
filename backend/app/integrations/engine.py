"""V9 — the sync engine: one run = one entity from one source.

For every incoming record: map (source field → MedFlow field, defaults) → validate (types, required fields, negative
quantities, dates) → duplicate / idempotency check (`external_refs`: same external id + same content = unchanged;
transactions re-sent with different content are rejected) → apply through the existing services inside a SAVEPOINT
(a bad record never leaves half a change behind) → count. Rejected records are stored with their raw content and
reasons so they can be inspected and retried. A dry run does all of it and rolls everything back.
"""

import hashlib
import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import utcnow
from app.integrations import fields as F
from app.integrations.entities import ENTITIES, Ctx, Entity
from app.integrations.fields import RowError
from app.models import (
    ExternalRef,
    IntegrationMapping,
    IntegrationSource,
    SyncCheckpoint,
    SyncRecord,
    SyncRun,
    User,
)
from app.services import audit

log = logging.getLogger(__name__)


class SyncError(ValueError):
    """The whole run cannot proceed (bad file, source misconfigured, remote system unreachable…)."""


# ---------------------------------------------------------------- mapping


def default_mapping(entity: str) -> dict[str, str]:
    return {f.name: f.name for f in ENTITIES[entity].fields}


def effective_mapping(db: Session, source: IntegrationSource, entity: str) -> tuple[dict, dict, str | None]:
    """(field_map medflow→source, defaults, date_format). Unmapped fields default to the MedFlow field name."""
    m = db.scalar(select(IntegrationMapping).where(IntegrationMapping.source_id == source.id,
                                                   IntegrationMapping.entity == entity))
    fm = default_mapping(entity)
    if m is not None:
        fm.update({k: v for k, v in (m.field_map or {}).items() if k in fm and v})
        return fm, dict(m.defaults or {}), m.date_format
    return fm, {}, None


def map_and_validate(ent: Entity, raw: dict, field_map: dict, defaults: dict, date_format: str | None) -> dict:
    """Raw source record → validated MedFlow values. Raises RowError listing every problem in the record."""
    if not isinstance(raw, dict):
        raise RowError("record is not an object", F.INVALID_VALUE)
    errors: list[dict] = []
    out: dict = {}
    for f in ent.fields:
        src = field_map.get(f.name, f.name)
        v = raw.get(src)
        if F.blank(v):
            v = defaults.get(f.name)
        if F.blank(v):
            if f.required:
                errors.append({"field": f.name, "code": F.MISSING_FIELD,
                               "message": f"{f.name} is required (source field '{src}')"})
            continue
        try:
            out[f.name] = F.parse_value(f, v, date_format)
        except ValueError as e:
            code = F.INVALID_DATE if f.kind in ("date", "datetime") else F.INVALID_VALUE
            if f.kind == "int" and _is_negative(v):
                code = F.NEGATIVE_QUANTITY if f.name in ("quantity", "moq") else F.INVALID_VALUE
            errors.append({"field": f.name, "code": code, "message": f"{f.name} ('{v}') {e}"
                           if f.kind not in ("date", "datetime") else f"{f.name}: {e}"})
    if errors:
        raise RowError(errors)
    return out


def _is_negative(v) -> bool:
    try:
        return Decimal(str(v).strip().replace(",", "")) < 0
    except Exception:  # noqa: BLE001
        return False


def _jsonable(v):
    if isinstance(v, tuple):  # parsed datetime (dt, has_time)
        return v[0].isoformat() if v[1] else F.local_date(v[0]).isoformat()
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def record_hash(values: dict) -> str:
    body = {k: _jsonable(v) for k, v in sorted(values.items()) if k != "updated_at"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _raw_jsonable(raw) -> dict:
    if not isinstance(raw, dict):
        return {"value": str(raw)[:500]}
    return {str(k)[:100]: (v if isinstance(v, str | int | float | bool) or v is None else str(v))
            for k, v in list(raw.items())[:100]}


# ---------------------------------------------------------------- run


def run_sync(db: Session, source: IntegrationSource, entity: str, records: list, *, trigger: str,
             user: User | None = None, mode: str = "incremental", dry_run: bool = False, row_numbers: list[int] | None = None,
             file_name: str | None = None, file_sha256: str | None = None, parent: SyncRun | None = None,
             checkpoint_before: str | None = None, retried: list[SyncRecord] | None = None) -> SyncRun:
    """Process `records` (raw dicts from the source) for one entity. Flushes; the caller commits."""
    if entity not in ENTITIES:
        raise SyncError(f"Unknown entity '{entity}'")
    ent = ENTITIES[entity]
    run = SyncRun(hospital_id=source.hospital_id, source_id=source.id, entity=entity,
                  mode="dry_run" if dry_run else mode, trigger=trigger, status="RUNNING", started_at=utcnow(),
                  records_received=len(records), file_name=file_name, file_sha256=file_sha256,
                  parent_run_id=parent.id if parent else None, triggered_by_id=user.id if user else None,
                  checkpoint_before=checkpoint_before)
    db.add(run)
    db.flush()
    reasons: dict[str, int] = {}
    rejected: list[tuple[int, str | None, dict, list]] = []
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    fatal = None
    max_cursor: datetime | None = None
    ctx = Ctx(db, source, _actor(db, source), run, tolerance=int((source.config or {}).get("reconciliation_tolerance", 0)))

    if entity not in (source.entities or []):
        fatal = f"This source is not configured to send '{entity}'"
    elif len(records) > settings.INTEGRATION_MAX_RECORDS_PER_REQUEST:
        fatal = f"Too many records ({len(records)}); at most {settings.INTEGRATION_MAX_RECORDS_PER_REQUEST} per run"
    if fatal is None:
        fm, defaults, date_format = effective_mapping(db, source, entity)
        numbers = row_numbers or list(range(1, len(records) + 1))
        # 1) map + validate every record
        parsed: list[tuple[int, dict, dict]] = []
        for n, raw in zip(numbers, records, strict=True):
            try:
                parsed.append((n, raw, map_and_validate(ent, raw, fm, defaults, date_format)))
            except RowError as e:
                rejected.append((n, _ext_hint(raw, fm, ent), raw, e.errors))
        for _n, _raw, v in parsed:
            if v.get("updated_at"):
                ts = v["updated_at"][0]
                max_cursor = ts if max_cursor is None or ts > max_cursor else max_cursor
        if ent.sort_key:  # transactions are applied in chronological order
            parsed.sort(key=lambda t: t[2][ent.sort_key][0])
        outer = db.begin_nested() if dry_run else None
        seen: dict[str, tuple[int, str]] = {}
        # 2) duplicates, idempotency, apply
        for n, raw, v in parsed:
            key = ent.key(v)
            h = record_hash(v)
            if key in seen:
                first, first_hash = seen[key]
                msg = (f"duplicate record: '{key}' already appears in this batch (row {first})"
                       + ("" if first_hash == h else " with different values"))
                rejected.append((n, key, raw, [{"field": None, "code": F.DUPLICATE, "message": msg}]))
                continue
            seen[key] = (n, h)
            ref = db.scalar(select(ExternalRef).where(ExternalRef.source_id == source.id, ExternalRef.entity == entity,
                                                      ExternalRef.external_id == key))
            if ref is not None and ref.record_hash == h:
                counts["unchanged"] += 1
                ref.last_run_id = run.id
                continue
            if ref is not None and ent.transactional:
                rejected.append((n, key, raw, [{"field": None, "code": F.CHANGED_TRANSACTION, "message":
                    f"'{key}' was already applied with different values; the ledger is append-only — send the correction "
                    "as a new transaction (new id)"}]))
                continue
            sp = db.begin_nested()
            try:
                outcome, target_type, target_id = ent.apply(ctx, v)
                db.flush()
            except RowError as e:
                sp.rollback()
                rejected.append((n, key, raw, e.errors))
                continue
            except Exception as e:  # noqa: BLE001 — a database/tenant error on one record must not stop the run
                sp.rollback()
                log.warning("integration record failed: %s", e)
                rejected.append((n, key, raw, [{"field": None, "code": F.CONFLICT,
                                                "message": f"could not be applied: {type(e).__name__}"}]))
                continue
            sp.commit()
            counts[outcome] += 1
            if ref is None:
                db.add(ExternalRef(hospital_id=source.hospital_id, source_id=source.id, entity=entity, external_id=key,
                                   record_hash=h, target_type=target_type, target_id=target_id, first_run_id=run.id,
                                   last_run_id=run.id, updated_at=utcnow()))
            else:
                ref.record_hash, ref.target_type, ref.target_id = h, target_type, target_id
                ref.last_run_id, ref.updated_at = run.id, utcnow()
            db.flush()
        if outer is not None:
            outer.rollback()
    # 3) results
    for _n, _k, _raw, errs in rejected:
        for e in errs:
            reasons[e["code"]] = reasons.get(e["code"], 0) + 1
    run = db.get(SyncRun, run.id)  # reload after a dry-run rollback
    run.records_created, run.records_updated, run.records_unchanged = counts["created"], counts["updated"], counts["unchanged"]
    run.records_rejected = len(rejected) if fatal is None else len(records)
    run.completed_at = utcnow()
    if fatal:
        run.status = "FAILED"
    elif rejected and len(rejected) == len(records):
        run.status = "FAILED"
    elif rejected:
        run.status = "PARTIAL"
    else:
        run.status = "SUCCESS"
    run.error_summary = {"fatal": fatal, "reasons": reasons, "info": ctx.info} if (fatal or reasons or ctx.info) else None
    for n, key, raw, errs in rejected:
        db.add(SyncRecord(hospital_id=source.hospital_id, run_id=run.id, row_number=n, external_id=(key or None) and key[:128],
                          raw=_raw_jsonable(raw), errors=errs))
    for r in retried or []:
        r.status, r.retried_in_run_id = "RETRIED", run.id
    cursor = max_cursor.astimezone(UTC).isoformat() if max_cursor else None
    if not dry_run and fatal is None:
        if cursor and (checkpoint_before is None or cursor > checkpoint_before):
            set_checkpoint(db, source, entity, cursor, run)
            run.checkpoint_after = cursor
        else:
            run.checkpoint_after = checkpoint_before
        if ctx.touched_items:
            from app.services import alerts  # local import: alerts pull in the risk engine

            alerts.evaluate(db, source.hospital_id, sorted(ctx.touched_items))
    if not dry_run:
        mark_source(source, run.status, run.completed_at, _summary_text(run))
    audit.record(db, user, "integration.dry_run" if dry_run else "integration.sync", "sync_run", run.id, {
        "source": source.name, "source_id": source.id, "entity": entity, "trigger": trigger, "mode": run.mode,
        "status": run.status, "received": run.records_received, "created": run.records_created,
        "updated": run.records_updated, "unchanged": run.records_unchanged, "rejected": run.records_rejected,
        "file": file_name}, hospital_id=source.hospital_id)
    db.flush()
    return run


def _ext_hint(raw, fm: dict, ent: Entity) -> str | None:
    """Best-effort external id of a record that failed validation (for display only)."""
    if not isinstance(raw, dict):
        return None
    for name in ("external_id", "po_number", "code", "item_code"):
        if any(f.name == name for f in ent.fields):
            v = raw.get(fm.get(name, name))
            if not F.blank(v):
                return str(v).strip()[:128]
    return None


def _summary_text(run: SyncRun) -> str | None:
    s = run.error_summary or {}
    if s.get("fatal"):
        return s["fatal"]
    if run.records_rejected:
        top = ", ".join(f"{k} ×{v}" for k, v in sorted(s.get("reasons", {}).items(), key=lambda kv: -kv[1])[:3])
        return f"{run.entity}: {run.records_rejected} of {run.records_received} records rejected ({top})"
    return None


def mark_source(source: IntegrationSource, status: str, at: datetime, error: str | None) -> None:
    source.last_run_at = at
    if status in ("SUCCESS", "PARTIAL"):
        source.last_success_at = at
        source.last_error = error if status == "PARTIAL" else None
    else:
        source.last_failure_at = at
        source.last_error = error


def fail_run(db: Session, source: IntegrationSource, entity: str, trigger: str, user: User | None, message: str,
             mode: str = "incremental", checkpoint_before: str | None = None) -> SyncRun:
    """Record a run that could not start (connection refused, HTTP 401, bad file…)."""
    now = utcnow()
    run = SyncRun(hospital_id=source.hospital_id, source_id=source.id, entity=entity, mode=mode, trigger=trigger,
                  status="FAILED", started_at=now, completed_at=now, error_summary={"fatal": message, "reasons": {}},
                  triggered_by_id=user.id if user else None, checkpoint_before=checkpoint_before,
                  checkpoint_after=checkpoint_before)
    db.add(run)
    db.flush()
    mark_source(source, "FAILED", now, message)
    audit.record(db, user, "integration.sync", "sync_run", run.id, {
        "source": source.name, "source_id": source.id, "entity": entity, "trigger": trigger, "status": "FAILED",
        "error": message}, hospital_id=source.hospital_id)
    return run


def retry(db: Session, run: SyncRun, user: User | None) -> SyncRun:
    """Re-process the records a run rejected (e.g. after the missing item was added or the mapping fixed)."""
    if run.mode == "dry_run":
        raise SyncError("A dry run wrote nothing — run the import for real instead of retrying it")
    recs = db.scalars(select(SyncRecord).where(SyncRecord.run_id == run.id, SyncRecord.status == "REJECTED")
                      .order_by(SyncRecord.row_number, SyncRecord.id)).all()
    if not recs:
        raise SyncError("This run has no rejected records left to retry")
    return run_sync(db, run.source, run.entity, [r.raw for r in recs], trigger="retry", user=user, mode="retry",
                    row_numbers=[r.row_number for r in recs], parent=run, retried=list(recs),
                    file_name=run.file_name)


# ---------------------------------------------------------------- checkpoints / service account


def get_checkpoint(db: Session, source: IntegrationSource, entity: str) -> str | None:
    cp = db.scalar(select(SyncCheckpoint).where(SyncCheckpoint.source_id == source.id, SyncCheckpoint.entity == entity))
    return cp.cursor if cp else None


def set_checkpoint(db: Session, source: IntegrationSource, entity: str, cursor: str | None, run: SyncRun | None) -> None:
    cp = db.scalar(select(SyncCheckpoint).where(SyncCheckpoint.source_id == source.id, SyncCheckpoint.entity == entity))
    if cp is None:
        cp = SyncCheckpoint(hospital_id=source.hospital_id, source_id=source.id, entity=entity)
        db.add(cp)
    cp.cursor, cp.run_id, cp.updated_at = cursor, run.id if run else None, utcnow()
    db.flush()


def _actor(db: Session, source: IntegrationSource) -> User:
    u = db.get(User, source.service_user_id) if source.service_user_id else None
    if u is None:
        from app.integrations.sources import ensure_service_user

        u = ensure_service_user(db, source)
    return u
