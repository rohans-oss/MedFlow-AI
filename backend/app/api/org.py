from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select

from app.api.deps import DB, client_ip, get_owned, require
from app.core.permissions import MANAGE_DEPARTMENTS, MANAGE_HOSPITAL, MANAGE_USERS, READ
from app.core.security import hash_password
from app.models import Department, Hospital, HospitalMembership, Role, TenantStatus, User
from app.schemas.org import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
    HospitalOut,
    HospitalUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services import audit, tenancy

router = APIRouter(tags=["organisation"])

Reader = Annotated[User, Depends(require(READ))]


# ---------------- Hospital ----------------


@router.get("/hospitals/current", response_model=HospitalOut)
def get_hospital(user: Reader, db: DB):
    return db.get(Hospital, user.hospital_id)


@router.patch("/hospitals/current", response_model=HospitalOut)
def update_hospital(
    body: HospitalUpdate, request: Request, db: DB, user: Annotated[User, Depends(require(MANAGE_HOSPITAL))]
):
    hospital = db.get(Hospital, user.hospital_id)
    changes = audit.diff(hospital, body.model_dump(exclude_unset=True))
    if changes:
        audit.record(db, user, "hospital.update", "hospital", hospital.id, changes, client_ip(request))
    db.commit()
    return hospital


# ---------------- Departments ----------------


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(user: Reader, db: DB, include_inactive: bool = False):
    q = select(Department).where(Department.hospital_id == user.hospital_id)
    if not include_inactive:
        q = q.where(Department.is_active.is_(True))
    return db.scalars(q.order_by(Department.name)).all()


@router.post("/departments", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
def create_department(
    body: DepartmentCreate, request: Request, db: DB,
    user: Annotated[User, Depends(require(MANAGE_DEPARTMENTS))],
):
    exists = db.scalar(
        select(Department.id).where(Department.hospital_id == user.hospital_id, Department.code == body.code)
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Department code {body.code} already exists")
    dept = Department(hospital_id=user.hospital_id, **body.model_dump())
    db.add(dept)
    db.flush()
    audit.record(db, user, "department.create", "department", dept.id, body.model_dump(), client_ip(request))
    db.commit()
    return dept


@router.patch("/departments/{dept_id}", response_model=DepartmentOut)
def update_department(
    dept_id: int, body: DepartmentUpdate, request: Request, db: DB,
    user: Annotated[User, Depends(require(MANAGE_DEPARTMENTS))],
):
    dept = get_owned(db, Department, dept_id, user.hospital_id, "Department")
    changes = audit.diff(dept, body.model_dump(exclude_unset=True))
    if changes:
        audit.record(db, user, "department.update", "department", dept.id, changes, client_ip(request))
    db.commit()
    return dept


# ---------------- Users (V8: members of the active hospital) ----------------
#
# V1 contract kept: /users lists, creates and updates the people who can use *this* hospital. Since V8 that is the
# hospital's memberships: role / department / active are the membership's (per hospital); name and password belong to
# the account and can only be changed here when the account belongs to no other hospital.


def _member_out(m: HospitalMembership) -> dict:
    u = m.user
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": m.role,
            "is_active": u.is_active and m.status == TenantStatus.ACTIVE, "department_id": m.department_id,
            "department": m.department, "last_login_at": u.last_login_at, "created_at": u.created_at,
            "membership_id": m.id, "membership_status": m.status}


@router.get("/users", response_model=list[UserOut])
def list_users(db: DB, user: Annotated[User, Depends(require(MANAGE_USERS))]):
    ms = db.scalars(select(HospitalMembership).join(User, User.id == HospitalMembership.user_id)
                    .where(HospitalMembership.hospital_id == user.hospital_id).order_by(User.full_name)).all()
    return [_member_out(m) for m in ms]


def _validate_department(db: DB, hospital_id: int, role: str, department_id: int | None) -> None:
    if department_id is not None:
        get_owned(db, Department, department_id, hospital_id, "Department")
    if role == Role.DEPARTMENT_MANAGER and department_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Department managers must be assigned a department")


def add_member(db: DB, actor: User, hospital: Hospital, body: UserCreate, ip: str | None) -> HospitalMembership:
    """Create an account + membership, or add an existing account of the *same organization* as a member.
    An e-mail registered to an account outside this organization stays a 409 (as before V8) — hospital admins cannot
    pull in accounts from other organizations."""
    _validate_department(db, hospital.id, body.role, body.department_id)
    existing = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if existing is not None:
        if tenancy.membership(db, existing.id, hospital.id):
            raise HTTPException(status.HTTP_409_CONFLICT, "This user is already a member of this hospital")
        same_org = db.scalar(select(HospitalMembership.id).join(Hospital, Hospital.id == HospitalMembership.hospital_id)
                             .where(HospitalMembership.user_id == existing.id,
                                    Hospital.organization_id == hospital.organization_id))
        if not same_org and not existing.is_platform_admin:
            raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
        target = existing
    else:
        target = User(email=body.email.lower(), full_name=body.full_name, hashed_password=hash_password(body.password),
                      hospital_id=None, role=None)
        db.add(target)
        db.flush()
    m = HospitalMembership(user_id=target.id, hospital_id=hospital.id, role=body.role, department_id=body.department_id,
                           status=TenantStatus.ACTIVE, created_by_id=actor.id)
    db.add(m)
    db.flush()
    if target.hospital_id is None:  # first hospital of this account → make it the active one
        target.hospital_id, target.role, target.department_id = m.hospital_id, m.role, m.department_id
    audit.record(db, actor, "user.create" if existing is None else "membership.create", "user", target.id,
                 {"email": target.email, "role": m.role, "department_id": m.department_id,
                  "existing_account": existing is not None}, ip, hospital_id=hospital.id)
    return m


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, request: Request, db: DB, user: Annotated[User, Depends(require(MANAGE_USERS))]):
    m = add_member(db, user, db.get(Hospital, user.hospital_id), body, client_ip(request))
    db.commit()
    db.refresh(m)
    return _member_out(m)


def update_member(db: DB, actor: User, hospital: Hospital, target_user_id: int, body: UserUpdate,
                  ip: str | None) -> HospitalMembership:
    m = tenancy.membership(db, target_user_id, hospital.id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    target = m.user
    data = body.model_dump(exclude_unset=True)
    if target.id == actor.id and (data.get("is_active") is False or data.get("role", Role.ADMIN) != Role.ADMIN):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate or demote yourself")
    _validate_department(db, hospital.id, data.get("role", m.role), data.get("department_id", m.department_id))
    password = data.pop("password", None)
    full_name = data.pop("full_name", None)
    if password or (full_name and full_name != target.full_name):
        others = db.scalar(select(HospitalMembership.id).where(HospitalMembership.user_id == target.id,
                                                               HospitalMembership.hospital_id != hospital.id))
        if others and not actor.is_platform_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This account also belongs to other hospitals — its name and "
                                "password can only be changed by the account holder or an administrator of all of them")
    changes: dict = {}
    if "role" in data and data["role"] != m.role:
        changes["role"] = {"from": m.role, "to": data["role"]}
        m.role = data["role"]
    if "department_id" in data and data["department_id"] != m.department_id:
        changes["department_id"] = {"from": m.department_id, "to": data["department_id"]}
        m.department_id = data["department_id"]
    if "is_active" in data:
        new_status = TenantStatus.ACTIVE if data["is_active"] else TenantStatus.SUSPENDED
        if new_status != m.status:
            changes["is_active"] = {"from": m.status == TenantStatus.ACTIVE, "to": data["is_active"]}
            m.status = new_status
    if full_name and full_name != target.full_name:
        changes["full_name"] = {"from": target.full_name, "to": full_name}
        target.full_name = full_name
    if password:
        target.hashed_password = hash_password(password)
        changes["password"] = {"from": "***", "to": "***"}
    tenancy.sync_pointer_after_membership_change(target, m)
    if password or "is_active" in changes or "role" in changes:
        target.token_version += 1  # force re-login
    if changes:
        audit.record(db, actor, "user.update", "user", target.id, changes, ip, hospital_id=hospital.id)
    return m


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, body: UserUpdate, request: Request, db: DB,
    user: Annotated[User, Depends(require(MANAGE_USERS))],
):
    m = update_member(db, user, db.get(Hospital, user.hospital_id), user_id, body, client_ip(request))
    db.commit()
    db.refresh(m)
    return _member_out(m)
