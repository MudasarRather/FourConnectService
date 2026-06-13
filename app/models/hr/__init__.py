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
    AttendanceStatus as InductionAttendanceStatus,
)
from app.models.hr.account_provisioning import (
    AccountProvisioning,
    AccountType,
    AccountProvisioningStatus,
)
from app.models.hr.employee_document import (
    EmployeeDocument,
    EmployeeDocumentEvent,
    EmployeeDocumentTemplate,
    DocumentCategory,
    DocVerificationStatus,
    DocSource,
    DocTemplateType,
    CONFIDENTIAL_CATEGORIES,
)
from app.models.hr.document_request import (
    DocumentRequest,
    DocumentRequestType,
    DocumentRequestStatus,
)
# Attendance module — Phase 2.0
from app.models.hr.shift import Shift, EmployeeShiftAssignment, ShiftType
# Shifts & Rosters module — Phase 2.5 (Control Tower)
from app.models.hr.shift_rotation import (
    ShiftRotation, ShiftRotationStep, ShiftRotationMember, RotationCycle,
)
from app.models.hr.shift_roster import ShiftRoster, ShiftRosterEntry, RosterStatus
from app.models.hr.shift_coverage import ShiftCoverageRule
# Shifts & Rosters — Phase 2 (ops)
from app.models.hr.overtime_rule import OvertimeRule
from app.models.hr.shift_swap import ShiftSwapRequest, SwapStatus
from app.models.hr.holiday_shift import HolidayShiftAssignment, HolidayCompType
from app.models.hr.night_policy import NightShiftPolicy
from app.models.hr.workforce_demand import WorkforceDemand
from app.models.hr.attendance import Attendance, AttendanceStatus, AttendanceSource
from app.models.hr.attendance_punch import AttendancePunch, PunchType
from app.models.hr.attendance_correction import AttendanceCorrection, CorrectionStatus
from app.models.hr.wfh_request import WfhRequest, WfhStatus, WfhRequestType
from app.models.hr.overtime import OvertimeRequest, OtType, OtStatus, OtPayrollStatus
from app.models.hr.holiday import Holiday, HolidayType
from app.models.hr.attendance_policy import AttendancePolicy, PolicyType
from app.models.hr.geo_fence import GeoFence
from app.models.hr.biometric_device import BiometricDevice, BiometricDeviceType, BiometricDeviceStatus
from app.models.hr.attendance_log import AttendanceLog, AttendanceLogAction

# Payroll module — Phase 3.0
from app.models.hr.salary_structure import SalaryStructure
from app.models.hr.salary_component import (
    SalaryComponent, ComponentType, CalcType, StatutoryKind,
)
from app.models.hr.salary_structure_component import SalaryStructureComponent
from app.models.hr.employee_compensation import EmployeeCompensation, CompensationStatus
from app.models.hr.payroll_batch import PayrollBatch, PayrollBatchStatus
from app.models.hr.payslip import Payslip, PayslipLine, PayslipStatus
from app.models.hr.payroll_config import (
    StatutoryConfig, PayrollAuditLog, PayrollAuditAction,
)
from app.models.hr.payroll_adjustment import (
    PayrollAdjustment, AdjustmentType, AdjustmentStatus,
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
    "InductionAttendanceStatus",
    # Account Provisioning
    "AccountProvisioning",
    "AccountType",
    "AccountProvisioningStatus",
    # Employee Documents
    "EmployeeDocument",
    "EmployeeDocumentEvent",
    "EmployeeDocumentTemplate",
    "DocumentCategory",
    "DocVerificationStatus",
    "DocSource",
    "DocTemplateType",
    "CONFIDENTIAL_CATEGORIES",
    # Document Requests
    "DocumentRequest",
    "DocumentRequestType",
    "DocumentRequestStatus",
    # Attendance / Shifts
    "Shift",
    "EmployeeShiftAssignment",
    "ShiftType",
    # Shifts & Rosters (Control Tower)
    "ShiftRotation",
    "ShiftRotationStep",
    "ShiftRotationMember",
    "RotationCycle",
    "ShiftRoster",
    "ShiftRosterEntry",
    "RosterStatus",
    "ShiftCoverageRule",
    "OvertimeRule",
    "ShiftSwapRequest",
    "SwapStatus",
    "HolidayShiftAssignment",
    "HolidayCompType",
    "NightShiftPolicy",
    "WorkforceDemand",
    "Attendance",
    "AttendanceStatus",
    "AttendanceSource",
    "AttendancePunch",
    "PunchType",
    "AttendanceCorrection",
    "CorrectionStatus",
    "WfhRequest",
    "WfhStatus",
    "WfhRequestType",
    "OvertimeRequest",
    "OtType",
    "OtStatus",
    "OtPayrollStatus",
    "Holiday",
    "HolidayType",
    "AttendancePolicy",
    "PolicyType",
    "GeoFence",
    "BiometricDevice",
    "BiometricDeviceType",
    "BiometricDeviceStatus",
    "AttendanceLog",
    "AttendanceLogAction",
    # Payroll
    "SalaryStructure",
    "SalaryComponent",
    "ComponentType",
    "CalcType",
    "StatutoryKind",
    "SalaryStructureComponent",
    "EmployeeCompensation",
    "CompensationStatus",
    "PayrollBatch",
    "PayrollBatchStatus",
    "Payslip",
    "PayslipLine",
    "PayslipStatus",
    "StatutoryConfig",
    "PayrollAuditLog",
    "PayrollAuditAction",
    "PayrollAdjustment",
    "AdjustmentType",
    "AdjustmentStatus",
]
