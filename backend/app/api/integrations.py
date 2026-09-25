"""V9 — integrations & data exchange API (hospital-scoped; the hospital is always the caller's active hospital).

Sources, field mappings, API keys, file upload, pull syncs, run history with rejected records + retry, reconciliation.
Inbound pushes from hospital systems use a separate, API-key-authenticated router (`ingest_router`).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DB, client_ip, get_owned, require
from app.core.config import settings
from app.core.permissions import INTEGRATIONS_MANAGE, INTEGRATIONS_RUN, STOCK_RECEIVE, permissions_for
from app.db.base import utcnow
from app.integrations import connectors, credentials, engine, readers, reconciliation, sources
from app.integrations.engine import SyncError
from app.integrations.entities import ENTITIES, ORDER
from app.integrations.sources import ConfigError
from app.models import (
    IntegrationCredential,
    IntegrationMapping,
    IntegrationSource,
    ReconciliationIssue,
    SyncRecord,
    SyncRun,
    User,
)
from app.schemas.common import Page
from app.schemas.integrations import (
    CredentialCreate,
    CredentialCreated,
    CredentialOut,
    EntityField,
    EntityInfo,
    IngestRequest,
    IngestResult,
    MappingOut,
    MappingUpdate,
    Overview,
    ReconciliationOut,
    ReconItem,
    ResolveRequest,
    SourceCreate,
    SourceOut,
    SourceUpdate,
    SyncRecordOut,
    SyncRequest,
    SyncRunDetail,
    SyncRunOut,
    TestResult,
    UserRef,
)
from app.services import alerts, audit, stock

router = APIRouter(prefix="/integrations", tags=["integrations"])
Runner = Annotated[User, Depends(require(INTEGRATIONS_RUN))]
Manager = Annotated[User, Depends(require(INTEGRATIONS_MANAGE))]


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))


def _source(db: Session, source_id: int, user: User) -> IntegrationSource:
    return get_owned(db, IntegrationSource, source_id, user.hospital_id, "Integration source")


def _user_ref(db: Session, uid: int | None) -> UserRef | None:
    u = db.get(User, uid) if uid else None
    return UserRef(id=u.id, full_name=u.full_name) if u else None


def run_out(db: Session, r: SyncRun) -> SyncRunOut:
    return SyncRunOut(id=r.id, source_id=r.source_id, source_name=r.source.name, entity=r.entity, mode=r.mode,
                      trigger=r.trigger, status=r.status, started_at=r.started_at, completed_at=r.completed_at,
                      records_received=r.records_received, records_created=r.records_created,
                      records_updated=r.records_updated, records_unchanged=r.records_unchanged,
                      records_rejected=r.records_rejected, error_summary=r.error_summary,
                      checkpoint_before=r.checkpoint_before, checkpoint_after=r.checkpoint_after, file_name=r.file_name,
                      file_sha256=r.file_sha256, parent_run_id=r.parent_run_id,
                      triggered_by=_user_ref(db, r.triggered_by_id))


def run_detail(db: Session, r: SyncRun) -> SyncRunDetail:
    recs = db.scalars(select(SyncRecord).where(SyncRecord.run_id == r.id).order_by(SyncRecord.row_number, SyncRecord.id)
                      .limit(1000)).all()
    return SyncRunDetail(**run_out(db, r).model_dump(), rejected=[SyncRecordOut.model_validate(x) for x in recs])


# ---------------------------------------------------------------- catalogue of entities / monitoring


@router.get("/entities", response_model=list[EntityInfo])
def entities(_: Runner):
    return [EntityInfo(name=e.name, label=e.label, description=e.description, transactional=e.transactional,
                       fields=[EntityField(name=f.name, kind=f.kind, required=f.required, description=f.description)
                               for f in e.fields]) for e in (ENTITIES[n] for n in ORDER)]


@router.get("/overview", response_model=Overview)
def overview(db: DB, user: Runner):
    return sources.overview(db, user.hospital_id)


# ---------------------------------------------------------------- sources


@router.get("/sources", response_model=list[SourceOut])
def list_sources(db: DB, user: Runner):
    return db.scalars(select(IntegrationSource).where(IntegrationSource.hospital_id == user.hospital_id)
                      .order_by(IntegrationSource.name)).all()


@router.post("/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(body: SourceCreate, request: Request, db: DB, user: Manager):
    try:
        src = sources.create_source(db, user.hospital_id, user, body.name, body.system_type, body.connector, body.entities,
                                    body.config, body.enabled)
    except ConfigError as e:
        raise _bad(e) from e
    audit.record(db, user, "integration.source.create", "integration_source", src.id,
                 {"name": src.name, "connector": src.connector, "entities": src.entities, "config": src.config},
                 client_ip(request))
    db.commit()
    return src


@router.get("/sources/{source_id}", response_model=SourceOut)
def get_source(source_id: int, db: DB, user: Runner):
    return _source(db, source_id, user)


@router.patch("/sources/{source_id}", response_model=SourceOut)
def update_source(source_id: int, body: SourceUpdate, request: Request, db: DB, user: Manager):
    src = _source(db, source_id, user)
    changes = body.model_dump(exclude_unset=True)
    try:
        if "name" in changes and changes["name"].strip().lower() != src.name.lower():
            if db.scalar(select(IntegrationSource.id).where(IntegrationSource.hospital_id == user.hospital_id,
                                                            func.lower(IntegrationSource.name) == changes["name"].strip().lower())):
                raise ConfigError(f"A source named '{changes['name'].strip()}' already exists")
            src.name = changes["name"].strip()
        if changes.get("system_type"):
            src.system_type = changes["system_type"]
        if changes.get("entities") is not None:
            src.entities = sources.validate_entities(changes["entities"])
        if changes.get("config") is not None:
            src.config = sources.validate_config(src.connector, changes["config"])
            if src.config.get("base_url") == sources.REFERENCE_ERP:
                src.is_simulated = True
        if changes.get("enabled") is not None:
            src.enabled = changes["enabled"]
    except ConfigError as e:
        raise _bad(e) from e
    audit.record(db, user, "integration.source.update", "integration_source", src.id, changes, client_ip(request))
    db.commit()
    return src


@router.post("/sources/{source_id}/test", response_model=TestResult)
def test_source(source_id: int, db: DB, user: Runner):
    return connectors.test_connection(db, _source(db, source_id, user))


# ---------------------------------------------------------------- mappings


def _mapping_out(db: Session, src: IntegrationSource, entity: str) -> MappingOut:
    fm, defaults, fmt = engine.effective_mapping(db, src, entity)
    customized = db.scalar(select(IntegrationMapping.id).where(IntegrationMapping.source_id == src.id,
                                                              IntegrationMapping.entity == entity)) is not None
    return MappingOut(entity=entity, field_map=fm, defaults=defaults, date_format=fmt, customized=customized)


@router.get("/sources/{source_id}/mappings", response_model=list[MappingOut])
def get_mappings(source_id: int, db: DB, user: Runner):
    src = _source(db, source_id, user)
    return [_mapping_out(db, src, e) for e in src.entities]


@router.put("/sources/{source_id}/mappings/{entity}", response_model=MappingOut)
def put_mapping(source_id: int, entity: str, body: MappingUpdate, request: Request, db: DB, user: Manager):
    src = _source(db, source_id, user)
    if entity not in src.entities:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This source does not send that entity")
    ent = ENTITIES[entity]
    names = {f.name for f in ent.fields}
    unknown = sorted((set(body.field_map) | set(body.defaults)) - names)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown MedFlow field(s) for {entity}: {', '.join(unknown)}")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in body.field_map.values()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Source field names must be 1–100 characters")
    for k, v in body.defaults.items():  # a default must itself be a valid value
        try:
            from app.integrations.fields import parse_value

            parse_value(ent.field(k), v, body.date_format)
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Default for {k}: {e}") from e
    m = db.scalar(select(IntegrationMapping).where(IntegrationMapping.source_id == src.id, IntegrationMapping.entity == entity))
    if m is None:
        m = IntegrationMapping(hospital_id=src.hospital_id, source_id=src.id, entity=entity)
        db.add(m)
    m.field_map = {k: v.strip() for k, v in body.field_map.items()}
    m.defaults, m.date_format, m.updated_by_id = body.defaults, body.date_format or None, user.id
    audit.record(db, user, "integration.mapping.update", "integration_source", src.id,
                 {"entity": entity, **body.model_dump()}, client_ip(request))
    db.commit()
    return _mapping_out(db, src, entity)


@router.delete("/sources/{source_id}/mappings/{entity}", response_model=MappingOut)
def reset_mapping(source_id: int, entity: str, request: Request, db: DB, user: Manager):
    src = _source(db, source_id, user)
    if entity not in src.entities:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This source does not send that entity")
    m = db.scalar(select(IntegrationMapping).where(IntegrationMapping.source_id == src.id, IntegrationMapping.entity == entity))
    if m is not None:
        db.delete(m)
        audit.record(db, user, "integration.mapping.reset", "integration_source", src.id, {"entity": entity},
                     client_ip(request))
        db.commit()
    return _mapping_out(db, src, entity)


# ---------------------------------------------------------------- API keys (api_push)


@router.get("/sources/{source_id}/credentials", response_model=list[CredentialOut])
def list_credentials(source_id: int, db: DB, user: Manager):
    src = _source(db, source_id, user)
    return db.scalars(select(IntegrationCredential).where(IntegrationCredential.source_id == src.id)
                      .order_by(IntegrationCredential.created_at.desc())).all()


@router.post("/sources/{source_id}/credentials", response_model=CredentialCreated, status_code=status.HTTP_201_CREATED)
def create_credential(source_id: int, body: CredentialCreate, request: Request, db: DB, user: Manager):
    src = _source(db, source_id, user)
    if src.connector != "api_push":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "API keys are only for api_push sources")
    cred, key = credentials.create(db, src, body.label, user)
    audit.record(db, user, "integration.key.create", "integration_credential", cred.id,
                 {"source_id": src.id, "label": cred.label, "key_prefix": cred.key_prefix}, client_ip(request))
    db.commit()
    return CredentialCreated(**CredentialOut.model_validate(cred).model_dump(), api_key=key)


@router.post("/credentials/{credential_id}/revoke", response_model=CredentialOut)
def revoke_credential(credential_id: int, request: Request, db: DB, user: Manager):
    cred = get_owned(db, IntegrationCredential, credential_id, user.hospital_id, "API key")
    if cred.revoked_at is None:
        cred.revoked_at = utcnow()
        audit.record(db, user, "integration.key.revoke", "integration_credential", cred.id,
                     {"source_id": cred.source_id, "key_prefix": cred.key_prefix}, client_ip(request))
        db.commit()
    return cred


# ---------------------------------------------------------------- data in: upload / pull / retry


@router.post("/sources/{source_id}/upload", response_model=SyncRunDetail, status_code=status.HTTP_201_CREATED)
def upload(source_id: int, db: DB, user: Runner, entity: Annotated[str, Form()], file: Annotated[UploadFile, File()],
           dry_run: Annotated[bool, Form()] = False):
    src = _source(db, source_id, user)
    if not src.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "This source is disabled")
    if entity not in src.entities:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"This source is not configured to send '{entity}'")
    limit = settings.INTEGRATION_MAX_UPLOAD_MB * 1024 * 1024
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Files up to {settings.INTEGRATION_MAX_UPLOAD_MB} MB")
    name = (file.filename or "upload")[:255]
    try:
        rows, lines, digest = readers.read(name, content, entity)
    except SyncError as e:
        run = engine.fail_run(db, src, entity, "upload", user, str(e))
        run.file_name = name
        db.commit()
        raise _bad(e) from e
    run = engine.run_sync(db, src, entity, rows, trigger="upload", user=user, mode="initial", dry_run=dry_run,
                          row_numbers=lines, file_name=name, file_sha256=digest)
    db.commit()
    return run_detail(db, run)


@router.post("/sources/{source_id}/sync", response_model=list[SyncRunOut])
def sync(source_id: int, body: SyncRequest, db: DB, user: Runner):
    src = _source(db, source_id, user)
    try:
        runs = connectors.sync_source(db, src, user, "manual", body.entities, body.full, body.dry_run)
    except SyncError as e:
        raise _bad(e) from e
    return [run_out(db, r) for r in runs]


@router.get("/runs", response_model=Page[SyncRunOut])
def list_runs(db: DB, user: Runner, source_id: int | None = None, entity: str | None = None,
              status_: Annotated[str | None, Query(alias="status")] = None, page: int = Query(1, ge=1),
              page_size: int = Query(50, ge=1, le=200)):
    q = select(SyncRun).where(SyncRun.hospital_id == user.hospital_id)
    if source_id is not None:
        q = q.where(SyncRun.source_id == source_id)
    if entity:
        q = q.where(SyncRun.entity == entity)
    if status_:
        q = q.where(SyncRun.status == status_.upper())
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[run_out(db, r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/runs/{run_id}", response_model=SyncRunDetail)
def get_run(run_id: int, db: DB, user: Runner):
    return run_detail(db, get_owned(db, SyncRun, run_id, user.hospital_id, "Sync run"))


@router.post("/runs/{run_id}/retry", response_model=SyncRunDetail, status_code=status.HTTP_201_CREATED)
def retry_run(run_id: int, db: DB, user: Runner):
    run = get_owned(db, SyncRun, run_id, user.hospital_id, "Sync run")
    if not run.source.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "This source is disabled")
    try:
        new = engine.retry(db, run, user)
    except SyncError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    db.commit()
    return run_detail(db, new)


# ---------------------------------------------------------------- reconciliation


def _recon_out(db: Session, i: ReconciliationIssue, now_levels: dict) -> ReconciliationOut:
    c = i.consumable
    lv = now_levels.get(c.id)
    return ReconciliationOut(id=i.id, source_id=i.source_id, source_name=i.source.name, run_id=i.run_id,
                             item=ReconItem(id=c.id, sku=c.sku, name=c.name, unit=c.unit), as_of=i.as_of,
                             external_quantity=i.external_quantity, medflow_quantity=i.medflow_quantity,
                             difference=i.difference, medflow_now=(lv.usable + lv.expired) if lv else 0, status=i.status,
                             resolution=i.resolution, note=i.note, resolved_by=_user_ref(db, i.resolved_by_id),
                             resolved_at=i.resolved_at, created_at=i.created_at)


@router.get("/reconciliation", response_model=list[ReconciliationOut])
def list_reconciliation(db: DB, user: Runner, status_: Annotated[str, Query(alias="status")] = "OPEN",
                        source_id: int | None = None):
    q = select(ReconciliationIssue).where(ReconciliationIssue.hospital_id == user.hospital_id)
    if status_.upper() != "ALL":
        q = q.where(ReconciliationIssue.status == status_.upper())
    if source_id is not None:
        q = q.where(ReconciliationIssue.source_id == source_id)
    rows = db.scalars(q.order_by(ReconciliationIssue.status, ReconciliationIssue.created_at.desc()).limit(500)).all()
    levels = stock.stock_levels(db, user.hospital_id, [r.consumable_id for r in rows])
    return [_recon_out(db, r, levels) for r in rows]


@router.post("/reconciliation/{issue_id}/resolve", response_model=ReconciliationOut)
def resolve_issue(issue_id: int, body: ResolveRequest, request: Request, db: DB, user: Runner):
    issue = get_owned(db, ReconciliationIssue, issue_id, user.hospital_id, "Reconciliation issue")
    if body.action == "adjust" and STOCK_RECEIVE not in permissions_for(user.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Adjusting stock needs the stock-receive permission (inventory "
                                                       "manager or admin)")
    moves = reconciliation.resolve(db, issue, user, body.action, body.note)
    audit.record(db, user, "integration.reconciliation.resolve", "reconciliation_issue", issue.id,
                 {"action": body.action, "note": body.note, "difference": issue.difference, "item": issue.consumable.sku,
                  "movements": [m.id for m in moves]}, client_ip(request))
    if moves:
        alerts.evaluate(db, user.hospital_id, [issue.consumable_id])
    db.commit()
    levels = stock.stock_levels(db, user.hospital_id, [issue.consumable_id])
    return _recon_out(db, issue, levels)


# ---------------------------------------------------------------- inbound push (API key, no user session)

ingest_router = APIRouter(prefix="/ingest/v1", tags=["ingest (API key)"])


def _api_key(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.headers.get("X-API-Key")


@ingest_router.get("/status")
def ingest_status(request: Request, db: DB):
    """Lets a hospital system check its key: which source and entities it may send (no data returned)."""
    cred, src = credentials.authenticate(db, _api_key(request))
    credentials.rate_limit(cred)
    db.commit()
    return {"source": src.name, "entities": src.entities, "max_records_per_request": settings.INTEGRATION_MAX_RECORDS_PER_REQUEST,
            "rate_limit_per_minute": settings.INTEGRATION_RATE_LIMIT_PER_MINUTE}


@ingest_router.post("/{entity}", response_model=IngestResult)
def ingest(entity: str, body: IngestRequest, request: Request, db: DB):
    cred, src = credentials.authenticate(db, _api_key(request))
    credentials.rate_limit(cred)
    if entity not in ENTITIES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown entity '{entity}'. Known: {', '.join(ORDER)}")
    if entity not in src.entities:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"This source is not allowed to send '{entity}'")
    if not body.records:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "records is empty")
    if len(body.records) > settings.INTEGRATION_MAX_RECORDS_PER_REQUEST:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            f"At most {settings.INTEGRATION_MAX_RECORDS_PER_REQUEST} records per request")
    run = engine.run_sync(db, src, entity, body.records, trigger="api", user=None, mode="incremental", dry_run=body.dry_run)
    audit.record(db, None, "integration.ingest", "integration_credential", cred.id,
                 {"key_prefix": cred.key_prefix, "run_id": run.id, "ip": client_ip(request)}, hospital_id=src.hospital_id)
    db.commit()
    d = run_detail(db, run)
    return IngestResult(run_id=run.id, status=run.status, entity=entity, dry_run=body.dry_run,
                        received=run.records_received, created=run.records_created, updated=run.records_updated,
                        unchanged=run.records_unchanged, rejected=run.records_rejected, rejected_records=d.rejected,
                        info=(run.error_summary or {}).get("info") or {})
