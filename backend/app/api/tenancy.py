"""V8 — multi-hospital SaaS API: hospital switching, hospital / member administration, organizations, onboarding,
organization reporting, organization / platform audit, and hospital-scoped CSV import.

GET   /hospitals                                   hospitals I belong to or administer
POST  /hospitals/{id}/switch                        make {id} my active hospital (needs an active membership there)
GET   /hospitals/{id}          PATCH                hospital detail / profile + status        (hospital admin*, org admin)
GET   /hospitals/{id}/members  POST  PATCH …/{uid}  members of that hospital                  (hospital admin*, org admin)
GET   /organizations           POST  PATCH /{id}    organizations                             (org admin: own; platform)
GET   /organizations/{id}/hospitals  POST           list / onboard a hospital                 (org admin, platform)
GET   /organizations/{id}/overview                  organization reporting                    (org admin)
GET   /organizations/{id}/admins  POST  DELETE      organization admins                       (org admin, platform)
GET   /organizations/{id}/audit-logs                organization audit                        (org admin)
GET   /platform/audit-logs                          platform-level audit                      (platform admin)
POST  /imports/{kind}                               CSV into the ACTIVE hospital (departments | suppliers | items | opening_stock)

* hospital admins act on their active hospital only. Foreign ids answer 404 (no existence hints).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, CurrentUser, client_ip, require
from app.api.org import add_member, update_member
from app.core.permissions import MANAGE_CATALOG, MANAGE_DEPARTMENTS, MANAGE_SUPPLIERS, READ, STOCK_RECEIVE
from app.core.security import hash_password
from app.models import (
    AuditLog,
    Department,
    Hospital,
    HospitalMembership,
    Organization,
    OrganizationMembership,
    OrgRole,
    Role,
    TenantStatus,
    User,
)
from app.schemas.common import Page
from app.schemas.inventory import AuditOut
from app.schemas.org import MeOut, UserCreate, UserUpdate
from app.schemas.procurement import SettingsValues
from app.schemas.tenancy import (
    HospitalAdminOut,
    HospitalAdminUpdate,
    HospitalDetail,
    ImportIn,
    ImportOut,
    MemberOut,
    OnboardIn,
    OnboardOut,
    OrgAdminIn,
    OrgAdminOut,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
    OrgOverview,
)
from app.services import audit, imports, org_reporting, tenancy

router = APIRouter(tags=["multi-hospital (V8)"])


# ---------------------------------------------------------------- helpers


def _counts(db: DB, hospital_ids: list[int]) -> dict[int, int]:
    rows = db.execute(select(HospitalMembership.hospital_id, func.count()).where(
        HospitalMembership.hospital_id.in_(hospital_ids), HospitalMembership.status == TenantStatus.ACTIVE)
        .group_by(HospitalMembership.hospital_id)).all()
    return {h: 0 for h in hospital_ids} | dict(rows)


def _hospital_rows(db: DB, user: User, hospitals: list[Hospital]) -> list[dict]:
    mine = {m.hospital_id: m for m in tenancy.memberships(db, user)}
    admin_orgs = tenancy.admin_org_ids(db, user)
    counts = _counts(db, [h.id for h in hospitals])
    out = []
    for h in hospitals:
        m = mine.get(h.id)
        can_admin = (user.is_platform_admin or h.organization_id in admin_orgs
                     or (m is not None and m.role == Role.ADMIN and user.hospital_id == h.id))
        out.append({"id": h.id, "name": h.name, "code": h.code, "city": h.city, "state": h.state, "bed_count": h.bed_count,
                    "status": h.status, "is_demo": h.is_demo, "organization_id": h.organization_id,
                    "organization_name": h.organization.name, "member_count": counts[h.id],
                    "my_role": m.role if m else None, "my_membership_status": m.status if m else None,
                    "can_switch": m is not None and tenancy._usable(m), "can_admin": can_admin})
    return out


def _org_out(db: DB, org: Organization) -> dict:
    hids = list(db.scalars(select(Hospital.id).where(Hospital.organization_id == org.id)))
    members = db.scalar(select(func.count(func.distinct(HospitalMembership.user_id))).where(
        HospitalMembership.hospital_id.in_(hids), HospitalMembership.status == TenantStatus.ACTIVE)) if hids else 0
    admins = db.scalar(select(func.count()).where(OrganizationMembership.organization_id == org.id,
                                                  OrganizationMembership.status == TenantStatus.ACTIVE))
    return {"id": org.id, "name": org.name, "code": org.code, "status": org.status, "is_demo": org.is_demo,
            "created_at": org.created_at, "hospital_count": len(hids), "member_count": members or 0, "admin_count": admins}


def _member_out(db: DB, m: HospitalMembership) -> dict:
    others = db.scalar(select(func.count()).where(HospitalMembership.user_id == m.user_id,
                                                  HospitalMembership.hospital_id != m.hospital_id))
    return {"membership_id": m.id, "user_id": m.user_id, "email": m.user.email, "full_name": m.user.full_name,
            "role": m.role, "department_id": m.department_id, "department": m.department.name if m.department else None,
            "status": m.status, "account_active": m.user.is_active, "other_hospitals": others,
            "last_login_at": m.user.last_login_at, "created_at": m.created_at}


# ---------------------------------------------------------------- hospitals


@router.get("/hospitals", response_model=list[HospitalAdminOut])
def list_hospitals(user: CurrentUser, db: DB):
    """Hospitals I am a member of, plus hospitals of organizations I administer (platform admins: all)."""
    if user.is_platform_admin:
        q = select(Hospital)
    else:
        mine = select(HospitalMembership.hospital_id).where(HospitalMembership.user_id == user.id)
        q = select(Hospital).where(or_(Hospital.id.in_(mine), Hospital.organization_id.in_(tenancy.admin_org_ids(db, user))))
    return _hospital_rows(db, user, list(db.scalars(q.order_by(Hospital.name))))


@router.post("/hospitals/{hospital_id}/switch", response_model=MeOut)
def switch_hospital(hospital_id: int, request: Request, response: Response, user: CurrentUser, db: DB):
    from app.api.auth import _set_auth_cookies, build_me

    before = user.hospital_id
    tenancy.switch(db, user, hospital_id)
    audit.record(db, user, "hospital.switch", "hospital", hospital_id, {"from_hospital_id": before}, client_ip(request))
    db.commit()
    _set_auth_cookies(response, user)  # new access token bound to the new hospital
    return build_me(db, user)


@router.get("/hospitals/{hospital_id}", response_model=HospitalDetail)
def hospital_detail(hospital_id: int, user: CurrentUser, db: DB):
    h, basis = tenancy.admin_hospital(db, user, hospital_id)
    row = _hospital_rows(db, user, [h])[0]
    depts = db.scalars(select(Department).where(Department.hospital_id == h.id).order_by(Department.name)).all()
    return {**row, "expiry_warning_days": h.expiry_warning_days, "admin_basis": basis,
            "departments": [{"id": d.id, "code": d.code, "name": d.name, "is_active": d.is_active} for d in depts]}


@router.patch("/hospitals/{hospital_id}", response_model=HospitalAdminOut)
def update_hospital(hospital_id: int, body: HospitalAdminUpdate, request: Request, user: CurrentUser, db: DB):
    h, basis = tenancy.admin_hospital(db, user, hospital_id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and basis == "hospital_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only organization or platform administrators can suspend a hospital")
    changes = audit.diff(h, data)
    if changes:
        audit.record(db, user, "hospital.update", "hospital", h.id, changes, client_ip(request), hospital_id=h.id)
    db.commit()
    return _hospital_rows(db, user, [h])[0]


@router.get("/hospitals/{hospital_id}/members", response_model=list[MemberOut])
def list_members(hospital_id: int, user: CurrentUser, db: DB):
    h, _ = tenancy.admin_hospital(db, user, hospital_id)
    ms = db.scalars(select(HospitalMembership).join(User, User.id == HospitalMembership.user_id)
                    .where(HospitalMembership.hospital_id == h.id).order_by(User.full_name)).all()
    return [_member_out(db, m) for m in ms]


@router.post("/hospitals/{hospital_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def create_member(hospital_id: int, body: UserCreate, request: Request, user: CurrentUser, db: DB):
    h, _ = tenancy.admin_hospital(db, user, hospital_id)
    m = add_member(db, user, h, body, client_ip(request))
    db.commit()
    db.refresh(m)
    return _member_out(db, m)


@router.patch("/hospitals/{hospital_id}/members/{user_id}", response_model=MemberOut)
def patch_member(hospital_id: int, user_id: int, body: UserUpdate, request: Request, user: CurrentUser, db: DB):
    h, _ = tenancy.admin_hospital(db, user, hospital_id)
    m = update_member(db, user, h, user_id, body, client_ip(request))
    db.commit()
    db.refresh(m)
    return _member_out(db, m)


# ---------------------------------------------------------------- organizations


@router.get("/organizations", response_model=list[OrganizationOut])
def list_organizations(user: CurrentUser, db: DB):
    q = select(Organization)
    if not user.is_platform_admin:
        q = q.where(Organization.id.in_(tenancy.admin_org_ids(db, user)))
    return [_org_out(db, o) for o in db.scalars(q.order_by(Organization.name))]


@router.post("/organizations", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED)
def create_organization(body: OrganizationCreate, request: Request, user: CurrentUser, db: DB):
    tenancy.require_platform_admin(user)
    if db.scalar(select(Organization.id).where(Organization.code == body.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Organization code {body.code} already exists")
    org = Organization(name=body.name, code=body.code, status=TenantStatus.ACTIVE)
    db.add(org)
    db.flush()
    audit.record(db, user, "organization.create", "organization", org.id, body.model_dump(), client_ip(request),
                 organization_id=org.id, scope="organization")
    db.commit()
    return _org_out(db, org)


@router.get("/organizations/{org_id}", response_model=OrganizationOut)
def get_organization(org_id: int, user: CurrentUser, db: DB):
    return _org_out(db, tenancy.require_org_admin(db, user, org_id))


@router.patch("/organizations/{org_id}", response_model=OrganizationOut)
def update_organization(org_id: int, body: OrganizationUpdate, request: Request, user: CurrentUser, db: DB):
    org = tenancy.require_org_admin(db, user, org_id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and not user.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only platform administrators can suspend an organization")
    changes = audit.diff(org, data)
    if changes:
        audit.record(db, user, "organization.update", "organization", org.id, changes, client_ip(request),
                     organization_id=org.id, scope="organization")
    db.commit()
    return _org_out(db, org)


@router.get("/organizations/{org_id}/hospitals", response_model=list[HospitalAdminOut])
def org_hospitals(org_id: int, user: CurrentUser, db: DB):
    org = tenancy.require_org_admin(db, user, org_id)
    return _hospital_rows(db, user, list(db.scalars(select(Hospital).where(Hospital.organization_id == org.id)
                                                    .order_by(Hospital.name))))


@router.post("/organizations/{org_id}/hospitals", response_model=OnboardOut, status_code=status.HTTP_201_CREATED)
def onboard_hospital(org_id: int, body: OnboardIn, request: Request, user: CurrentUser, db: DB):
    """Onboarding: hospital → administrator → departments → procurement settings (one transaction)."""
    from app.services import procurement as procurement_service

    org = tenancy.require_org_admin(db, user, org_id)
    if org.status != TenantStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "The organization is suspended")
    if db.scalar(select(Hospital.id).where(Hospital.code == body.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Hospital code {body.code} already exists")
    settings_values = None
    if body.procurement:
        from app.services.procurement import DEFAULTS

        try:
            settings_values = SettingsValues(**(DEFAULTS | body.procurement)).model_dump()
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Procurement settings: {e}") from e
    h = Hospital(organization_id=org.id, name=body.name, code=body.code, city=body.city, state=body.state,
                 bed_count=body.bed_count, expiry_warning_days=body.expiry_warning_days, status=TenantStatus.ACTIVE)
    db.add(h)
    db.flush()
    codes = set()
    for d in body.departments:
        if d.code in codes:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Duplicate department code {d.code}")
        codes.add(d.code)
        db.add(Department(hospital_id=h.id, **d.model_dump()))
    existing = db.scalar(select(User).where(func.lower(User.email) == body.admin.email.lower()))
    if existing is not None:
        same_org = db.scalar(select(HospitalMembership.id).join(Hospital, Hospital.id == HospitalMembership.hospital_id)
                             .where(HospitalMembership.user_id == existing.id, Hospital.organization_id == org.id))
        is_admin_here = db.scalar(select(OrganizationMembership.id).where(OrganizationMembership.user_id == existing.id,
                                                                          OrganizationMembership.organization_id == org.id))
        if not (same_org or is_admin_here):
            raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
        admin = existing
    else:
        admin = User(email=body.admin.email.lower(), full_name=body.admin.full_name,
                     hashed_password=hash_password(body.admin.password), hospital_id=None, role=None)
        db.add(admin)
        db.flush()
    db.add(HospitalMembership(user_id=admin.id, hospital_id=h.id, role=Role.ADMIN, status=TenantStatus.ACTIVE,
                              created_by_id=user.id))
    db.flush()
    if admin.hospital_id is None:
        admin.hospital_id, admin.role, admin.department_id = h.id, Role.ADMIN, None
    if settings_values:
        procurement_service.save_settings(db, h.id, user, settings_values)
    audit.record(db, user, "hospital.onboard", "hospital", h.id, {
        "name": h.name, "code": h.code, "admin_email": admin.email, "admin_created": existing is None,
        "departments": sorted(codes), "procurement_settings": "custom" if settings_values else "defaults"},
        client_ip(request), hospital_id=h.id)
    db.commit()
    return {"hospital": _hospital_rows(db, user, [h])[0], "admin_user_id": admin.id, "admin_created": existing is None,
            "departments": len(codes), "procurement_settings": "custom" if settings_values else "defaults",
            "next_steps": ["The hospital administrator signs in (the hospital is selected automatically if it is their first).",
                           "Import suppliers, items and opening stock (Settings → Import CSV), or enter them on the pages.",
                           "Record consumption for a few weeks, then train forecasts / risk (Forecasts → Train)."]}


@router.get("/organizations/{org_id}/overview", response_model=OrgOverview)
def organization_overview(org_id: int, user: CurrentUser, db: DB):
    org = tenancy.require_org_admin(db, user, org_id)
    if not tenancy.is_org_admin(db, user, org.id):  # platform admins manage organizations but don't read their data
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    out = org_reporting.overview(db, org)
    out["organization"] = _org_out(db, org)
    return out


@router.get("/organizations/{org_id}/admins", response_model=list[OrgAdminOut])
def org_admins(org_id: int, user: CurrentUser, db: DB):
    org = tenancy.require_org_admin(db, user, org_id)
    rows = db.scalars(select(OrganizationMembership).where(OrganizationMembership.organization_id == org.id)).all()
    return [{"user_id": m.user_id, "email": m.user.email, "full_name": m.user.full_name, "status": m.status} for m in rows]


@router.post("/organizations/{org_id}/admins", response_model=OrgAdminOut, status_code=status.HTTP_201_CREATED)
def add_org_admin(org_id: int, body: OrgAdminIn, request: Request, user: CurrentUser, db: DB):
    """Make an existing account an organization admin. Org admins may only promote accounts that already belong to
    one of the organization's hospitals; platform admins may promote any account."""
    org = tenancy.require_org_admin(db, user, org_id)
    target = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if target is not None and not user.is_platform_admin:
        in_org = db.scalar(select(HospitalMembership.id).join(Hospital, Hospital.id == HospitalMembership.hospital_id)
                           .where(HospitalMembership.user_id == target.id, Hospital.organization_id == org.id))
        target = target if in_org else None
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user in this organization")
    m = db.scalar(select(OrganizationMembership).where(OrganizationMembership.user_id == target.id,
                                                       OrganizationMembership.organization_id == org.id))
    if m is None:
        m = OrganizationMembership(user_id=target.id, organization_id=org.id, role=OrgRole.ORG_ADMIN)
        db.add(m)
    m.status = TenantStatus.ACTIVE
    db.flush()
    audit.record(db, user, "organization.admin_add", "user", target.id, {"email": target.email}, client_ip(request),
                 organization_id=org.id, scope="organization")
    db.commit()
    return {"user_id": target.id, "email": target.email, "full_name": target.full_name, "status": m.status}


@router.delete("/organizations/{org_id}/admins/{user_id}", response_model=OrgAdminOut)
def remove_org_admin(org_id: int, user_id: int, request: Request, user: CurrentUser, db: DB):
    org = tenancy.require_org_admin(db, user, org_id)
    if user_id == user.id and not user.is_platform_admin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot remove yourself")
    m = db.scalar(select(OrganizationMembership).where(OrganizationMembership.user_id == user_id,
                                                       OrganizationMembership.organization_id == org.id))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not an administrator of this organization")
    m.status = TenantStatus.SUSPENDED
    audit.record(db, user, "organization.admin_remove", "user", user_id, None, client_ip(request),
                 organization_id=org.id, scope="organization")
    db.commit()
    return {"user_id": m.user_id, "email": m.user.email, "full_name": m.user.full_name, "status": m.status}


def _audit_page(db: DB, q, page: int, page_size: int, action: str | None) -> Page:
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                       .offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/organizations/{org_id}/audit-logs", response_model=Page[AuditOut])
def organization_audit(org_id: int, user: CurrentUser, db: DB, action: str | None = None,
                       page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    org = tenancy.require_org_admin(db, user, org_id)
    if not tenancy.is_org_admin(db, user, org.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return _audit_page(db, select(AuditLog).where(AuditLog.organization_id == org.id), page, page_size, action)


@router.get("/platform/audit-logs", response_model=Page[AuditOut])
def platform_audit(user: CurrentUser, db: DB, action: str | None = None,
                   page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    """Platform and organization-level events (no hospital's operational audit)."""
    tenancy.require_platform_admin(user)
    return _audit_page(db, select(AuditLog).where(AuditLog.hospital_id.is_(None)), page, page_size, action)


# ---------------------------------------------------------------- CSV import (active hospital only)

IMPORT_PERMS = {"departments": MANAGE_DEPARTMENTS, "suppliers": MANAGE_SUPPLIERS, "items": MANAGE_CATALOG,
                "opening_stock": STOCK_RECEIVE}


@router.post("/imports/{kind}", response_model=ImportOut)
def import_csv(kind: str, body: ImportIn, request: Request, db: DB, user: Annotated[User, Depends(require(READ))]):
    from app.core.permissions import permissions_for

    perm = IMPORT_PERMS.get(kind)
    if perm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown import type '{kind}'")
    if perm not in permissions_for(user.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to do this")
    try:
        result = imports.run(db, user, kind, body.csv, body.dry_run)
    except imports.ImportError_ as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    if result["created"]:
        audit.record(db, user, "import.csv", kind, None, {"rows": result["rows"], "created": result["created"],
                                                          "created_categories": result["created_categories"]},
                     client_ip(request))
        if kind == "opening_stock":
            from app.services import alerts

            alerts.evaluate(db, user.hospital_id)
        db.commit()
    else:
        db.rollback()
    return result
