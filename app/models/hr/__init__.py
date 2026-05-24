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
from app.models.hr.onboarding import (
    OnboardingProcess,
    OnboardingChecklistTemplate,
    OnboardingChecklistItem,
    OnboardingDocument,
    JoiningApproval,
    OnboardingTask,
    EmployeeIdentity,
    WelcomeKit,
    WelcomeKitTemplate,
    OnboardingStatus,
    OnboardingStage,
    ChecklistCategory,
    ChecklistItemStatus,
    DocumentSlotStatus,
    ApprovalRole,
    ApprovalDecision,
    TaskStatus as OnbTaskStatus,
    TaskPriority as OnbTaskPriority,
    IdentityStatus,
    WelcomeKitStatus,
)
from app.models.hr.asset import (
    Asset,
    AssetAllocation,
    AssetType,
    AssetCondition,
    AssetStatus,
    AllocationStatus,
)
from app.models.hr.training import (
    TrainingProgram,
    TrainingAssignment,
    TrainingType,
    TrainingAssignmentStatus,
)
from app.models.hr.induction import (
    InductionSession,
    InductionAttendance,
    InductionType,
    AttendanceStatus,
)
from app.models.hr.account_provisioning import (
    AccountProvisioning,
    AccountType,
    AccountProvisioningStatus,
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
    # Onboarding
    "OnboardingProcess",
    "OnboardingChecklistTemplate",
    "OnboardingChecklistItem",
    "OnboardingDocument",
    "JoiningApproval",
    "OnboardingTask",
    "EmployeeIdentity",
    "WelcomeKit",
    "WelcomeKitTemplate",
    "OnboardingStatus",
    "OnboardingStage",
    "ChecklistCategory",
    "ChecklistItemStatus",
    "DocumentSlotStatus",
    "ApprovalRole",
    "ApprovalDecision",
    "OnbTaskStatus",
    "OnbTaskPriority",
    "IdentityStatus",
    "WelcomeKitStatus",
    # Assets
    "Asset",
    "AssetAllocation",
    "AssetType",
    "AssetCondition",
    "AssetStatus",
    "AllocationStatus",
    # Training
    "TrainingProgram",
    "TrainingAssignment",
    "TrainingType",
    "TrainingAssignmentStatus",
    # Induction
    "InductionSession",
    "InductionAttendance",
    "InductionType",
    "AttendanceStatus",
    # Account Provisioning
    "AccountProvisioning",
    "AccountType",
    "AccountProvisioningStatus",
]
