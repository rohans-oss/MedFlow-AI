from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import Role
from app.schemas.common import ORMModel

# ---------- Auth ----------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


def _check_password(v: str) -> str:
    if len(v.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes")
    if not any(c.isdigit() for c in v) or not any(c.isalpha() for c in v):
        raise ValueError("Password must contain letters and digits")
    return v


# ---------- Hospital ----------


class HospitalOut(ORMModel):
    id: int
    name: str
    code: str
    city: str | None
    state: str | None
    bed_count: int | None
    expiry_warning_days: int
    is_demo: bool
    organization_id: int | None = None  # V8
    status: str = "ACTIVE"  # V8


class HospitalUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    bed_count: int | None = Field(None, ge=0, le=10000)
    expiry_warning_days: int | None = Field(None, ge=1, le=365)


# ---------- Departments ----------


class DepartmentBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = None
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def upper_code(cls, v: str) -> str:
        return v.upper()


class DepartmentCreate(DepartmentBase):
    pass


class DepartmentUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    description: str | None = None
    is_active: bool | None = None


class DepartmentOut(ORMModel):
    id: int
    name: str
    code: str
    description: str | None
    is_active: bool


# ---------- Users ----------


class UserOut(ORMModel):
    id: int
    email: str
    full_name: str
    role: Role
    is_active: bool
    department_id: int | None
    department: DepartmentOut | None = None
    last_login_at: datetime | None
    created_at: datetime
    membership_id: int | None = None  # V8: the membership in the current hospital (users list)
    membership_status: str | None = None  # V8


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=8, max_length=72)
    role: Role = Role.VIEWER
    department_id: int | None = None

    @field_validator("password")
    @classmethod
    def pw(cls, v: str) -> str:
        return _check_password(v)


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=200)
    role: Role | None = None
    department_id: int | None = None
    is_active: bool | None = None
    password: str | None = Field(None, min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def pw(cls, v: str | None) -> str | None:
        return _check_password(v) if v else v


class MembershipBrief(BaseModel):
    """V8: one hospital the user can switch to (or a suspended membership, shown as unavailable)."""

    membership_id: int
    hospital_id: int
    hospital_name: str
    hospital_code: str
    organization_id: int
    organization_name: str
    role: Role
    department_id: int | None
    status: str
    available: bool


class OrgBrief(ORMModel):
    id: int
    name: str
    code: str
    status: str


class MeOut(UserOut):
    role: Role | None  # V8: None when no hospital is selected
    hospital: HospitalOut | None  # V8: the ACTIVE hospital (None = none selected)
    permissions: list[str]
    organization: OrgBrief | None = None  # V8: organization of the active hospital
    memberships: list[MembershipBrief] = []  # V8: hospitals this account belongs to
    admin_organizations: list[OrgBrief] = []  # V8: organizations this account administers
    is_platform_admin: bool = False  # V8


class LoginResponse(BaseModel):
    user: MeOut
    access_token: str
    token_type: str = "bearer"
