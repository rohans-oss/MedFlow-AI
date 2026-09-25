"""V2B — procedure schemas. Procedure data is counts only: never patient identifiers."""

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import ProcedureStatus
from app.schemas.common import ORMModel
from app.schemas.inventory import ConsumableRef, DepartmentRef


class ProcedureTypeIn(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=200)
    department_id: int
    description: str | None = None
    avg_duration_minutes: int | None = Field(None, ge=1, le=1440)
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class ProcedureTypeUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    department_id: int | None = None
    description: str | None = None
    avg_duration_minutes: int | None = Field(None, ge=1, le=1440)
    is_active: bool | None = None


class ProcedureTypeRef(ORMModel):
    id: int
    code: str
    name: str


class ProcedureTypeOut(ORMModel):
    id: int
    code: str
    name: str
    description: str | None
    avg_duration_minutes: int | None
    is_active: bool
    is_synthetic: bool
    department: DepartmentRef
    mapping_count: int = 0
    upcoming_count: int = 0  # non-cancelled procedures scheduled in the next 30 days


class ScheduleIn(BaseModel):
    procedure_type_id: int
    department_id: int | None = None  # defaults to the procedure type's department
    scheduled_date: date
    count: int = Field(gt=0, le=500)
    status: ProcedureStatus = ProcedureStatus.SCHEDULED
    notes: str | None = Field(None, max_length=500)

    @field_validator("status")
    @classmethod
    def not_cancelled(cls, v: ProcedureStatus) -> ProcedureStatus:
        if v == ProcedureStatus.CANCELLED:
            raise ValueError("Create a SCHEDULED or COMPLETED entry; use the cancel action to cancel")
        return v


class ScheduleUpdate(BaseModel):
    scheduled_date: date | None = None
    count: int | None = Field(None, gt=0, le=500)
    status: ProcedureStatus | None = None
    notes: str | None = Field(None, max_length=500)

    @field_validator("status")
    @classmethod
    def not_cancelled(cls, v: ProcedureStatus | None) -> ProcedureStatus | None:
        if v == ProcedureStatus.CANCELLED:
            raise ValueError("Use the cancel action to cancel")
        return v


class CancelIn(BaseModel):
    reason: str | None = Field(None, max_length=500)


class ScheduleOut(ORMModel):
    id: int
    scheduled_date: date
    count: int
    status: ProcedureStatus
    notes: str | None
    is_synthetic: bool
    created_at: datetime
    procedure_type: ProcedureTypeRef
    department: DepartmentRef


class MappingIn(BaseModel):
    procedure_type_id: int
    consumable_id: int
    quantity_per_procedure: float = Field(gt=0, le=10_000)
    notes: str | None = Field(None, max_length=500)
    is_active: bool = True


class MappingUpdate(BaseModel):
    quantity_per_procedure: float | None = Field(None, gt=0, le=10_000)
    notes: str | None = Field(None, max_length=500)
    is_active: bool | None = None


class MappingOut(ORMModel):
    id: int
    quantity_per_procedure: float
    notes: str | None
    is_active: bool
    is_synthetic: bool
    procedure_type: ProcedureTypeRef
    consumable: ConsumableRef


class UpcomingByType(BaseModel):
    procedure_type_id: int
    code: str
    name: str
    department: str
    count: int


class ProcedureSummary(BaseModel):
    days: int
    total_scheduled: int
    by_type: list[UpcomingByType]
    schedule_through: date | None  # last date with a non-cancelled schedule entry
    synthetic_rows: int
