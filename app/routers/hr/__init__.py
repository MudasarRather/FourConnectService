"""HR routers package — aggregates all HR sub-routers under a single APIRouter
so main.py can register the package with one include_router call.

Sub-routers each set their own prefix (e.g. ``/hr/employees``); this package
mounts them flat so URLs stay clean and stable.
"""
from fastapi import APIRouter

from app.routers.hr.dashboard import router as _dashboard_router
from app.routers.hr.employees import router as _employees_router
from app.routers.hr.departments import router as _departments_router
from app.routers.hr.designations import router as _designations_router
from app.routers.hr.grades import router as _grades_router
from app.routers.hr.locations import router as _locations_router
from app.routers.hr.recruitment import router as _recruitment_router
from app.routers.hr.onboarding import router as _onboarding_router
from app.routers.hr.onboarding_documents import router as _onb_documents_router
from app.routers.hr.identity import router as _identity_router
from app.routers.hr.assets import router as _assets_router
from app.routers.hr.training import router as _training_router
from app.routers.hr.induction import router as _induction_router
from app.routers.hr.account_provisioning import router as _account_provisioning_router
from app.routers.hr.welcome_kit import router as _welcome_kit_router
from app.routers.hr.employee_documents import router as _employee_documents_router
from app.routers.hr.employee_document_reports import router as _employee_document_reports_router

# Attendance module — Phase 2.0
from app.routers.hr.shifts import router as _shifts_router
# Shifts & Rosters module — Phase 2.5 (Control Tower)
from app.routers.hr.shift_rotations import router as _shift_rotations_router
from app.routers.hr.shift_rosters import router as _shift_rosters_router
from app.routers.hr.shift_coverage import router as _shift_coverage_router
# Shifts & Rosters Phase 2 (ops)
from app.routers.hr.overtime_rules import router as _overtime_rules_router
from app.routers.hr.shift_swaps import router as _shift_swaps_router
from app.routers.hr.holiday_shifts import router as _holiday_shifts_router
from app.routers.hr.night_policies import router as _night_policies_router
from app.routers.hr.workforce import router as _workforce_router
from app.routers.hr.shift_reports import router as _shift_reports_router
from app.routers.hr.attendance import router as _attendance_router
from app.routers.hr.attendance_corrections import router as _att_corrections_router
from app.routers.hr.wfh import router as _wfh_router
from app.routers.hr.overtime import router as _overtime_router
from app.routers.hr.holidays import router as _holidays_router
from app.routers.hr.attendance_policies import router as _att_policies_router
from app.routers.hr.geo_fences import router as _geo_fences_router
from app.routers.hr.biometric import router as _biometric_router
from app.routers.hr.attendance_logs import router as _attendance_logs_router
from app.routers.hr.attendance_reports import router as _attendance_reports_router
from app.routers.hr.half_day import router as _half_day_router
from app.routers.hr.leaves import router as _leaves_router

# Payroll module — Phase 3.0
from app.routers.hr.payroll_config import router as _payroll_config_router
from app.routers.hr.payroll_components import router as _payroll_components_router
from app.routers.hr.payroll_structures import router as _payroll_structures_router
from app.routers.hr.employee_compensation import router as _employee_compensation_router
from app.routers.hr.payroll_batches import router as _payroll_batches_router
from app.routers.hr.payroll_self import router as _payroll_self_router
from app.routers.hr.payslips import router as _payslips_router
from app.routers.hr.payroll_adjustments import router as _payroll_adjustments_router
from app.routers.hr.payroll_reports import router as _payroll_reports_router

router = APIRouter()
router.include_router(_dashboard_router)
router.include_router(_employees_router)
router.include_router(_departments_router)
router.include_router(_designations_router)
router.include_router(_grades_router)
router.include_router(_locations_router)
router.include_router(_recruitment_router)
router.include_router(_onboarding_router)
router.include_router(_onb_documents_router)
router.include_router(_identity_router)
router.include_router(_assets_router)
router.include_router(_training_router)
router.include_router(_induction_router)
router.include_router(_account_provisioning_router)
router.include_router(_welcome_kit_router)
# Reports sub-router (prefix /hr/employee-documents/reports) registers before
# the broad employee-documents router so /reports/... isn't shadowed by the
# `/{doc_id}` route on the broad router.
router.include_router(_employee_document_reports_router)
router.include_router(_employee_documents_router)

# Attendance — order matters when prefixes share roots: more-specific
# (corrections, policies, logs) before the broad /hr/attendance router. The
# distinct prefixes don't actually collide, but keeping this order makes the
# OpenAPI route table easier to scan.
# Shift reports sub-router (prefix /hr/shifts/reports) before the broad shifts
# router so /reports/... isn't shadowed by the `/{shift_id}` route.
router.include_router(_shift_reports_router)
router.include_router(_shifts_router)
# Shifts & Rosters — distinct prefixes (/hr/shift-rotations, /hr/shift-rosters, /hr/shift-coverage)
router.include_router(_shift_rotations_router)
router.include_router(_shift_rosters_router)
router.include_router(_shift_coverage_router)
router.include_router(_overtime_rules_router)
router.include_router(_shift_swaps_router)
router.include_router(_holiday_shifts_router)
router.include_router(_night_policies_router)
router.include_router(_workforce_router)
router.include_router(_att_corrections_router)
router.include_router(_att_policies_router)
router.include_router(_attendance_logs_router)
# IMPORTANT: reports sub-router (prefix /hr/attendance/reports) must register
# before the broad attendance router (prefix /hr/attendance) so /reports/...
# isn't shadowed by the `/{attendance_id}` route on the broad router.
router.include_router(_attendance_reports_router)
router.include_router(_attendance_router)
router.include_router(_wfh_router)
router.include_router(_half_day_router)
router.include_router(_leaves_router)
router.include_router(_overtime_router)
router.include_router(_holidays_router)
router.include_router(_geo_fences_router)
router.include_router(_biometric_router)

# Payroll — register config/dashboard + self-service before the broad payslips
# router so /hr/payroll/dashboard, /hr/payroll/config/* and /hr/me/payslips/*
# aren't shadowed by the `/{payslip_id}` route on the payslips router.
router.include_router(_payroll_config_router)
router.include_router(_payroll_reports_router)
router.include_router(_payroll_adjustments_router)
router.include_router(_payroll_components_router)
router.include_router(_payroll_structures_router)
router.include_router(_employee_compensation_router)
router.include_router(_payroll_batches_router)
router.include_router(_payroll_self_router)
router.include_router(_payslips_router)
