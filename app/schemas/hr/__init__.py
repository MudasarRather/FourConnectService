"""HR Pydantic schemas — re-exports."""
from app.schemas.hr.department import (
    DepartmentCreate, DepartmentUpdate, DepartmentResponse,
)
from app.schemas.hr.grade import GradeCreate, GradeUpdate, GradeResponse
from app.schemas.hr.location import (
    WorkLocationCreate, WorkLocationUpdate, WorkLocationResponse,
)
from app.schemas.hr.designation import (
    DesignationCreate, DesignationUpdate, DesignationResponse,
)
from app.schemas.hr.employee import (
    EmployeeCreate, EmployeeUpdate, EmployeeResponse,
    EmployeeDetailResponse, EmployeeListResponse,
)
from app.schemas.hr.employee_history import EmployeeHistoryResponse
from app.schemas.hr.employee_lifecycle import (
    LifecycleConfirmBody, LifecyclePromoteBody, LifecycleTransferBody,
    LifecycleSuspendBody, LifecycleReinstateBody,
    LifecycleGiveNoticeBody, LifecycleExitBody, LifecycleArchiveBody,
)

__all__ = [
    "DepartmentCreate", "DepartmentUpdate", "DepartmentResponse",
    "GradeCreate", "GradeUpdate", "GradeResponse",
    "WorkLocationCreate", "WorkLocationUpdate", "WorkLocationResponse",
    "DesignationCreate", "DesignationUpdate", "DesignationResponse",
    "EmployeeCreate", "EmployeeUpdate", "EmployeeResponse",
    "EmployeeDetailResponse", "EmployeeListResponse",
    "EmployeeHistoryResponse",
    "LifecycleConfirmBody", "LifecyclePromoteBody", "LifecycleTransferBody",
    "LifecycleSuspendBody", "LifecycleReinstateBody",
    "LifecycleGiveNoticeBody", "LifecycleExitBody", "LifecycleArchiveBody",
]
