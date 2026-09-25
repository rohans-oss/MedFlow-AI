"""V8 — tenant context: memberships, the active hospital, and who may administer what.

Access model
    * A user reaches hospital data only through an ACTIVE `HospitalMembership` in an ACTIVE hospital of an ACTIVE
      organization. The membership carries the role (and department) held in that hospital.
    * `users.hospital_id / role / department_id` is the *active* hospital context — a cache of one such membership.
      It is changed only here (`switch`, `ensure_context`, membership updates) and re-verified on every request
      (`verify_request_context`); a database trigger rejects a pointer without an active membership.
    * Organization admins (`OrganizationMembership`) administer their organization's hospitals (members, profile,
      status, onboarding) and see organization reporting. They do not see a hospital's operational data unless they
      also hold a membership there — like everyone else.
    * Platform admins (`users.is_platform_admin`) manage organizations. Same rule: no membership, no hospital data.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Hospital,
    HospitalMembership,
    Organization,
    OrganizationMembership,
    Role,
    TenantStatus,
    User,
)


def _usable(m: HospitalMembership) -> bool:
    return (m.status == TenantStatus.ACTIVE and m.hospital.status == TenantStatus.ACTIVE
            and m.hospital.organization.status == TenantStatus.ACTIVE)


def membership(db: Session, user_id: int, hospital_id: int) -> HospitalMembership | None:
    return db.scalar(select(HospitalMembership).where(HospitalMembership.user_id == user_id,
                                                      HospitalMembership.hospital_id == hospital_id))


def usable_membership(db: Session, user_id: int, hospital_id: int) -> HospitalMembership | None:
    m = membership(db, user_id, hospital_id)
    return m if m is not None and _usable(m) else None


def memberships(db: Session, user: User) -> list[HospitalMembership]:
    return list(db.scalars(select(HospitalMembership).where(HospitalMembership.user_id == user.id)
                           .order_by(HospitalMembership.id)))


def _apply(user: User, m: HospitalMembership | None) -> bool:
    """Point the user's active context at membership m (or clear it). Returns True if anything changed."""
    new = (m.hospital_id, m.role, m.department_id) if m else (None, None, None)
    if (user.hospital_id, user.role, user.department_id) == new:
        return False
    user.hospital_id, user.role, user.department_id = new
    return True


def verify_request_context(db: Session, user: User) -> None:
    """Called for every authenticated request: the active hospital must still be reachable through a usable
    membership, and role / department come from that membership. Otherwise the context is cleared (the request then
    has no hospital and hospital endpoints answer 403)."""
    if user.hospital_id is None:
        return
    m = usable_membership(db, user.id, user.hospital_id)
    if _apply(user, m):
        db.commit()


def ensure_context(db: Session, user: User) -> None:
    """At login / refresh: keep a valid active hospital, else fall back to the first usable membership."""
    if user.hospital_id is not None and usable_membership(db, user.id, user.hospital_id):
        verify_request_context(db, user)
        return
    first = next((m for m in memberships(db, user) if _usable(m)), None)
    _apply(user, first)


def switch(db: Session, user: User, hospital_id: int) -> HospitalMembership:
    """Make hospital_id the active hospital. 404 unless the user has a usable membership there (no hint whether the
    hospital exists)."""
    m = usable_membership(db, user.id, hospital_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hospital not found")
    _apply(user, m)
    return m


def sync_pointer_after_membership_change(user: User, m: HospitalMembership) -> None:
    """A membership changed (role, department, status): keep the user's active context consistent with it."""
    if user.hospital_id != m.hospital_id:
        return
    if m.status != TenantStatus.ACTIVE:
        _apply(user, None)
    else:
        _apply(user, m)


# ---------------------------------------------------------------- organization / platform administration


def admin_org_ids(db: Session, user: User) -> set[int]:
    return set(db.scalars(select(OrganizationMembership.organization_id).where(
        OrganizationMembership.user_id == user.id, OrganizationMembership.status == TenantStatus.ACTIVE)))


def is_org_admin(db: Session, user: User, organization_id: int) -> bool:
    return organization_id in admin_org_ids(db, user)


def require_org_admin(db: Session, user: User, organization_id: int) -> Organization:
    """Organization admin of that organization (platform admins too). 404 otherwise — no existence hint."""
    org = db.get(Organization, organization_id)
    if org is None or not (user.is_platform_admin or is_org_admin(db, user, organization_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org


def require_platform_admin(user: User) -> None:
    if not user.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform administrators only")


def admin_hospital(db: Session, user: User, hospital_id: int) -> tuple[Hospital, str]:
    """May `user` administer hospital_id (members, profile)? → (hospital, basis). Basis:
    'hospital_admin' — an ADMIN membership there AND it is the user's active hospital (hospital admins act inside the
    hospital they are working in); 'organization_admin' — admin of the hospital's organization; 'platform_admin'.
    404 otherwise (no existence hint)."""
    h = db.get(Hospital, hospital_id)
    if h is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hospital not found")
    if user.is_platform_admin:
        return h, "platform_admin"
    if is_org_admin(db, user, h.organization_id):
        return h, "organization_admin"
    if user.hospital_id == h.id and user.role == Role.ADMIN:
        m = usable_membership(db, user.id, h.id)
        if m is not None and m.role == Role.ADMIN:
            return h, "hospital_admin"
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Hospital not found")
