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
# Training & Development module — Phase 5 (Learning Observatory / LTCMS)
from app.models.hr.skill import (
    Skill, EmployeeSkill, SkillRequirement, SkillCategory, SkillSource,
)
from app.models.hr.certification import (
    Certification, EmployeeCertification, CertificationStatus,
)
from app.models.hr.compliance_training import (
    ComplianceTraining, ComplianceFrequency, FREQUENCY_MONTHS,
)
from app.models.hr.training_request import (
    TrainingRequest, TrainingRequestStatus, TrainingRequestDecision,
)
from app.models.hr.trainer import Trainer, TrainerType
from app.models.hr.training_material import TrainingMaterial, MaterialType
from app.models.hr.training_feedback import TrainingFeedback
from app.models.hr.training_audit_log import TrainingAuditLog, TrainingAuditAction
# Training & Development — Phase 2 (Assessments + Budget)
from app.models.hr.assessment import Assessment, AssessmentResult, AssessmentType
from app.models.hr.training_budget import (
    TrainingBudget, TrainingBudgetItem, BudgetPeriodType, BudgetCostType,
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

# Reimbursements / Employee Claims module — Phase 3.5
from app.models.hr.reimbursement_type import (
    ClaimStatus, ClaimDecision, SettlementMethod, ClaimApproverType, ClaimAuditAction,
)
from app.models.hr.claim_category import ClaimCategory
from app.models.hr.claim_policy import ClaimPolicy
from app.models.hr.claim import Claim
from app.models.hr.claim_settlement import ClaimSettlement
from app.models.hr.claim_audit_log import ClaimAuditLog

# Travel Management module — Phase 4.0 (Aviation Command Deck)
from app.models.hr.travel_type import (
    TravelRequestStatus, TravelDecision, TravelApproverType, TravelPriority,
    CityCategory, BookingType, BookingStatus, AdvanceStatus, DaRecordStatus,
    TravelSettlementStatus, TravelSettlementMethod, TravelExpenseCategory,
    TravelAuditAction,
)
from app.models.hr.travel_category import TravelCategory
from app.models.hr.travel_policy import TravelPolicy
from app.models.hr.travel_request import TravelRequest
from app.models.hr.travel_booking import TravelBooking
from app.models.hr.travel_da import TravelDaRate, TravelDaRecord
from app.models.hr.travel_advance import TravelAdvance
from app.models.hr.travel_settlement import TravelSettlement
from app.models.hr.travel_audit_log import TravelAuditLog

# Exit Management module — Phase 5 (Ceremonial Gateway)
from app.models.hr.exit_type import (
    ResignationType, ExitReasonCategory, ExitCaseStatus, OPEN_CASE_STATUSES,
    ClearanceDepartment, ClearanceItemStatus, SettlementStatus as ExitSettlementStatus,
    InterviewStatus as ExitInterviewStatus, ExitDocStatus, ExitAuditAction,
)
from app.models.hr.exit_policy import ExitPolicy
from app.models.hr.exit_case import ExitCase
from app.models.hr.exit_clearance import ExitClearanceItem
from app.models.hr.exit_interview import ExitInterview
from app.models.hr.exit_settlement import ExitSettlement
from app.models.hr.exit_document import ExitDocument
from app.models.hr.exit_audit_log import ExitAuditLog

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
    # Training & Development (Phase 5 — LTCMS)
    "Skill",
    "EmployeeSkill",
    "SkillRequirement",
    "SkillCategory",
    "SkillSource",
    "Certification",
    "EmployeeCertification",
    "CertificationStatus",
    "ComplianceTraining",
    "ComplianceFrequency",
    "FREQUENCY_MONTHS",
    "TrainingRequest",
    "TrainingRequestStatus",
    "TrainingRequestDecision",
    "Trainer",
    "TrainerType",
    "TrainingMaterial",
    "MaterialType",
    "TrainingFeedback",
    "TrainingAuditLog",
    "TrainingAuditAction",
    "Assessment",
    "AssessmentResult",
    "AssessmentType",
    "TrainingBudget",
    "TrainingBudgetItem",
    "BudgetPeriodType",
    "BudgetCostType",
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
    # Reimbursements / Employee Claims
    "ClaimStatus",
    "ClaimDecision",
    "SettlementMethod",
    "ClaimApproverType",
    "ClaimAuditAction",
    "ClaimCategory",
    "ClaimPolicy",
    "Claim",
    "ClaimSettlement",
    "ClaimAuditLog",
    # Travel Management
    "TravelRequestStatus",
    "TravelDecision",
    "TravelApproverType",
    "TravelPriority",
    "CityCategory",
    "BookingType",
    "BookingStatus",
    "AdvanceStatus",
    "DaRecordStatus",
    "TravelSettlementStatus",
    "TravelSettlementMethod",
    "TravelExpenseCategory",
    "TravelAuditAction",
    "TravelCategory",
    "TravelPolicy",
    "TravelRequest",
    "TravelBooking",
    "TravelDaRate",
    "TravelDaRecord",
    "TravelAdvance",
    "TravelSettlement",
    "TravelAuditLog",
    # Exit Management
    "ResignationType",
    "ExitReasonCategory",
    "ExitCaseStatus",
    "OPEN_CASE_STATUSES",
    "ClearanceDepartment",
    "ClearanceItemStatus",
    "ExitSettlementStatus",
    "ExitInterviewStatus",
    "ExitDocStatus",
    "ExitAuditAction",
    "ExitPolicy",
    "ExitCase",
    "ExitClearanceItem",
    "ExitInterview",
    "ExitSettlement",
    "ExitDocument",
    "ExitAuditLog",
]
