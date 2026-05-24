from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _BaseLifecycle(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: Optional[str] = Field(None, max_length=1000)
    effective_date: Optional[date] = None


class LifecycleConfirmBody(_BaseLifecycle):
    """Confirm a probationary employee (sets confirmation_date and lifecycle to ACTIVE)."""
    pass


class LifecyclePutOnProbationBody(_BaseLifecycle):
    """Move an ACTIVE employee onto probation (e.g. for a performance review window)."""
    probation_months: Optional[int] = Field(default=6, ge=1, le=24)
    confirmation_date: Optional[date] = None  # if not set, joining_date + probation_months is used


class LifecyclePromoteBody(_BaseLifecycle):
    new_designation_id: UUID
    new_grade_id: Optional[UUID] = None
    new_pay_level: Optional[str] = None
    new_monthly_ctc: Optional[float] = None


class LifecycleTransferBody(_BaseLifecycle):
    new_department_id: Optional[UUID] = None
    new_work_location_id: Optional[UUID] = None
    new_reporting_manager_id: Optional[UUID] = None


class LifecycleSuspendBody(_BaseLifecycle):
    reason: str = Field(..., min_length=1, max_length=1000)  # required on suspend


class LifecycleReinstateBody(_BaseLifecycle):
    pass


class LifecycleGiveNoticeBody(_BaseLifecycle):
    notice_period_start_date: date
    last_working_date: date


class LifecycleExitBody(_BaseLifecycle):
    exit_date: date
    eligible_for_rehire: Optional[bool] = None


class LifecycleArchiveBody(_BaseLifecycle):
    pass
