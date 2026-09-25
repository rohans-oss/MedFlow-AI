"""V8 — organizations, hospitals, memberships, onboarding, organization reporting, CSV import (mirrored in
frontend/lib/types.ts)."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import Role, TenantStatus
from app.schemas.common import ORMModel
from app.schemas.org import DepartmentCreate, HospitalUpdate, UserCreate


def _code(v: str) -> str:
    v = v.strip().upper()
    if not v or not all(c.isalnum() or c in "-_" for c in v):
        raise ValueError("Use letters, digits, '-' or '_'")
    return v


class OrganizationOut(ORMModel):
    id: int
    name: str
    code: str
    status: TenantStatus
    is_demo: bool
    created_at: datetime
    hospital_count: int = 0
    member_count: int = 0
    admin_count: int = 0


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=2, max_length=32)

    _c = field_validator("code")(_code)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    status: TenantStatus | None = None


class OrgAdminIn(BaseModel):
    email: EmailStr


class OrgAdminOut(BaseModel):
    user_id: int
    email: str
    full_name: str
    status: str


class HospitalAdminOut(BaseModel):
    id: int
    name: str
    code: str
    city: str | None
    state: str | None
    bed_count: int | None
    status: TenantStatus
    is_demo: bool
    organization_id: int
    organization_name: str
    member_count: int
    my_role: Role | None  # the caller's membership role there (None = not a member)
    my_membership_status: str | None
    can_switch: bool
    can_admin: bool


class HospitalAdminUpdate(HospitalUpdate):
    status: TenantStatus | None = None


class DepartmentBrief(BaseModel):
    id: int
    code: str
    name: str
    is_active: bool


class HospitalDetail(HospitalAdminOut):
    expiry_warning_days: int
    departments: list[DepartmentBrief]
    admin_basis: str


class MemberOut(BaseModel):
    membership_id: int
    user_id: int
    email: str
    full_name: str
    role: Role
    department_id: int | None
    department: str | None
    status: TenantStatus
    account_active: bool
    other_hospitals: int  # how many other hospitals this account belongs to (names are not disclosed)
    last_login_at: datetime | None
    created_at: datetime


class OnboardAdmin(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        from app.schemas.org import _check_password

        return _check_password(v)


class OnboardIn(BaseModel):
    """Create organization hospital → administrator → departments → procurement settings. Inventory is loaded
    afterwards by the hospital (CSV import) — MedFlow does not connect to hospital systems."""

    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=2, max_length=32)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    bed_count: int | None = Field(None, ge=0, le=10000)
    expiry_warning_days: int = Field(60, ge=1, le=365)
    admin: OnboardAdmin
    departments: list[DepartmentCreate] = Field(default_factory=list, max_length=50)
    procurement: dict[str, Any] | None = None  # SettingsValues fields; defaults when omitted

    _c = field_validator("code")(_code)


class OnboardOut(BaseModel):
    hospital: HospitalAdminOut
    admin_user_id: int
    admin_created: bool
    departments: int
    procurement_settings: Literal["defaults", "custom"]
    next_steps: list[str]


class MemberCreate(UserCreate):
    pass


class OrgHospitalRow(BaseModel):
    hospital_id: int
    hospital: str
    code: str
    status: str
    city: str | None
    active_members: int
    items: int
    risk_high: int
    risk_medium: int
    risk_low: int
    risk_as_of: date | None
    active_alerts: int
    critical_alerts: int
    pending_recommendations: int
    pending_with_order: int
    pending_purchase_value: float
    supplier_otif_rate: float | None
    supplier_orders_decided: int


class SupplierHospitalPerf(BaseModel):
    hospital_id: int
    hospital: str
    orders: int
    otif_rate: float | None
    reliability_score: float | None
    grade: str | None


class SupplierComparison(BaseModel):
    code: str
    names: list[str]
    by_hospital: list[SupplierHospitalPerf]


class OrgOverview(BaseModel):
    organization: OrganizationOut
    hospitals: list[OrgHospitalRow]
    totals: dict[str, float | int]
    contributing_hospitals: list[str]
    supplier_comparison: list[SupplierComparison]
    risk_as_of_range: list[date] | None
    notes: list[str]


class SwitchOut(BaseModel):
    hospital_id: int
    hospital_name: str
    role: Role


class ImportIn(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)
    dry_run: bool = True


class ImportRowError(BaseModel):
    line: int
    error: str
    row: dict[str, str]


class ImportOut(BaseModel):
    kind: str
    rows: int
    valid: int
    created: int
    dry_run: bool
    errors: list[ImportRowError]
    created_categories: list[str]
