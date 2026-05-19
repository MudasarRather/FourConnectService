"""HR models package — Phase 1.0 data spine.

Re-exports models so callers can `from app.models.hr import Employee, Department, ...`.
Importing this package also registers the audit event listeners that write to
the existing `audit_logs` table on Employee / Department / Designation / Grade /
WorkLocation insert / update / delete.
"""
from app.models.hr.department import Department
from app.models.hr.grade import Grade
from app.models.hr.location import WorkLocation, WorkLocationType
from app.models.hr.designation import Designation
from app.models.hr.employee import (
    Employee,
    EmploymentType,
    EmployeeCategory,
    MaritalStatus,
    LifecycleState,
    TaxRegime,
)
from app.models.hr.employee_history import EmployeeHistory, EmployeeChangeType
from app.models.hr.recruitment import (
    JobRequisition,
    JobPosition,
    Candidate,
    Application,
    InterviewPanel,
    Interview,
    InterviewFeedback,
    Offer,
    HiringType,
    RecEmploymentType,
    Priority,
    RequisitionStatus,
    PositionStatus,
    WorkMode,
    CandidateStatus,
    ApplicationStage,
    ApplicationSource,
    InterviewType,
    InterviewMode,
    InterviewRound,
    InterviewStatus,
    FeedbackRecommendation,
    OfferStatus,
)

# Register audit listeners on import.
from app.utils.hr.audit import register_hr_audit_listeners  # noqa: E402

register_hr_audit_listeners()

__all__ = [
    "Department",
    "Grade",
    "WorkLocation",
    "WorkLocationType",
    "Designation",
    "Employee",
    "EmploymentType",
    "EmployeeCategory",
    "MaritalStatus",
    "LifecycleState",
    "TaxRegime",
    "EmployeeHistory",
    "EmployeeChangeType",
    "JobRequisition",
    "JobPosition",
    "Candidate",
    "Application",
    "InterviewPanel",
    "Interview",
    "InterviewFeedback",
    "Offer",
    "HiringType",
    "RecEmploymentType",
    "Priority",
    "RequisitionStatus",
    "PositionStatus",
    "WorkMode",
    "CandidateStatus",
    "ApplicationStage",
    "ApplicationSource",
    "InterviewType",
    "InterviewMode",
    "InterviewRound",
    "InterviewStatus",
    "FeedbackRecommendation",
    "OfferStatus",
]
