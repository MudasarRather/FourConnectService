"""HR Exit Management — admin router (prefix /hr/exit).

Orchestrates the full separation lifecycle. ``ExitCase.status`` is the workflow
overlay; the canonical employee lifecycle (ON_NOTICE / EXITED / ARCHIVED) stays on
the Employee row and is mutated ONLY via ``_sync_employee_lifecycle`` which calls
the existing ``employees.py`` lifecycle handlers — so EmployeeHistory + asset
offboarding fire identically and a bad transition surfaces the existing 409.

Route ordering note: literal roots (/dashboard, /cases, /policies, /audit-logs,
/notice-board, /verify/{code}, /clearance-items/...) are declared and the case
catch-all is pinned to ``/{case_id:uuid}`` so it never shadows them.
"""
from __future__ import annotations

import math
import secrets
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_superuser
from app.models.hr.employee import Employee, LifecycleState
from app.models.hr.department import Department
from app.models.hr.exit_case import ExitCase
from app.models.hr.exit_clearance import ExitClearanceItem
from app.models.hr.exit_interview import ExitInterview
from app.models.hr.exit_settlement import ExitSettlement
from app.models.hr.exit_policy import ExitPolicy
from app.models.hr.exit_document import ExitDocument
from app.models.hr.exit_audit_log import ExitAuditLog
from app.models.hr.exit_type import (
    ExitCaseStatus, ResignationType, ClearanceItemStatus, SettlementStatus,
    InterviewStatus, ExitDocStatus, ExitAuditAction,
)
from app.models.hr.employee_document import DocTemplateType
from app.schemas.hr.exit_management import (
    ExitCaseCreate, ExitCaseUpdate, ExitCaseResponse, ExitCaseDetailResponse,
    ExitCaseListResponse, ExitSubmitBody, ManagerDecisionBody, AcceptBody,
    RejectBody, CancelBody, DeleteCaseBody, StartNoticeBody, WaiveNoticeBody, NoticeAdjustBody,
    FinalizeExitBody, ClearanceItemUpdate, ClearanceReopenBody, ClearanceItemResponse,
    HrRecordsApplyBody, FfAckApplyBody, FinLoansApplyBody, ClearanceApplyResponse,
    InterviewScheduleBody, InterviewSubmitBody, ExitInterviewResponse,
    SettlementRecalcBody, SettlementUpdate, SettlementVerifyBody, SettlementApproveBody,
    SettlementPayBody, SettlementReverseBody, SettlementCloseBody, ArchiveCaseBody, ExitSettlementResponse,
    LetterGenerateBody, LetterRevokeBody, ExitDocumentResponse, LetterVerifyResponse,
    ExitPolicyCreate, ExitPolicyUpdate, ExitPolicyResponse, ExitPolicyListResponse,
    ExitAuditLogResponse, ExitAuditLogListResponse,
)
from app.schemas.hr.employee_lifecycle import (
    LifecycleGiveNoticeBody, LifecycleExitBody, LifecycleArchiveBody, _BaseLifecycle,
)
from app.utils.hr.exit_management.audit import write_exit_audit
from app.utils.hr.exit_management import service as svc
from app.utils.hr.exit_bootstrap import bootstrap_exit
from app.utils.hr.exit_management.settlement_engine import compute_settlement
from app.utils.hr.exit_management import payroll_post
from app.utils.hr.exit_management import payment_advice as _padvice
from app.utils.hr.exit_management.notice_serving import notice_metrics, notice_serving_snapshot, notice_start_moment, is_notice_served

router = APIRouter(prefix="/hr/exit", tags=["HR — Exit Management"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_case(db: Session, case_id: UUID) -> ExitCase:
    case = (
        db.query(ExitCase)
        .options(
            joinedload(ExitCase.employee).joinedload(Employee.user),
            joinedload(ExitCase.employee).joinedload(Employee.designation),
            joinedload(ExitCase.department),
            joinedload(ExitCase.policy),
            joinedload(ExitCase.settlement),
            joinedload(ExitCase.interview),
            joinedload(ExitCase.clearance_items),
            joinedload(ExitCase.documents),
        )
        .filter(ExitCase.id == case_id, ExitCase.is_deleted == False)  # noqa: E712
        .first()
    )
    if not case:
        raise HTTPException(404, "Exit case not found")
    return case


def _detail(db: Session, case: ExitCase) -> dict:
    base = svc.case_to_response(db, case)
    base["lifecycle_consistent"] = svc.lifecycle_consistent(case)
    base["rejection_reason"] = case.rejection_reason
    base["cancel_reason"] = case.cancel_reason
    base["manager_notes"] = case.manager_notes
    base["clearance_items"] = [
        svc.clearance_item_to_response(db, i)
        for i in sorted(case.clearance_items or [], key=lambda x: x.sort_order)
    ]
    base["interview"] = _interview_resp(db, case) if case.interview else None
    base["settlement"] = ExitSettlementResponse.model_validate(case.settlement).model_dump() if case.settlement else None
    base["documents"] = [ExitDocumentResponse.model_validate(d).model_dump() for d in (case.documents or [])]
    base["policy"] = _policy_resp(db, case.policy) if case.policy else None
    # The actual moment notice began (date + time + working-hours check).
    base["notice_start"] = notice_start_moment(db, case)
    return base


def _interview_resp(db: Session, case: ExitCase) -> dict:
    iv = case.interview
    data = ExitInterviewResponse.model_validate(iv).model_dump()
    data["conducted_by_name"] = svc._user_name(db, iv.conducted_by_id)
    questions = []
    if case.policy and case.policy.interview_questions:
        questions = list(case.policy.interview_questions)
    if not questions:
        questions = svc.DEFAULT_INTERVIEW_QUESTIONS
    data["questions"] = questions
    return data


def _policy_resp(db: Session, p: ExitPolicy) -> dict:
    data = ExitPolicyResponse.model_validate(p).model_dump()
    if p.grade_id:
        from app.models.hr.grade import Grade
        g = db.query(Grade).filter(Grade.id == p.grade_id).first()
        data["grade_name"] = g.name if g else None
    return data


def _sync_employee_lifecycle(db: Session, case: ExitCase, action: str, actor: User, body) -> None:
    """The ONLY path that mutates Employee lifecycle state. Calls the existing
    employees.py handlers (which guard + commit + write EmployeeHistory + fire
    asset offboarding). Raises the existing 409 on a bad transition."""
    from app.routers.hr import employees as emp_router
    pk = case.employee_id
    if action == "give-notice":
        emp_router.lifecycle_give_notice(pk, body, db, actor)
    elif action == "exit":
        emp_router.lifecycle_exit(pk, body, db, actor)
    elif action == "archive":
        emp_router.lifecycle_archive(pk, body, db, actor)
    elif action == "cancel-notice":
        emp_router.lifecycle_cancel_notice(pk, body, db, actor)
    else:
        raise HTTPException(500, f"Unknown lifecycle action {action}")


def _paginate(query, page: int, limit: int):
    total = query.count()
    rows = query.offset((page - 1) * limit).limit(limit).all()
    total_pages = max(1, math.ceil(total / limit)) if limit else 1
    return rows, total, total_pages


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    base = db.query(ExitCase).options(joinedload(ExitCase.employee)).filter(
        ExitCase.is_deleted == False)  # noqa: E712
    if department_id:
        base = base.filter(ExitCase.department_id == department_id)

    cases = base.all()
    by_status: dict = {}
    by_type: dict = {}
    by_reason: dict = {}
    # A COMPLETED case means the employee has been RELIEVED (lifecycle → EXITED).
    # Archiving is a *separate*, later action (lifecycle → ARCHIVED) that does NOT
    # change the case status — so the raw COMPLETED bucket conflates both. Split it
    # by the employee's lifecycle so the dashboard procession can show "Relieved"
    # and "Archived" as distinct stages instead of dumping every relieved person
    # under Archive.
    completed_relieved = 0
    completed_archived = 0
    for c in cases:
        by_status[c.status.value] = by_status.get(c.status.value, 0) + 1
        by_type[c.resignation_type.value] = by_type.get(c.resignation_type.value, 0) + 1
        if c.reason_category:
            by_reason[c.reason_category.value] = by_reason.get(c.reason_category.value, 0) + 1
        if c.status == ExitCaseStatus.COMPLETED:
            if c.employee and c.employee.lifecycle_state == LifecycleState.ARCHIVED:
                completed_archived += 1
            else:
                completed_relieved += 1

    open_statuses = {s for s in ExitCaseStatus} - {
        ExitCaseStatus.COMPLETED, ExitCaseStatus.REJECTED,
        ExitCaseStatus.WITHDRAWN, ExitCaseStatus.CANCELLED,
    }
    active = [c for c in cases if c.status in open_statuses]
    serving_notice = [c for c in cases if c.status == ExitCaseStatus.NOTICE_PERIOD]

    # Pending clearances / settlements.
    pending_clearance = sum(1 for c in active if (c.clearance_progress_pct or 0) < 100
                            and c.status in (ExitCaseStatus.CLEARANCE, ExitCaseStatus.NOTICE_PERIOD))
    pending_settlement = db.query(func.count(ExitSettlement.id)).join(
        ExitCase, ExitCase.id == ExitSettlement.exit_case_id).filter(
        ExitCase.is_deleted == False,  # noqa: E712
        ExitSettlement.status.in_([SettlementStatus.DRAFT, SettlementStatus.VERIFIED, SettlementStatus.APPROVED]),
    ).scalar() or 0
    # "Interviews due" = everything not yet conducted: awaiting scheduling (PENDING),
    # scheduled, or mid-session.
    pending_interview = db.query(func.count(ExitInterview.id)).join(
        ExitCase, ExitCase.id == ExitInterview.exit_case_id).filter(
        ExitCase.is_deleted == False,  # noqa: E712
        ExitInterview.status.in_([
            InterviewStatus.PENDING, InterviewStatus.SCHEDULED, InterviewStatus.IN_PROGRESS,
        ]),
    ).scalar() or 0

    # Relieved this month.
    today = date.today()
    relieved_this_month = sum(
        1 for c in cases
        if c.exit_date and c.exit_date.year == today.year and c.exit_date.month == today.month
    )

    # Avg processing days (resignation_date → exit_date) for completed.
    durations = [
        (c.exit_date - c.resignation_date).days
        for c in cases if c.exit_date and c.resignation_date and c.exit_date >= c.resignation_date
    ]
    avg_processing = round(sum(durations) / len(durations), 1) if durations else 0

    # Notice board: ON_NOTICE employees, days remaining.
    notice_board = []
    for c in serving_notice:
        lwd = c.last_working_date
        days_left = (lwd - today).days if lwd else None
        emp = c.employee
        lbl = svc.employee_label(emp)
        notice_board.append({
            "case_id": str(c.id), "case_number": c.case_number,
            "employee_id": str(c.employee_id), "employee_name": lbl["employee_name"],
            "last_working_date": lwd.isoformat() if lwd else None,
            "days_remaining": days_left, "overdue": (days_left is not None and days_left < 0),
            "clearance_progress_pct": c.clearance_progress_pct or 0,
        })
    notice_board.sort(key=lambda x: (x["days_remaining"] is None, x["days_remaining"] if x["days_remaining"] is not None else 0))

    return {
        "kpis": {
            "active_resignations": len(active),
            "serving_notice": len(serving_notice),
            "pending_clearances": pending_clearance,
            "pending_settlements": int(pending_settlement),
            "pending_interviews": int(pending_interview),
            "relieved_this_month": relieved_this_month,
            "avg_processing_days": avg_processing,
            "total_cases": len(cases),
        },
        "by_status": by_status,
        "by_type": by_type,
        "by_reason": by_reason,
        # COMPLETED split by employee lifecycle: relieved (EXITED) vs archived.
        "completed_relieved": completed_relieved,
        "completed_archived": completed_archived,
        "notice_board": notice_board[:25],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Notice board (literal — must precede /{case_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/notice-board")
def notice_board(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    today = date.today()
    cases = (
        db.query(ExitCase)
        .options(joinedload(ExitCase.employee).joinedload(Employee.user),
                 joinedload(ExitCase.department),
                 joinedload(ExitCase.settlement))
        .filter(ExitCase.is_deleted == False,  # noqa: E712
                ExitCase.status == ExitCaseStatus.NOTICE_PERIOD)
        .all()
    )
    # An employee is only actively "serving notice" while they are still employed
    # and their F&F is open. Once they've SEPARATED (lifecycle EXITED/ARCHIVED/
    # INACTIVE) or the settlement is PAID/CLOSED, the separation has concluded —
    # so they must drop off the active board (the old query filtered ONLY on
    # case.status == NOTICE_PERIOD, which left an exited+settled employee stuck on
    # the board with a live "Serving" countdown).
    _SEPARATED = ("EXITED", "ARCHIVED", "INACTIVE")
    out = []
    concluded = []
    healed = 0
    for c in cases:
        lbl = svc.employee_label(c.employee)
        lc = getattr(c.employee, "lifecycle_state", None)
        lc_val = lc.value if hasattr(lc, "value") else (str(lc) if lc else "")
        separated = lc_val in _SEPARATED
        st = c.settlement
        st_val = st.status.value if (st and hasattr(st.status, "value")) else None
        settled = st_val in ("PAID", "CLOSED")

        if separated or settled:
            if separated and settled:
                reason = "Employee has exited and the F&F is settled"
            elif separated:
                reason = f"Employee is {lc_val.lower()}"
            else:
                reason = f"Full & final settlement {st_val.lower()}"
            auto_completed = False
            # Self-heal a TERMINAL case (exited AND F&F settled) whose status was
            # never advanced off NOTICE_PERIOD — reconcile it to COMPLETED so it
            # leaves the notice stage everywhere, not just this board. Idempotent
            # (a COMPLETED case won't match the NOTICE_PERIOD query next time) and
            # audited. Mirrors the get_clearance read-time reconcile pattern.
            if separated and settled:
                frm = c.status.value
                c.status = ExitCaseStatus.COMPLETED
                if not c.exit_date:
                    c.exit_date = getattr(c.employee, "exit_date", None) or today
                write_exit_audit(
                    db, entity_type="CASE", action=ExitAuditAction.EXITED,
                    exit_case_id=c.id, entity_id=c.id, actor_id=admin.id,
                    from_status=frm, to_status=c.status.value,
                    note="Auto-concluded — employee exited & F&F settled (notice board reconcile)",
                )
                auto_completed = True
                healed += 1
            concluded.append({
                "case_id": str(c.id), "case_number": c.case_number,
                "employee_id": str(c.employee_id), "employee_name": lbl["employee_name"],
                "employee_code": lbl["employee_code"],
                "department_name": c.department.name if c.department else None,
                "lifecycle_state": lc_val or None,
                "settlement_status": st_val,
                "reason": reason,
                "auto_completed": auto_completed,
            })
            continue

        # Anchor the countdown on the REAL notice window (start → LWD), not a naive
        # LWD-minus-today: served/progress/short-notice all derive from the start date.
        m = notice_metrics(c)
        out.append({
            "case_id": str(c.id), "case_number": c.case_number,
            "employee_id": str(c.employee_id), "employee_name": lbl["employee_name"],
            "employee_code": lbl["employee_code"],
            "department_name": c.department.name if c.department else None,
            "notice_period_days": c.notice_period_days,
            "days_remaining": m["remaining_days"], "overdue": m["overdue"],
            "notice_waived": c.notice_waived,
            "clearance_progress_pct": c.clearance_progress_pct or 0,
            # date-anchored notice metrics
            "notice_period_start_date": m["notice_period_start_date"],
            "last_working_date": m["last_working_date"],
            "required_days": m["required_days"],
            "notice_total_days": m["notice_total_days"],
            "served_days": m["served_days"],
            "progress_pct": m["progress_pct"],
            "not_started": m["not_started"],
            "short_notice": m["short_notice"],
            "shortfall_days": m["shortfall_days"],
        })
    if healed:
        db.commit()
    out.sort(key=lambda x: (x["days_remaining"] is None, x["days_remaining"] if x["days_remaining"] is not None else 0))
    return {"items": out, "total": len(out), "concluded": concluded}


# ─────────────────────────────────────────────────────────────────────────────
# Notice preview (literal — must precede /{case_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/notice-preview")
def notice_preview(
    employee_id: UUID,
    resignation_type: ResignationType = ResignationType.VOLUNTARY,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Resolve the notice period that WOULD apply to a separation, BEFORE the case
    is accepted. Mirrors ``accept_case``: ``resolve_policy`` (grade-specific →
    wildcard → default 30) and the probation / waiver rules. Lets the New-separation
    UI inform HR + the employee of the applicable notice up-front."""
    emp = (
        db.query(Employee)
        .filter(Employee.id == employee_id, Employee.is_deleted == False)  # noqa: E712
        .first()
    )
    if not emp:
        raise HTTPException(404, "Employee not found")
    policy = svc.resolve_policy(db, emp)
    base_notice = policy.notice_period_days if policy else 30
    prob_notice = policy.probation_notice_days if policy else 7
    waived = resignation_type in (ResignationType.TERMINATION, ResignationType.MUTUAL_SEPARATION)
    applied = prob_notice if resignation_type == ResignationType.PROBATION_EXIT else base_notice
    if waived:
        applied = 0
    return {
        "employee_id": str(emp.id),
        "resignation_type": resignation_type.value,
        "policy_id": str(policy.id) if policy else None,
        "policy_name": policy.policy_name if policy else None,
        "is_default": policy is None,
        "notice_period_days": base_notice,
        "probation_notice_days": prob_notice,
        "applied_notice_days": applied,
        "notice_waived": waived,
        "buyout_allowed": bool(policy.buyout_allowed) if policy else True,
        "on_probation": emp.lifecycle_state == LifecycleState.ON_PROBATION,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cases — list / create
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/cases", response_model=ExitCaseListResponse)
def list_cases(
    status: Optional[ExitCaseStatus] = None,
    resignation_type: Optional[ResignationType] = None,
    department_id: Optional[UUID] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    query = (
        db.query(ExitCase)
        .options(joinedload(ExitCase.employee).joinedload(Employee.user),
                 joinedload(ExitCase.employee).joinedload(Employee.designation),
                 joinedload(ExitCase.department))
        .filter(ExitCase.is_deleted == False)  # noqa: E712
    )
    if status:
        query = query.filter(ExitCase.status == status)
    if resignation_type:
        query = query.filter(ExitCase.resignation_type == resignation_type)
    if department_id:
        query = query.filter(ExitCase.department_id == department_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.outerjoin(Employee, Employee.id == ExitCase.employee_id).filter(
            or_(ExitCase.case_number.ilike(like),
                Employee.employee_id.ilike(like),
                Employee.employee_code.ilike(like))
        )
    query = query.order_by(ExitCase.created_at.desc())
    rows, total, total_pages = _paginate(query, page, limit)
    return {
        "items": [svc.case_to_response(db, c) for c in rows],
        "total": total, "page": page, "limit": limit, "total_pages": total_pages,
    }


# Literal path — must precede the dynamic /{case_id} routes (it lives under
# /cases/ so it never collides, but keep it grouped with the case list).
@router.get("/cases/active-for-employee")
def active_case_for_employee(
    employee_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Resolve whether an employee currently has an OPEN exit case.

    Backs the 'Initiate Exit' deep-link from the employee profile / lifecycle
    surfaces: the Resignation tab uses this to FOCUS an already-open case instead
    of trying to create a duplicate (``create_case`` would otherwise 409), and to
    pre-select the employee in the create modal even when they fall outside the
    lite employee list's first page. The Exit module is the single owner of
    offboarding, so the profile no longer mutates lifecycle for separation."""
    emp = (
        db.query(Employee)
        .options(joinedload(Employee.user))
        .filter(Employee.id == employee_id, Employee.is_deleted == False)  # noqa: E712
        .first()
    )
    if not emp:
        raise HTTPException(404, "Employee not found")
    name = None
    if emp.user is not None:
        name = getattr(emp.user, "full_name", None) or getattr(emp.user, "name", None)
    name = name or getattr(emp, "full_name", None) or emp.employee_id
    case = svc.open_case_for_employee(db, emp.id)
    return {
        "employee": {
            "id": str(emp.id),
            "name": name,
            "code": emp.employee_id,
            "lifecycle_state": emp.lifecycle_state.value if emp.lifecycle_state else None,
        },
        "open_case": (
            {"id": str(case.id), "case_number": case.case_number, "status": case.status.value}
            if case else None
        ),
    }


@router.post("/cases", response_model=ExitCaseDetailResponse, status_code=201)
def create_case(
    body: ExitCaseCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    emp = db.query(Employee).options(joinedload(Employee.user)).filter(
        Employee.id == body.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    if emp.lifecycle_state in (LifecycleState.EXITED, LifecycleState.ARCHIVED):
        raise HTTPException(409, f"Employee is already {emp.lifecycle_state.value}")
    existing = svc.open_case_for_employee(db, emp.id)
    if existing:
        raise HTTPException(409, f"An open exit case ({existing.case_number}) already exists for this employee")

    case = ExitCase(
        case_number=svc.generate_case_number(db),
        employee_id=emp.id,
        resignation_type=body.resignation_type,
        reason_category=body.reason_category,
        reason_detail=body.reason_detail,
        status=ExitCaseStatus.DRAFT,
        initiated_by="HR",
        resignation_date=body.resignation_date or date.today(),
        requested_last_working_date=body.requested_last_working_date,
        manager_id=emp.reporting_manager_id,
        department_id=emp.department_id,
        designation_id=emp.designation_id,
        grade_id=emp.grade_id,
        employee_category=emp.employee_category,
        joining_date_snapshot=emp.joining_date,
        created_by_id=admin.id,
        last_updated_by_id=admin.id,
    )
    # Apply the selected exit policy (defaults to the grade match in the UI). Pre-fill
    # notice days so the case is transparent pre-accept; resolved_notice_days() honours
    # a pre-set value, so Accept stays consistent with what HR chose here.
    if body.policy_id:
        policy = db.query(ExitPolicy).filter(
            ExitPolicy.id == body.policy_id, ExitPolicy.is_active == True,  # noqa: E712
            ExitPolicy.is_deleted == False).first()  # noqa: E712
        if not policy:
            raise HTTPException(400, "Selected exit policy not found or inactive")
        case.policy_id = policy.id
        if body.resignation_type in (ResignationType.TERMINATION, ResignationType.MUTUAL_SEPARATION):
            case.notice_period_days = 0
            case.notice_waived = True
        elif body.resignation_type == ResignationType.PROBATION_EXIT:
            case.notice_period_days = policy.probation_notice_days
        else:
            case.notice_period_days = policy.notice_period_days
    db.add(case)
    db.flush()
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.CREATED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     to_status=case.status.value, note="HR-initiated exit case")
    db.commit()
    # Notify the reporting manager (internal awareness). The employee is NOT pinged
    # here — this endpoint is HR-initiated/DRAFT and may be a sensitive in-progress
    # action; the employee-facing exit ping is CLEARANCE_PENDING (fired on accept).
    try:
        from app.utils.hr.notify import dispatch
        if case.manager_id:
            dispatch(db, "EXIT_INITIATED", case.manager_id, audience="MANAGER", context={
                "title": "Exit initiated for a team member",
                "message": f"Exit case {case.case_number} has been initiated.",
                "action_url": "/admin/hr/exit/dashboard",
            })
            db.commit()
    except Exception:
        db.rollback()
    return _detail(db, _get_case(db, case.id))


# ─────────────────────────────────────────────────────────────────────────────
# Policies (literal — before /{case_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/policies", response_model=ExitPolicyListResponse)
def list_policies(
    include_inactive: bool = False,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    query = db.query(ExitPolicy).filter(ExitPolicy.is_deleted == False)  # noqa: E712
    if not include_inactive:
        query = query.filter(ExitPolicy.is_active == True)  # noqa: E712
    query = query.order_by(ExitPolicy.grade_id.is_(None).desc(), ExitPolicy.created_at.asc())
    rows, total, total_pages = _paginate(query, page, limit)
    return {"items": [_policy_resp(db, p) for p in rows], "total": total,
            "page": page, "limit": limit, "total_pages": total_pages}


@router.post("/policies", response_model=ExitPolicyResponse, status_code=201)
def create_policy(
    body: ExitPolicyCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    p = ExitPolicy(
        policy_name=body.policy_name, description=body.description,
        grade_id=body.grade_id,
        employee_category=(body.employee_category or None),
        notice_period_days=body.notice_period_days,
        probation_notice_days=body.probation_notice_days,
        buyout_allowed=body.buyout_allowed, buyout_basis=body.buyout_basis,
        approval_levels=[a.model_dump() for a in body.approval_levels],
        clearance_template=[c.model_dump() for c in body.clearance_template],
        interview_questions=[q.model_dump() for q in body.interview_questions],
        gratuity_enabled=body.gratuity_enabled, gratuity_min_years=body.gratuity_min_years,
        is_active=body.is_active, created_by_id=admin.id,
    )
    db.add(p)
    db.flush()
    write_exit_audit(db, entity_type="POLICY", action=ExitAuditAction.POLICY_CREATED,
                     entity_id=p.id, actor_id=admin.id, note=p.policy_name)
    db.commit()
    return _policy_resp(db, p)


@router.patch("/policies/{policy_id:uuid}", response_model=ExitPolicyResponse)
def update_policy(
    policy_id: UUID,
    body: ExitPolicyUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    p = db.query(ExitPolicy).filter(ExitPolicy.id == policy_id, ExitPolicy.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Policy not found")
    data = body.model_dump(exclude_unset=True)
    for k in ("approval_levels", "clearance_template", "interview_questions"):
        if k in data and data[k] is not None:
            data[k] = [x.model_dump() if hasattr(x, "model_dump") else x for x in data[k]]
    for k, v in data.items():
        setattr(p, k, v)
    db.flush()
    write_exit_audit(db, entity_type="POLICY", action=ExitAuditAction.POLICY_UPDATED,
                     entity_id=p.id, actor_id=admin.id, note=p.policy_name)
    db.commit()
    return _policy_resp(db, p)


@router.delete("/policies/{policy_id:uuid}")
def delete_policy(
    policy_id: UUID,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    p = db.query(ExitPolicy).filter(ExitPolicy.id == policy_id, ExitPolicy.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Policy not found")
    p.is_deleted = True
    p.is_active = False
    note = f"{p.policy_name} — {reason.strip()}" if (reason and reason.strip()) else p.policy_name
    write_exit_audit(db, entity_type="POLICY", action=ExitAuditAction.POLICY_DELETED,
                     entity_id=p.id, actor_id=admin.id, note=note[:300])
    db.commit()
    return {"deleted": True}


# ─────────────────────────────────────────────────────────────────────────────
# Audit logs (literal — before /{case_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/audit-logs", response_model=ExitAuditLogListResponse)
def list_audit_logs(
    entity_type: Optional[str] = None,
    exit_case_id: Optional[UUID] = None,
    action: Optional[ExitAuditAction] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    query = db.query(ExitAuditLog)
    if entity_type:
        query = query.filter(ExitAuditLog.entity_type == entity_type)
    if exit_case_id:
        query = query.filter(ExitAuditLog.exit_case_id == exit_case_id)
    if action:
        query = query.filter(ExitAuditLog.action == action)
    query = query.order_by(ExitAuditLog.created_at.desc())
    rows, total, total_pages = _paginate(query, page, limit)
    items = []
    for r in rows:
        d = ExitAuditLogResponse.model_validate(r).model_dump()
        d["actor_name"] = svc._user_name(db, r.actor_id)
        items.append(d)
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": total_pages}


# ─────────────────────────────────────────────────────────────────────────────
# Letter verification (public-ish, literal — before /{case_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/verify/{code}", response_model=LetterVerifyResponse)
def verify_letter(code: str, db: Session = Depends(get_db)):
    doc = db.query(ExitDocument).filter(ExitDocument.verification_code == code).first()
    if not doc:
        return LetterVerifyResponse(valid=False, message="No document matches this code.")
    case = db.query(ExitCase).options(
        joinedload(ExitCase.employee).joinedload(Employee.user)).filter(ExitCase.id == doc.exit_case_id).first()
    name = svc.employee_label(case.employee)["employee_name"] if case else None
    revoked = doc.status == ExitDocStatus.REVOKED
    return LetterVerifyResponse(
        valid=(doc.status == ExitDocStatus.ISSUED and not revoked),
        employee_name=name, doc_type=doc.doc_type.value if doc.doc_type else None,
        issued_at=doc.issued_at, revoked=revoked,
        message=("Revoked" if revoked else ("Valid & issued" if doc.status == ExitDocStatus.ISSUED else "Not yet issued")),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Former-employee document portal (PUBLIC — no auth).
# Closes the offboarding catch-22: clearance revokes the ERP login, so the leaver
# can no longer reach /user to download the relieving/experience letter that was
# only issued *after* clearance. An unguessable per-case token (minted at
# acceptance, bookmarkable while still logged in) surfaces those letters here with
# NO login. Only ISSUED, non-revoked letters are ever served; unknown tokens get a
# generic 404 (no enumeration / no draft leakage).
# ─────────────────────────────────────────────────────────────────────────────

_LETTER_SLUG = {
    DocTemplateType.RELIEVING_LETTER: "relieving-letter",
    DocTemplateType.EXPERIENCE_LETTER: "experience-letter",
}


def _portal_case(db: Session, token: str) -> ExitCase:
    if not token or len(token) < 16:
        raise HTTPException(404, "Document link not found")
    case = (
        db.query(ExitCase)
        .options(joinedload(ExitCase.documents), joinedload(ExitCase.employee).joinedload(Employee.user))
        .filter(ExitCase.public_token == token, ExitCase.is_deleted == False)  # noqa: E712
        .first()
    )
    if not case:
        raise HTTPException(404, "Document link not found")
    if not svc.portal_token_valid(case):
        # Token was real but its security window has elapsed → it's now dead.
        raise HTTPException(410, "This document link has expired. Please ask HR for a fresh link.")
    return case


@router.get("/portal/{token}")
def public_portal(token: str, db: Session = Depends(get_db)):
    """Public landing payload: the leaver's currently-issued exit letters."""
    case = _portal_case(db, token)
    name = svc.employee_label(case.employee)["employee_name"] if case.employee else None
    letters = []
    for d in (case.documents or []):
        if d.status == ExitDocStatus.ISSUED and d.doc_type in _LETTER_SLUG:
            is_rel = d.doc_type == DocTemplateType.RELIEVING_LETTER
            letters.append({
                "doc_type": d.doc_type.value,
                "slug": _LETTER_SLUG[d.doc_type],
                "title": "Relieving Letter" if is_rel else "Experience Letter",
                "issued_at": d.issued_at,
            })
    letters.sort(key=lambda x: (x["issued_at"] is None, x["issued_at"]))
    return {
        "employee_name": name,
        "case_number": case.case_number,
        "last_working_date": case.last_working_date,
        "expires_at": case.public_token_expires_at,
        "letters": letters,
    }


@router.get("/portal/{token}/download/{doc_type}")
def public_portal_download(token: str, doc_type: str, db: Session = Depends(get_db)):
    """Public, no-auth PDF stream — only for an ISSUED, non-revoked letter."""
    case = _portal_case(db, token)
    dt = _letter_doc_type(doc_type)   # 404 on unknown slug
    doc = next((d for d in (case.documents or []) if d.doc_type == dt), None)
    if not doc or doc.status != ExitDocStatus.ISSUED or not doc.drive_document_id:
        raise HTTPException(404, "Letter not available")
    import os
    from app.models.drive_document import DriveDocument
    from app.utils.hr.exit_documents import letter_disk_path
    dd = db.query(DriveDocument).filter(DriveDocument.id == doc.drive_document_id).first()
    path = letter_disk_path(dd) if dd else None
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Stored file missing")
    with open(path, "rb") as fh:
        data = fh.read()
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{dt.value.lower()}.pdf"'})


@router.post("/{case_id:uuid}/letters/portal/rotate")
def rotate_portal_token(case_id: UUID, db: Session = Depends(get_db),
                        admin: User = Depends(get_current_superuser)):
    """Re-mint the public-portal token + restart the security window (invalidates
    the old link and gives the employee a fresh few-day window)."""
    case = _get_case(db, case_id)
    case.public_token = secrets.token_urlsafe(32)
    case.public_token_expires_at = datetime.now(timezone.utc) + timedelta(days=svc.PORTAL_TOKEN_TTL_DAYS)
    db.commit()
    return {"public_token": case.public_token, "public_token_expires_at": case.public_token_expires_at}


# ─────────────────────────────────────────────────────────────────────────────
# Clearance items (literal path — before /{case_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/clearance-items/{item_id:uuid}", response_model=ClearanceItemResponse)
def update_clearance_item(
    item_id: UUID,
    body: ClearanceItemUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    item = db.query(ExitClearanceItem).filter(ExitClearanceItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Clearance item not found")
    case = _get_case(db, item.exit_case_id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        new_status = data["status"]
        item.status = new_status
        if new_status == ClearanceItemStatus.CLEARED and not item.signed_off_at:
            item.signed_off_at = datetime.now(timezone.utc)
            item.signed_off_by_id = admin.id
        if new_status != ClearanceItemStatus.CLEARED:
            item.signed_off_at = None
            item.signed_off_by_id = None
    if "remarks" in data:
        item.remarks = data["remarks"]
    if "recovery_amount" in data:
        item.recovery_amount = data["recovery_amount"]
    if "assignee_user_id" in data:
        item.assignee_user_id = data["assignee_user_id"]
    db.flush()
    svc.recompute_clearance_progress(db, case)
    write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_ITEM_UPDATED,
                     exit_case_id=case.id, entity_id=item.id, actor_id=admin.id,
                     to_status=item.status.value, note=item.title)
    db.commit()
    db.refresh(item)
    return svc.clearance_item_to_response(db, item)


@router.post("/clearance-items/{item_id:uuid}/reopen", response_model=ClearanceItemResponse)
def reopen_clearance_item(
    item_id: UUID,
    body: ClearanceReopenBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    item = db.query(ExitClearanceItem).filter(ExitClearanceItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Clearance item not found")
    case = _get_case(db, item.exit_case_id)
    item.status = ClearanceItemStatus.PENDING
    item.signed_off_at = None
    item.signed_off_by_id = None
    item.remarks = (item.remarks or "") + f"\n[Reopened] {body.reason}"
    db.flush()
    svc.recompute_clearance_progress(db, case)
    write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_REOPENED,
                     exit_case_id=case.id, entity_id=item.id, actor_id=admin.id, note=body.reason)
    db.commit()
    db.refresh(item)
    return svc.clearance_item_to_response(db, item)


@router.post("/clearance-items/{item_id:uuid}/revoke-erp", response_model=ClearanceItemResponse)
def revoke_erp_login(
    item_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Perform the actual ERP de-provisioning for the IT 'ERP login' gate, then
    sign it off — the corporate IT-offboarding pattern where 'revoke access' DOES
    the work, not just ticks a box. Reuses Account Provisioning semantics:
    disables sign-in on the linked User and flips ERP provisioning rows to
    REVOKED, so every surface stays consistent."""
    item = db.query(ExitClearanceItem).filter(ExitClearanceItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Clearance item not found")
    if item.item_key != "it_erp_login":
        raise HTTPException(400, "ERP revoke applies only to the ERP login clearance item")
    case = _get_case(db, item.exit_case_id)
    emp = case.employee or db.query(Employee).filter(Employee.id == case.employee_id).first()
    user = (db.query(User).filter(User.id == emp.user_id).first()
            if emp and getattr(emp, "user_id", None) else None)
    if not user:
        raise HTTPException(404, "No ERP login is linked to this employee")
    already = not user.is_active
    # Disable sign-in + clear the activation gate (permanent exit). Mirrors the
    # Account Provisioning revoke; re-hire goes through provisioning set-credentials.
    user.is_active = False
    user.is_activated = False
    # Cross-module consistency: flip any ERP provisioning row(s) to REVOKED.
    try:
        from app.models.hr.account_provisioning import (
            AccountProvisioning, AccountProvisioningStatus, AccountType,
        )
        aps = db.query(AccountProvisioning).filter(
            AccountProvisioning.employee_id == emp.id,
            AccountProvisioning.account_type == AccountType.ERP,
        ).all()
        for ap in aps:
            ap.status = AccountProvisioningStatus.REVOKED
            ap.revoked_at = datetime.now(timezone.utc)
    except Exception:
        pass
    # Sign off the gate as a human-attested action.
    item.status = ClearanceItemStatus.CLEARED
    item.signed_off_at = datetime.now(timezone.utc)
    item.signed_off_by_id = admin.id
    note = ("ERP login confirmed revoked — account already disabled" if already
            else "ERP login revoked — sign-in disabled, activation gate cleared, session ended")
    base = (item.remarks or "").strip()
    item.remarks = f"{base}\n[ERP revoked] {note}" if base else f"[ERP revoked] {note}"
    db.flush()
    svc.recompute_clearance_progress(db, case)
    write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_ITEM_UPDATED,
                     exit_case_id=case.id, entity_id=item.id, actor_id=admin.id,
                     to_status="CLEARED", note="ERP login revoked")
    db.commit()
    db.refresh(item)
    return svc.clearance_item_to_response(db, item)


@router.post("/clearance-items/{item_id:uuid}/revoke-provisioning", response_model=ClearanceItemResponse)
def revoke_provisioning(
    item_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """De-provision an IT/Security access gate (email/SSO, VPN, repo, biometric,
    access-card) the same way the ERP gate works: flip the matching Account
    Provisioning row(s) to REVOKED, then sign off the gate. 'Revoke access' DOES
    the work instead of merely ticking a box — and the next clearance read keeps
    every surface in sync via ``sync_clearance_from_systems``."""
    item = db.query(ExitClearanceItem).filter(ExitClearanceItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Clearance item not found")
    gate = svc.PROVISIONING_GATES.get(item.item_key)
    if not gate:
        raise HTTPException(400, "This gate is not backed by an Account Provisioning record")
    atype_val, plabel = gate
    case = _get_case(db, item.exit_case_id)
    emp = case.employee or db.query(Employee).filter(Employee.id == case.employee_id).first()
    if not emp:
        raise HTTPException(404, "No employee is linked to this case")
    from app.models.hr.account_provisioning import (
        AccountProvisioning, AccountProvisioningStatus, AccountType,
    )
    aps = db.query(AccountProvisioning).filter(
        AccountProvisioning.employee_id == emp.id,
        AccountProvisioning.account_type == AccountType(atype_val),
    ).all()
    if not aps:
        raise HTTPException(404, f"No {plabel} account is on record for this employee")
    already = all(ap.status == AccountProvisioningStatus.REVOKED for ap in aps)
    now = datetime.now(timezone.utc)
    for ap in aps:
        if ap.status != AccountProvisioningStatus.REVOKED:
            ap.status = AccountProvisioningStatus.REVOKED
            ap.revoked_at = now
    item.status = ClearanceItemStatus.CLEARED
    item.signed_off_at = now
    item.signed_off_by_id = admin.id
    note = (f"{plabel} confirmed revoked — already de-provisioned" if already
            else f"{plabel} de-provisioned in Account Provisioning")
    base = (item.remarks or "").strip()
    item.remarks = f"{base}\n[Revoked] {note}" if base else f"[Revoked] {note}"
    db.flush()
    svc.recompute_clearance_progress(db, case)
    write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_ITEM_UPDATED,
                     exit_case_id=case.id, entity_id=item.id, actor_id=admin.id,
                     to_status="CLEARED", note=f"{plabel} revoked")
    db.commit()
    db.refresh(item)
    return svc.clearance_item_to_response(db, item)


# ── HR-gate apply actions — the marked tasks DO the work on the relevant records
# (employee record / F&F settlement), then sign off the gate. Same "the action
# does the work, not a checkbox" philosophy as revoke-erp / revoke-provisioning.
# Both are idempotent: a re-seal (Edit) never double-writes — already-applied
# tasks report "already recorded" and timestamps / lifecycle transitions are
# guarded. Effects are echoed so the UI can inform the user exactly what changed.

@router.post("/clearance-items/{item_id:uuid}/apply-hr-records", response_model=ClearanceApplyResponse)
def apply_hr_records(
    item_id: UUID,
    body: HrRecordsApplyBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Finalise the employee's exit records (the HR 'Employee records updated'
    gate). Each marked task performs a real, audited write:
      • HRIS status / Documents archived / Statutory (PF·ESI) → an EmployeeHistory
        audit row on the employee's timeline,
      • Lifecycle → runs the real EXIT transition (state→EXITED on the LWD,
        Employee History + asset off-boarding) via the shared lifecycle bridge.
    Then signs off the gate. Idempotent on re-seal."""
    from app.models.hr.employee_history import EmployeeHistory, EmployeeChangeType

    item = db.query(ExitClearanceItem).filter(ExitClearanceItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Clearance item not found")
    if item.item_key != "hr_records":
        raise HTTPException(400, "This action applies only to the 'Employee records updated' gate")
    if not all([body.hris_status, body.documents_archived, body.statutory_updated, body.lifecycle_exited]):
        raise HTTPException(400, "Complete every records task before sealing this gate")
    case = _get_case(db, item.exit_case_id)
    emp = case.employee or db.query(Employee).filter(Employee.id == case.employee_id).first()
    if not emp:
        raise HTTPException(404, "No employee is linked to this case")

    now = datetime.now(timezone.utc)
    prior = dict(item.submission or {})
    applied_keys = set(prior.get("applied_keys") or [])
    effects: List[dict] = []
    eff_date = case.last_working_date or date.today()

    def _hist(reason: str):
        db.add(EmployeeHistory(
            employee_id=emp.id, change_type=EmployeeChangeType.PROFILE_UPDATED,
            from_value_json=None, to_value_json=None, effective_date=eff_date,
            reason=reason, actioned_by_id=admin.id,
        ))

    # 1) HRIS status
    if "hris_status" in applied_keys:
        effects.append({"key": "hris_status", "label": "HRIS status updated", "done": True,
                        "detail": "Already recorded", "target": "Employee record", "severity": "info"})
    else:
        _hist(f"Exit HRIS status finalised (case {case.case_number})")
        applied_keys.add("hris_status")
        effects.append({"key": "hris_status", "label": "HRIS status updated", "done": True,
                        "detail": "Recorded on the employee timeline", "target": "Employee record", "severity": "success"})

    # 2) Documents archived
    if "documents_archived" in applied_keys:
        effects.append({"key": "documents_archived", "label": "Documents archived", "done": True,
                        "detail": "Already recorded", "target": "Personnel file", "severity": "info"})
    else:
        _hist(f"Personnel documents archived on exit (case {case.case_number})")
        applied_keys.add("documents_archived")
        effects.append({"key": "documents_archived", "label": "Documents archived", "done": True,
                        "detail": "Archive recorded on the employee timeline", "target": "Personnel file", "severity": "success"})

    # 3) Statutory (PF / ESI)
    if "statutory_updated" in applied_keys:
        effects.append({"key": "statutory_updated", "label": "Statutory records updated (PF / ESI)", "done": True,
                        "detail": "Already recorded", "target": "Statutory record", "severity": "info"})
    else:
        _hist(f"Statutory records (PF / ESI) updated on exit (case {case.case_number})")
        applied_keys.add("statutory_updated")
        effects.append({"key": "statutory_updated", "label": "Statutory records updated (PF / ESI)", "done": True,
                        "detail": "PF / ESI marked on the employee timeline", "target": "Statutory record", "severity": "success"})

    # 4) Lifecycle → EXITED on LWD (the real transition; idempotent + state-guarded)
    if emp.lifecycle_state in (LifecycleState.EXITED, LifecycleState.ARCHIVED):
        effects.append({"key": "lifecycle_exited", "label": "Lifecycle set to exited on LWD", "done": True,
                        "detail": f"Already {emp.lifecycle_state.value}", "target": "Lifecycle state", "severity": "info"})
        applied_keys.add("lifecycle_exited")
    elif emp.lifecycle_state in (LifecycleState.ON_NOTICE, LifecycleState.ACTIVE, LifecycleState.SUSPENDED):
        # Sign off + persist FIRST so the lifecycle handler's commit captures it too.
        item.status = ClearanceItemStatus.CLEARED
        item.signed_off_at = now
        item.signed_off_by_id = admin.id
        if body.assignee_user_id is not None:
            item.assignee_user_id = body.assignee_user_id
        applied_keys.add("lifecycle_exited")
        _persist_records_submission(item, prior, applied_keys, body, admin, now, effects)
        _records_remarks(item, body)
        db.flush()
        svc.recompute_clearance_progress(db, case)
        write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_ITEM_UPDATED,
                         exit_case_id=case.id, entity_id=item.id, actor_id=admin.id,
                         to_status="CLEARED", note="Employee records updated")
        # The bridge sets EXITED, writes Employee History, fires asset off-boarding
        # and COMMITS the session (our item + history rows go with it).
        _sync_employee_lifecycle(
            db, case, "exit", admin,
            LifecycleExitBody(exit_date=eff_date,
                              reason=f"Exit records finalised via clearance (case {case.case_number})"),
        )
        write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.EXITED,
                         exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                         to_status=LifecycleState.EXITED.value, note="Lifecycle set to EXITED on LWD")
        db.commit()
        db.refresh(item)
        effects.append({"key": "lifecycle_exited", "label": "Lifecycle set to exited on LWD", "done": True,
                        "detail": f"Employee set to EXITED · effective {eff_date.isoformat()} · asset off-boarding triggered",
                        "target": "Lifecycle state", "severity": "major"})
        return {"item": svc.clearance_item_to_response(db, item), "effects": effects}
    else:
        raise HTTPException(409, f"Cannot set this employee to EXITED from {emp.lifecycle_state.value}")

    # Path with no live lifecycle transition (already EXITED/ARCHIVED): commit here.
    item.status = ClearanceItemStatus.CLEARED
    item.signed_off_at = now
    item.signed_off_by_id = admin.id
    if body.assignee_user_id is not None:
        item.assignee_user_id = body.assignee_user_id
    _persist_records_submission(item, prior, applied_keys, body, admin, now, effects)
    _records_remarks(item, body)
    db.flush()
    svc.recompute_clearance_progress(db, case)
    write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_ITEM_UPDATED,
                     exit_case_id=case.id, entity_id=item.id, actor_id=admin.id,
                     to_status="CLEARED", note="Employee records updated")
    db.commit()
    db.refresh(item)
    return {"item": svc.clearance_item_to_response(db, item), "effects": effects}


def _persist_records_submission(item, prior, applied_keys, body, admin, now, effects):
    item.submission = {
        "kind": "records",
        "checklist": {"hris_status": body.hris_status, "documents_archived": body.documents_archived,
                      "statutory_updated": body.statutory_updated, "lifecycle_exited": body.lifecycle_exited},
        "applied_keys": sorted(applied_keys),
        "applied_at": now.isoformat(),
        "applied_by_id": str(admin.id),
        "applied_by_name": getattr(admin, "full_name", None) or getattr(admin, "name", None) or getattr(admin, "email", None),
        "effects": effects,
    }


def _records_remarks(item, body):
    summary = "[Records] HRIS · Documents archived · PF/ESI · Lifecycle→EXITED"
    free = (body.remarks or "").strip()
    base = (item.remarks or "").strip()
    # strip a prior [Records] line so re-seals don't stack it
    base = "\n".join(l for l in base.splitlines() if not l.startswith("[Records]")).strip()
    parts = [p for p in (summary, free, base) if p]
    item.remarks = "\n".join(parts) or None


@router.post("/clearance-items/{item_id:uuid}/apply-ff-ack", response_model=ClearanceApplyResponse)
def apply_ff_ack(
    item_id: UUID,
    body: FfAckApplyBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Record the Full & Final acknowledgement on the authoritative settlement
    record (the HR 'Full & Final acknowledged' gate), then sign off the gate.
    Requires a settlement to exist — you cannot acknowledge a statement that has
    not been drafted. Idempotent on re-seal."""
    item = db.query(ExitClearanceItem).filter(ExitClearanceItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Clearance item not found")
    if item.item_key != "hr_ff_ack":
        raise HTTPException(400, "This action applies only to the 'Full & Final acknowledged' gate")
    if not all([body.statement_shared, body.employee_acknowledged, body.payout_confirmed]):
        raise HTTPException(400, "Complete every acknowledgement task before sealing this gate")
    case = _get_case(db, item.exit_case_id)
    stl = case.settlement
    if not stl:
        raise HTTPException(409, "No Full & Final settlement has been drafted yet — draft it from the Settlement tab first")

    now = datetime.now(timezone.utc)
    effects: List[dict] = []
    sname = stl.settlement_number
    net = float(stl.net_amount or 0)
    direction = "payable to employee" if net > 0 else ("recoverable from employee" if net < 0 else "balanced")
    money = f"₹{abs(net):,.0f} {direction}"

    # 1) Statement shared
    if stl.ff_statement_shared_at:
        effects.append({"key": "statement_shared", "label": "F&F statement shared", "done": True,
                        "detail": "Already recorded", "target": sname, "severity": "info"})
    else:
        stl.ff_statement_shared_at = now
        effects.append({"key": "statement_shared", "label": "F&F statement shared", "done": True,
                        "detail": f"Recorded on {sname}", "target": sname, "severity": "success"})
    # 2) Employee acknowledged the amounts
    if stl.ff_acknowledged_at:
        effects.append({"key": "employee_acknowledged", "label": "Employee acknowledged the amounts", "done": True,
                        "detail": "Already recorded", "target": sname, "severity": "info"})
    else:
        stl.ff_acknowledged_at = now
        stl.ff_acknowledged_by_id = admin.id
        effects.append({"key": "employee_acknowledged", "label": "Employee acknowledged the amounts", "done": True,
                        "detail": money, "target": sname, "severity": "success"})
    # 3) Payout schedule confirmed
    if stl.payout_confirmed_at:
        effects.append({"key": "payout_confirmed", "label": "Payout schedule confirmed", "done": True,
                        "detail": "Already recorded", "target": sname, "severity": "info"})
    else:
        stl.payout_confirmed_at = now
        effects.append({"key": "payout_confirmed", "label": "Payout schedule confirmed", "done": True,
                        "detail": (stl.settlement_method or "Per payout schedule"), "target": sname, "severity": "success"})

    stl.ff_ack_snapshot = {
        "checklist": {"statement_shared": body.statement_shared,
                      "employee_acknowledged": body.employee_acknowledged,
                      "payout_confirmed": body.payout_confirmed},
        "by_id": str(admin.id),
        "by_name": getattr(admin, "full_name", None) or getattr(admin, "name", None) or getattr(admin, "email", None),
        "at": now.isoformat(),
        "settlement_status": stl.status.value if stl.status else None,
        "net_amount": net,
        "settlement_number": sname,
    }

    item.status = ClearanceItemStatus.CLEARED
    item.signed_off_at = now
    item.signed_off_by_id = admin.id
    if body.assignee_user_id is not None:
        item.assignee_user_id = body.assignee_user_id
    item.submission = {
        "kind": "ff_ack",
        "checklist": stl.ff_ack_snapshot["checklist"],
        "applied_at": now.isoformat(),
        "applied_by_id": str(admin.id),
        "applied_by_name": stl.ff_ack_snapshot["by_name"],
        "settlement_number": sname, "net_amount": net,
        "effects": effects,
    }
    summary = f"[F&F ack] {sname} · {money}"
    free = (body.remarks or "").strip()
    base = "\n".join(l for l in (item.remarks or "").splitlines() if not l.startswith("[F&F ack]")).strip()
    item.remarks = "\n".join(p for p in (summary, free, base) if p) or None

    db.flush()
    svc.recompute_clearance_progress(db, case)
    write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_ITEM_UPDATED,
                     exit_case_id=case.id, entity_id=item.id, actor_id=admin.id,
                     to_status="CLEARED", note=f"Full & Final acknowledged · {sname}")
    db.commit()
    db.refresh(item)
    return {"item": svc.clearance_item_to_response(db, item), "effects": effects}


@router.post("/clearance-items/{item_id:uuid}/apply-fin-loans", response_model=ClearanceApplyResponse)
def apply_fin_loans(
    item_id: UUID,
    body: FinLoansApplyBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Verify the employee's outstanding loans / advances against LIVE data and
    schedule the recovery into the F&F (the Finance 'Loans / advances cleared'
    gate). Travel advances are read live from the same source the settlement
    engine uses (no double count — they auto-recover into ``advance_recovery``);
    ``loan_recovery_amount`` captures any other loan / salary advance and is
    written to ``loan_recovery``. Recomputes a DRAFT settlement so the recovery
    is reflected immediately, records the acknowledgement, then signs off."""
    from app.utils.hr.exit_management.settlement_engine import _travel_advance_recovery

    item = db.query(ExitClearanceItem).filter(ExitClearanceItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Clearance item not found")
    if item.item_key != "fin_loan_advance":
        raise HTTPException(400, "This action applies only to the 'Loans / advances cleared' gate")
    if not all([body.loan_balance_computed, body.advance_balance_computed,
                body.recovery_scheduled, body.employee_acknowledged]):
        raise HTTPException(400, "Complete every loans / advances task before sealing this gate")
    case = _get_case(db, item.exit_case_id)
    emp = case.employee or db.query(Employee).filter(Employee.id == case.employee_id).first()
    if not emp:
        raise HTTPException(404, "No employee is linked to this case")

    now = datetime.now(timezone.utc)
    adv = _travel_advance_recovery(db, emp)          # {"amount": Decimal, "count": int}
    adv_amount = float(adv.get("amount") or 0)
    adv_count = int(adv.get("count") or 0)
    loan_amount = float(body.loan_recovery_amount or 0)
    total_dues = adv_amount + loan_amount

    effects: List[dict] = []
    stl = case.settlement
    scheduled = False
    # Reflect the recovery NOW only while the F&F is still a DRAFT — never mutate
    # a verified/approved/paid settlement (that goes through the Settlement tab).
    if stl is not None and stl.status == SettlementStatus.DRAFT:
        overrides = {"loan_recovery": loan_amount} if loan_amount > 0 else None
        compute_settlement(db, case, stl, overrides=overrides,
                           note=f"Finance gate: loans / advances cleared (case {case.case_number})")
        scheduled = True

    # 1) travel advances (auto-recovered)
    if adv_count > 0:
        effects.append({"key": "advance_balance_computed", "label": "Travel advances scheduled in F&F", "done": True,
                        "detail": f"₹{adv_amount:,.0f} across {adv_count} advance(s) — auto-recovered",
                        "target": "F&F · advance recovery", "severity": "success"})
    else:
        effects.append({"key": "advance_balance_computed", "label": "No travel advance outstanding", "done": True,
                        "detail": "Nothing to recover from advances", "target": "Travel advances", "severity": "info"})
    # 2) manual loan / salary advance
    if loan_amount > 0:
        effects.append({"key": "loan_recovery", "label": "Loan / salary advance recovery scheduled", "done": True,
                        "detail": (f"₹{loan_amount:,.0f}" if scheduled else f"₹{loan_amount:,.0f} — recorded (settlement locked)"),
                        "target": "F&F · loan recovery", "severity": "major" if scheduled else "info"})
    # 3) where it landed
    if scheduled:
        effects.append({"key": "recovery_scheduled", "label": "Recovery scheduled in F&F", "done": True,
                        "detail": f"F&F recomputed · {stl.settlement_number}", "target": stl.settlement_number, "severity": "success"})
    elif stl is None:
        effects.append({"key": "recovery_scheduled", "label": "Recovery scheduled in F&F", "done": True,
                        "detail": "Applies when the F&F is computed", "target": "F&F settlement", "severity": "info"})
    else:
        effects.append({"key": "recovery_scheduled", "label": "Recovery — settlement locked", "done": True,
                        "detail": f"F&F is {stl.status.value} — adjust via the Settlement tab", "target": stl.settlement_number, "severity": "info"})
    # 4) acknowledgement
    effects.append({"key": "employee_acknowledged", "label": "Employee acknowledged the dues", "done": True,
                    "detail": f"₹{total_dues:,.0f} total dues", "target": "Acknowledgement", "severity": "success"})

    item.submission = {
        "kind": "fin_loans",
        "checklist": {"loan_balance_computed": body.loan_balance_computed,
                      "advance_balance_computed": body.advance_balance_computed,
                      "recovery_scheduled": body.recovery_scheduled,
                      "employee_acknowledged": body.employee_acknowledged},
        "advances": {"amount": adv_amount, "count": adv_count},
        "loan_recovery": loan_amount, "total_dues": total_dues, "scheduled": scheduled,
        "applied_at": now.isoformat(), "applied_by_id": str(admin.id),
        "applied_by_name": getattr(admin, "full_name", None) or getattr(admin, "name", None) or getattr(admin, "email", None),
        "effects": effects,
    }
    item.status = ClearanceItemStatus.CLEARED
    item.signed_off_at = now
    item.signed_off_by_id = admin.id
    if body.assignee_user_id is not None:
        item.assignee_user_id = body.assignee_user_id
    summary = f"[Loans] advances ₹{adv_amount:,.0f} ({adv_count}) · loan ₹{loan_amount:,.0f}"
    free = (body.remarks or "").strip()
    base = "\n".join(l for l in (item.remarks or "").splitlines() if not l.startswith("[Loans]")).strip()
    item.remarks = "\n".join(p for p in (summary, free, base) if p) or None

    db.flush()
    svc.recompute_clearance_progress(db, case)
    write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_ITEM_UPDATED,
                     exit_case_id=case.id, entity_id=item.id, actor_id=admin.id,
                     to_status="CLEARED", note="Loans / advances cleared")
    db.commit()
    db.refresh(item)
    return {"item": svc.clearance_item_to_response(db, item), "effects": effects}


# ─────────────────────────────────────────────────────────────────────────────
# Case detail + workflow (catch-all pinned to :uuid)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{case_id:uuid}", response_model=ExitCaseDetailResponse)
def get_case(case_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    # Lazily backfill the portal token for cases accepted before this feature shipped.
    if not case.public_token:
        svc.ensure_public_token(db, case)
        db.commit()
    return _detail(db, case)


@router.patch("/{case_id:uuid}", response_model=ExitCaseDetailResponse)
def update_case(case_id: UUID, body: ExitCaseUpdate, db: Session = Depends(get_db),
                admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if case.status not in (ExitCaseStatus.DRAFT, ExitCaseStatus.SUBMITTED, ExitCaseStatus.MANAGER_REVIEW):
        raise HTTPException(409, f"Cannot edit a case in {case.status.value}")
    data = body.model_dump(exclude_unset=True)
    if data.get("policy_id"):
        pol = db.query(ExitPolicy).filter(
            ExitPolicy.id == data["policy_id"], ExitPolicy.is_active == True,  # noqa: E712
            ExitPolicy.is_deleted == False).first()  # noqa: E712
        if not pol:
            raise HTTPException(400, "Selected exit policy not found or inactive")
    for k, v in data.items():
        setattr(case, k, v)
    # keep notice days consistent with a changed policy / type (pre-accept transparency)
    if "policy_id" in data and case.policy_id:
        pol = pol if data.get("policy_id") else db.query(ExitPolicy).filter(ExitPolicy.id == case.policy_id).first()
        if pol:
            if case.resignation_type in (ResignationType.TERMINATION, ResignationType.MUTUAL_SEPARATION):
                case.notice_period_days = 0
                case.notice_waived = True
            elif case.resignation_type == ResignationType.PROBATION_EXIT:
                case.notice_period_days = pol.probation_notice_days
            else:
                case.notice_period_days = pol.notice_period_days
    case.last_updated_by_id = admin.id
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.UPDATED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id)
    db.commit()
    return _detail(db, _get_case(db, case_id))


# Statuses a case may be expunged from: anything that has NOT yet been accepted
# (no clearance / settlement / lifecycle artefacts exist yet) plus terminal/closed
# states. ACCEPTED onward is in-flight and must be cancelled, never deleted.
DELETABLE_STATUSES = (
    ExitCaseStatus.DRAFT, ExitCaseStatus.SUBMITTED, ExitCaseStatus.MANAGER_REVIEW,
    ExitCaseStatus.REJECTED, ExitCaseStatus.WITHDRAWN, ExitCaseStatus.CANCELLED,
)


@router.delete("/{case_id:uuid}")
def delete_case(case_id: UUID, body: DeleteCaseBody = DeleteCaseBody(),
                db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if case.status not in DELETABLE_STATUSES:
        raise HTTPException(
            409, "Only not-yet-accepted (draft / submitted / under manager review) "
                 "or closed (rejected / withdrawn / cancelled) cases can be deleted. "
                 "Cancel an in-progress case instead.")
    reason = (body.reason or "").strip() or None
    case.is_deleted = True
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.DELETED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     from_status=case.status.value, note=reason)
    db.commit()
    return {"deleted": True}


@router.post("/{case_id:uuid}/submit", response_model=ExitCaseDetailResponse)
def submit_case(case_id: UUID, body: ExitSubmitBody, db: Session = Depends(get_db),
                admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if case.status != ExitCaseStatus.DRAFT:
        raise HTTPException(409, f"Cannot submit from {case.status.value}")
    if body.reason_detail:
        case.reason_detail = body.reason_detail
    frm = case.status.value
    case.status = ExitCaseStatus.MANAGER_REVIEW if case.manager_id else ExitCaseStatus.SUBMITTED
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.SUBMITTED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     from_status=frm, to_status=case.status.value)
    db.commit()
    return _detail(db, _get_case(db, case_id))


@router.post("/{case_id:uuid}/manager-decision", response_model=ExitCaseDetailResponse)
def manager_decision(case_id: UUID, body: ManagerDecisionBody, db: Session = Depends(get_db),
                     admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if case.status != ExitCaseStatus.MANAGER_REVIEW:
        raise HTTPException(409, f"Case is not in manager review ({case.status.value})")
    case.manager_decision = body.decision
    case.manager_notes = body.notes
    case.manager_decided_at = datetime.now(timezone.utc)
    frm = case.status.value
    if body.decision == "REJECTED":
        case.status = ExitCaseStatus.REJECTED
        case.rejection_reason = body.notes or "Rejected by manager"
    else:
        case.status = ExitCaseStatus.SUBMITTED   # back to HR for acceptance
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.MANAGER_DECISION,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     from_status=frm, to_status=case.status.value, note=body.decision)
    db.commit()
    return _detail(db, _get_case(db, case_id))


@router.post("/{case_id:uuid}/accept", response_model=ExitCaseDetailResponse)
def accept_case(case_id: UUID, body: AcceptBody, db: Session = Depends(get_db),
                admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if case.status not in (ExitCaseStatus.SUBMITTED, ExitCaseStatus.MANAGER_REVIEW):
        raise HTTPException(409, f"Cannot accept from {case.status.value}")
    if case.manager_decision == "REJECTED":
        raise HTTPException(409, "Manager has rejected this resignation")
    emp = case.employee
    # Respect a policy HR already chose at create/edit; otherwise resolve by grade.
    policy = None
    if case.policy_id:
        policy = db.query(ExitPolicy).filter(
            ExitPolicy.id == case.policy_id, ExitPolicy.is_deleted == False).first()  # noqa: E712
    if policy is None:
        policy = svc.resolve_policy(db, emp)
        case.policy_id = policy.id if policy else None

    # Resolve notice + LWD.
    if body.notice_period_days is not None:
        case.notice_period_days = body.notice_period_days
    else:
        case.notice_period_days = svc.resolved_notice_days(case, policy)
    if body.last_working_date:
        case.last_working_date = body.last_working_date
    elif case.requested_last_working_date:
        case.last_working_date = case.requested_last_working_date

    # Termination / mutual separation = no notice.
    if body.notice_waived is not None:
        case.notice_waived = body.notice_waived
    elif case.resignation_type in (ResignationType.TERMINATION, ResignationType.MUTUAL_SEPARATION):
        case.notice_waived = True
    if body.eligible_for_rehire is not None:
        case.eligible_for_rehire = body.eligible_for_rehire

    case.accepted_by_id = admin.id
    case.accepted_at = datetime.now(timezone.utc)
    frm = case.status.value
    case.status = ExitCaseStatus.ACCEPTED
    db.flush()

    bootstrap_exit(db, case, admin.id)
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.ACCEPTED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     from_status=frm, to_status=case.status.value)
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.CLEARANCE_SEEDED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id)
    db.commit()
    return _detail(db, _get_case(db, case_id))


@router.post("/{case_id:uuid}/reject", response_model=ExitCaseDetailResponse)
def reject_case(case_id: UUID, body: RejectBody, db: Session = Depends(get_db),
                admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if case.status not in (ExitCaseStatus.SUBMITTED, ExitCaseStatus.MANAGER_REVIEW, ExitCaseStatus.DRAFT):
        raise HTTPException(409, f"Cannot reject from {case.status.value}")
    frm = case.status.value
    case.status = ExitCaseStatus.REJECTED
    case.rejection_reason = body.reason
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.REJECTED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     from_status=frm, to_status=case.status.value, note=body.reason)
    db.commit()
    return _detail(db, _get_case(db, case_id))


@router.post("/{case_id:uuid}/cancel", response_model=ExitCaseDetailResponse)
def cancel_case(case_id: UUID, body: CancelBody, db: Session = Depends(get_db),
                admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if case.status in (ExitCaseStatus.COMPLETED, ExitCaseStatus.REJECTED,
                       ExitCaseStatus.WITHDRAWN, ExitCaseStatus.CANCELLED):
        raise HTTPException(409, f"Cannot cancel a {case.status.value} case")
    # Revert employee lifecycle if notice already started.
    if case.employee and case.employee.lifecycle_state == LifecycleState.ON_NOTICE:
        _sync_employee_lifecycle(db, case, "cancel-notice", admin,
                                 _BaseLifecycle(reason=body.reason))
        db.refresh(case)
    frm = case.status.value
    case.status = ExitCaseStatus.CANCELLED
    case.cancel_reason = body.reason
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.CANCELLED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     from_status=frm, to_status=case.status.value, note=body.reason)
    db.commit()
    return _detail(db, _get_case(db, case_id))


# ── Notice ──

@router.post("/{case_id:uuid}/start-notice", response_model=ExitCaseDetailResponse)
def start_notice(case_id: UUID, body: StartNoticeBody, db: Session = Depends(get_db),
                 admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if case.status != ExitCaseStatus.ACCEPTED:
        raise HTTPException(409, f"Notice can only start from ACCEPTED ({case.status.value})")
    # Drive the existing lifecycle (Employee → ON_NOTICE + history). Raises 409 on bad state.
    _sync_employee_lifecycle(
        db, case, "give-notice", admin,
        LifecycleGiveNoticeBody(
            notice_period_start_date=body.notice_period_start_date,
            last_working_date=body.last_working_date,
            reason=f"Exit case {case.case_number}",
        ),
    )
    db.refresh(case)
    case.notice_period_start_date = body.notice_period_start_date
    case.last_working_date = body.last_working_date
    frm = case.status.value
    case.status = ExitCaseStatus.NOTICE_PERIOD
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.NOTICE_STARTED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     from_status=frm, to_status=case.status.value)
    db.commit()
    return _detail(db, _get_case(db, case_id))


@router.post("/{case_id:uuid}/waive-notice", response_model=ExitCaseDetailResponse)
def waive_notice(case_id: UUID, body: WaiveNoticeBody, db: Session = Depends(get_db),
                 admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    case.notice_waived = True
    if body.buyout_days is not None:
        case.notice_buyout_days = body.buyout_days
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.NOTICE_WAIVED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id, note=body.reason)
    db.commit()
    return _detail(db, _get_case(db, case_id))


@router.post("/{case_id:uuid}/adjust-notice", response_model=ExitCaseDetailResponse)
def adjust_notice(case_id: UUID, body: NoticeAdjustBody, db: Session = Depends(get_db),
                  admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if body.last_working_date:
        case.last_working_date = body.last_working_date
    if body.notice_period_days is not None:
        case.notice_period_days = body.notice_period_days
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.NOTICE_ADJUSTED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id, note=body.reason)
    db.commit()
    return _detail(db, _get_case(db, case_id))


@router.get("/{case_id:uuid}/notice-serving")
def notice_serving(case_id: UUID, db: Session = Depends(get_db),
                   admin: User = Depends(get_current_superuser)):
    """Read-only serving telemetry for one case: notice progress anchored on the
    real start date + attendance during notice + leave cover + projected F&F
    impact. Powers the notice page's "is the leaver actually serving?" panel."""
    case = _get_case(db, case_id)
    return notice_serving_snapshot(db, case)


# ── Clearance ──

@router.get("/{case_id:uuid}/clearance")
def get_clearance(case_id: UUID, db: Session = Depends(get_db),
                  admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    # Self-heal: surface any template items added after this case was seeded
    # (e.g. the IT "ERP login / credentials revoked" row). Idempotent.
    added = svc.backfill_clearance_items(db, case)
    if added:
        db.flush()
        db.expire(case, ["clearance_items"])
    # Cross-module auto-sync: reflect obligations already satisfied elsewhere
    # (assets returned, ERP login revoked, claims closed, interview done) and
    # auto-clear the provably-done ones. Returns live signals for the UI.
    signals, auto_events = svc.sync_clearance_from_systems(db, case)
    if added or auto_events:
        for ev in auto_events:
            write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_ITEM_UPDATED,
                             exit_case_id=case.id, entity_id=ev["id"], actor_id=None,
                             to_status="CLEARED", note=ev["note"])
        db.commit()
        db.refresh(case)
    items = sorted(case.clearance_items or [], key=lambda x: x.sort_order)
    groups: dict = {}
    for it in items:
        dept = it.department.value
        groups.setdefault(dept, {"department": dept, "items": [], "cleared": 0, "total": 0})
        resp = svc.clearance_item_to_response(db, it)
        resp["system_signal"] = signals.get(it.item_key)
        groups[dept]["items"].append(resp)
        groups[dept]["total"] += 1
        if it.status in (ClearanceItemStatus.CLEARED, ClearanceItemStatus.NA):
            groups[dept]["cleared"] += 1
    for g in groups.values():
        g["progress"] = round(g["cleared"] * 100 / g["total"]) if g["total"] else 0
    return {"case_id": str(case.id), "progress_pct": case.clearance_progress_pct or 0,
            "groups": list(groups.values()),
            "all_mandatory_cleared": svc.all_mandatory_cleared(db, case)}


@router.post("/{case_id:uuid}/clearance/complete", response_model=ExitCaseDetailResponse)
def complete_clearance(case_id: UUID, db: Session = Depends(get_db),
                       admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if not svc.all_mandatory_cleared(db, case):
        raise HTTPException(409, "All mandatory clearance items must be CLEARED or N/A first")
    if case.status not in (ExitCaseStatus.NOTICE_PERIOD, ExitCaseStatus.CLEARANCE, ExitCaseStatus.ACCEPTED):
        raise HTTPException(409, f"Cannot complete clearance from {case.status.value}")
    # Push clearance recoveries into the settlement figures.
    if case.settlement and case.settlement.status == SettlementStatus.DRAFT:
        compute_settlement(db, case, case.settlement)
    frm = case.status.value
    case.status = ExitCaseStatus.SETTLEMENT
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.CLEARANCE_COMPLETED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     from_status=frm, to_status=case.status.value)
    db.commit()
    return _detail(db, _get_case(db, case_id))


# ── Interview ──

@router.get("/{case_id:uuid}/interview", response_model=ExitInterviewResponse)
def get_interview(case_id: UUID, db: Session = Depends(get_db),
                  admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if not case.interview:
        raise HTTPException(404, "No interview for this case")
    return _interview_resp(db, case)


@router.post("/{case_id:uuid}/interview/schedule", response_model=ExitInterviewResponse)
def schedule_interview(case_id: UUID, body: InterviewScheduleBody, db: Session = Depends(get_db),
                       admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    iv = case.interview
    if not iv:
        iv = ExitInterview(exit_case_id=case.id, status=InterviewStatus.PENDING, responses=[], ratings={})
        db.add(iv)
        db.flush()
    if iv.status == InterviewStatus.COMPLETED:
        raise HTTPException(409, "This interview is already completed — edit it from Conduct instead.")
    frm = iv.status.value
    iv.scheduled_at = body.scheduled_at
    iv.mode = body.mode or iv.mode or "FORM"
    if body.conducted_by_id is not None:
        iv.conducted_by_id = body.conducted_by_id
    if body.details is not None:
        iv.details = body.details.strip() or None
    # Scheduling/inviting is the act that makes the interview live for the employee:
    # PENDING → SCHEDULED. This is the only path that opens the self-service survey
    # (FORM) or surfaces the appointment (IN_PERSON / VIDEO).
    iv.status = InterviewStatus.SCHEDULED
    write_exit_audit(db, entity_type="INTERVIEW", action=ExitAuditAction.INTERVIEW_SCHEDULED,
                     exit_case_id=case.id, entity_id=iv.id, actor_id=admin.id,
                     from_status=frm, to_status=iv.status.value)
    db.commit()
    db.refresh(case)
    return _interview_resp(db, case)


@router.post("/{case_id:uuid}/interview/submit", response_model=ExitInterviewResponse)
def submit_interview(case_id: UUID, body: InterviewSubmitBody, db: Session = Depends(get_db),
                     admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    iv = case.interview
    if not iv:
        iv = ExitInterview(exit_case_id=case.id, responses=[], ratings={})
        db.add(iv)
        db.flush()
    iv.responses = body.responses or []
    iv.ratings = body.ratings or {}
    iv.would_recommend = body.would_recommend
    iv.primary_reason_category = body.primary_reason_category
    iv.feedback_summary = body.feedback_summary
    if body.mode:
        iv.mode = body.mode
    iv.status = InterviewStatus.COMPLETED
    iv.conducted_at = datetime.now(timezone.utc)
    iv.conducted_by_id = admin.id
    write_exit_audit(db, entity_type="INTERVIEW", action=ExitAuditAction.INTERVIEW_COMPLETED,
                     exit_case_id=case.id, entity_id=iv.id, actor_id=admin.id)
    db.commit()
    db.refresh(case)
    return _interview_resp(db, case)


# ── Asset return ──

@router.get("/{case_id:uuid}/assets")
def get_exit_assets(case_id: UUID, db: Session = Depends(get_db),
                    admin: User = Depends(get_current_superuser)):
    """Asset recovery view for an exiting employee.

    The ``AssetAllocation`` row is the SOURCE OF TRUTH for whether an asset has
    physically come back (status RETURNED / LOST / DAMAGED), because the Asset
    module's ``/allocations/{id}/return`` flow closes the *allocation* without
    necessarily completing the offboarding ``EMPLOYEE_TO_STORE`` transfer. We
    therefore build a unified ``items`` list keyed on the allocation lifecycle and
    overlay the return-transfer status for assets still in the recovery pipeline.

    ``allocations`` (still-held only) and ``transfers`` are kept for back-compat.
    """
    case = _get_case(db, case_id)
    out = {"allocations": [], "transfers": [], "items": []}
    try:
        from datetime import date, datetime
        from app.models.hr.asset import AssetAllocation, AllocationStatus
        from app.models.hr.asset_lifecycle import AssetTransfer, AssetTransferType

        def _as_date(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v.date()
            if isinstance(v, date):
                return v
            try:
                return datetime.fromisoformat(str(v)).date()
            except Exception:
                return None

        def _aname(asset):
            """Asset has no ``name`` column — compose one from brand/model, fall back
            to ``asset_code``; the tag column is ``tag`` (not ``asset_tag``)."""
            if not asset:
                return (None, None, None)
            nm = " ".join(x for x in [getattr(asset, "brand", None), getattr(asset, "model", None)] if x).strip()
            nm = nm or getattr(asset, "asset_code", None)
            tag = getattr(asset, "tag", None) or getattr(asset, "asset_code", None)
            typ = asset.asset_type.value if getattr(asset, "asset_type", None) else None
            return (nm, tag, typ)

        # Returns that belong to THIS separation: on/after the separation began.
        window = (_as_date(case.resignation_date) or _as_date(case.notice_period_start_date)
                  or _as_date(case.created_at) or date.min)

        # Most-recent return-to-store transfer per asset (the recovery task).
        transfers = (
            db.query(AssetTransfer).options(joinedload(AssetTransfer.asset))
            .filter(AssetTransfer.from_employee_id == case.employee_id,
                    AssetTransfer.transfer_type == AssetTransferType.EMPLOYEE_TO_STORE,
                    AssetTransfer.is_deleted == False).all()  # noqa: E712
        )
        tmap = {}
        for t in transfers:
            k = str(t.asset_id)
            prev = tmap.get(k)
            if prev is None or (getattr(t, "created_at", None) and getattr(prev, "created_at", None)
                                and t.created_at > prev.created_at):
                tmap[k] = t

        allocs = (
            db.query(AssetAllocation).options(joinedload(AssetAllocation.asset))
            .filter(AssetAllocation.employee_id == case.employee_id).all()
        )

        seen = set()

        def _item(asset_id, asset, alloc, transfer):
            nm, tag, typ = _aname(asset)
            return {
                "asset_id": str(asset_id),
                "asset_name": nm,
                "asset_tag": tag,
                "asset_type": typ,
                "allocation_id": str(alloc.id) if alloc else None,
                "allocation_status": (alloc.status.value if alloc and alloc.status else None),
                "returned_date": (alloc.returned_date.isoformat() if alloc and alloc.returned_date else None),
                "condition_on_return": (alloc.condition_on_return.value
                                        if alloc and getattr(alloc, "condition_on_return", None) else None),
                "return_requested": bool(getattr(alloc, "return_requested", False)) if alloc else False,
                "transfer_id": str(transfer.id) if transfer else None,
                "transfer_status": (transfer.status.value if transfer and transfer.status else None),
            }

        for a in allocs:
            k = str(a.asset_id)
            t = tmap.get(k)
            if a.status == AllocationStatus.ALLOCATED:
                out["items"].append(_item(a.asset_id, a.asset, a, t))
                seen.add(k)
                _n, _t, _ty = _aname(a.asset)
                out["allocations"].append({
                    "allocation_id": str(a.id), "asset_id": k,
                    "asset_name": _n, "asset_tag": _t, "asset_type": _ty,
                })
            elif a.status in (AllocationStatus.RETURNED, AllocationStatus.LOST, AllocationStatus.DAMAGED):
                rd = _as_date(a.returned_date)
                if (t is not None) or (rd is not None and rd >= window):
                    out["items"].append(_item(a.asset_id, a.asset, a, t))
                    seen.add(k)

        # A return transfer with no matching allocation row (rare) — surface it too.
        for t in transfers:
            k = str(t.asset_id)
            _n, _t, _ty = _aname(t.asset)
            out["transfers"].append({
                "transfer_id": str(t.id), "asset_id": k,
                "asset_name": _n, "asset_tag": _t,
                "status": t.status.value if t.status else None,
            })
            if k not in seen:
                out["items"].append(_item(t.asset_id, t.asset, None, t))
                seen.add(k)

        out["recovered"] = sum(1 for it in out["items"] if it["allocation_status"] == "RETURNED")
        out["shortfall"] = sum(1 for it in out["items"] if it["allocation_status"] in ("LOST", "DAMAGED"))
        out["pending_returns"] = sum(
            1 for it in out["items"]
            if it["allocation_status"] in (None, "ALLOCATED")
            and it["allocation_status"] != "RETURNED"
        )
    except Exception:
        import traceback
        traceback.print_exc()
    out["case_id"] = str(case.id)
    return out


@router.post("/{case_id:uuid}/assets/flag-returns")
def flag_asset_returns(case_id: UUID, db: Session = Depends(get_db),
                       admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    created = 0
    try:
        from app.utils.hr.assets.offboarding import flag_open_allocations_on_exit
        created = flag_open_allocations_on_exit(db, case.employee_id, admin.id)
    except Exception:
        import traceback
        traceback.print_exc()
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.ASSET_RETURN_FLAGGED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     note=f"{created} return tasks created")
    db.commit()
    return {"created": created}


# ── Settlement ──

@router.get("/{case_id:uuid}/settlement", response_model=ExitSettlementResponse)
def get_settlement(case_id: UUID, db: Session = Depends(get_db),
                   admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if not case.settlement:
        raise HTTPException(404, "No settlement for this case")
    return ExitSettlementResponse.model_validate(case.settlement)


@router.get("/{case_id:uuid}/settlement/preflight")
def settlement_preflight_view(case_id: UUID, db: Session = Depends(get_db),
                              admin: User = Depends(get_current_superuser)):
    """Live pre-verification gate state (clearance / assets / interview) so the
    UI can show what's blocking before VERIFY is allowed."""
    case = _get_case(db, case_id)
    return svc.settlement_preflight(db, case)


def _ensure_settlement(db: Session, case: ExitCase) -> ExitSettlement:
    if case.settlement:
        return case.settlement
    s = ExitSettlement(
        settlement_number=svc.generate_settlement_number(db),
        exit_case_id=case.id, employee_id=case.employee_id,
    )
    db.add(s)
    db.flush()
    return s


@router.post("/{case_id:uuid}/settlement/recalculate", response_model=ExitSettlementResponse)
def recalculate_settlement(case_id: UUID, body: SettlementRecalcBody, db: Session = Depends(get_db),
                           admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    s = _ensure_settlement(db, case)
    if s.status not in (SettlementStatus.DRAFT, SettlementStatus.VERIFIED, SettlementStatus.REVERSED):
        raise HTTPException(409, f"Cannot recalculate a {s.status.value} settlement")
    compute_settlement(db, case, s, overrides=body.overrides, note=body.reason)
    write_exit_audit(db, entity_type="SETTLEMENT", action=ExitAuditAction.SETTLEMENT_RECALCULATED,
                     exit_case_id=case.id, entity_id=s.id, actor_id=admin.id, note=body.reason)
    db.commit()
    db.refresh(s)
    return ExitSettlementResponse.model_validate(s)


@router.patch("/{case_id:uuid}/settlement", response_model=ExitSettlementResponse)
def update_settlement(case_id: UUID, body: SettlementUpdate, db: Session = Depends(get_db),
                      admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    s = _ensure_settlement(db, case)
    if s.status != SettlementStatus.DRAFT:
        raise HTTPException(409, "Only a DRAFT settlement can be edited")
    overrides = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    compute_settlement(db, case, s, overrides=overrides)
    db.commit()
    db.refresh(s)
    return ExitSettlementResponse.model_validate(s)


@router.post("/{case_id:uuid}/settlement/verify", response_model=ExitSettlementResponse)
def verify_settlement(case_id: UUID, body: SettlementVerifyBody, db: Session = Depends(get_db),
                      admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    s = case.settlement
    if not s:
        raise HTTPException(404, "No settlement")
    if s.status not in (SettlementStatus.DRAFT, SettlementStatus.REVERSED):
        raise HTTPException(409, f"Cannot verify from {s.status.value}")
    # Hard gate — F&F cannot be verified until clearance is 100% complete, all
    # company assets are returned, and the exit interview is concluded.
    pre = svc.settlement_preflight(db, case)
    if not pre["ready"]:
        raise HTTPException(409, "Settlement can't be verified yet — pending: " + "; ".join(pre["blockers"]) + ".")
    s.status = SettlementStatus.VERIFIED
    s.verified_by_id = admin.id
    s.verified_at = datetime.now(timezone.utc)
    s.verify_notes = body.notes
    write_exit_audit(db, entity_type="SETTLEMENT", action=ExitAuditAction.SETTLEMENT_VERIFIED,
                     exit_case_id=case.id, entity_id=s.id, actor_id=admin.id)
    db.commit()
    db.refresh(s)
    return ExitSettlementResponse.model_validate(s)


@router.post("/{case_id:uuid}/settlement/approve", response_model=ExitSettlementResponse)
def approve_settlement(case_id: UUID, body: SettlementApproveBody, db: Session = Depends(get_db),
                       admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    s = case.settlement
    if not s:
        raise HTTPException(404, "No settlement")
    if s.status != SettlementStatus.VERIFIED:
        raise HTTPException(409, f"Cannot approve from {s.status.value}")
    if not svc.all_mandatory_cleared(db, case):
        raise HTTPException(409, "Clearance must be complete before approving F&F")
    s.status = SettlementStatus.APPROVED
    s.approved_by_id = admin.id
    s.approved_at = datetime.now(timezone.utc)
    write_exit_audit(db, entity_type="SETTLEMENT", action=ExitAuditAction.SETTLEMENT_APPROVED,
                     exit_case_id=case.id, entity_id=s.id, actor_id=admin.id,
                     note=(body.notes or None))
    db.commit()
    db.refresh(s)
    return ExitSettlementResponse.model_validate(s)


@router.post("/{case_id:uuid}/settlement/pay", response_model=ExitSettlementResponse)
def pay_settlement(case_id: UUID, body: SettlementPayBody, db: Session = Depends(get_db),
                   admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    # Lock the settlement row for the idempotency check.
    s = db.query(ExitSettlement).filter(
        ExitSettlement.exit_case_id == case.id).with_for_update().first()
    if not s:
        raise HTTPException(404, "No settlement")
    if s.status != SettlementStatus.APPROVED:
        raise HTTPException(409, f"Cannot pay from {s.status.value}")
    if s.earning_adjustment_id or s.deduction_adjustment_id:
        raise HTTPException(409, "Settlement already posted to payroll")
    # Notice-period gate — the Full & Final settles only AFTER the notice period is
    # served (last working day reached), unless the employee is already separated
    # or their notice was formally waived/bought out. Blocks disbursing the F&F to
    # someone still mid-notice. Authoritative server-side check (mirrored in the UI).
    ns = is_notice_served(case)
    if not ns["served"]:
        raise HTTPException(409, ns["reason"] or "Notice period must be served before the F&F can be disbursed.")

    lwd = case.last_working_date or case.exit_date or date.today()
    pm = body.period_month or lwd.month
    py = body.period_year or lwd.year
    method = body.settlement_method or "PAYROLL"

    if method == "PAYROLL":
        if Decimal(str(s.total_earnings or 0)) > 0:
            earn = payroll_post.post_adjustment(
                db, employee_id=case.employee_id, sub_type=f"FNF_SETTLEMENT:{case.case_number}",
                title="Full & Final Settlement", amount=Decimal(str(s.total_earnings)),
                is_deduction=False, is_taxable=True, period_month=pm, period_year=py,
                actor=admin, reason=f"F&F {s.settlement_number}",
            )
            s.earning_adjustment_id = earn.id
        if Decimal(str(s.total_recoveries or 0)) > 0:
            ded = payroll_post.post_adjustment(
                db, employee_id=case.employee_id, sub_type=f"FNF_RECOVERY:{case.case_number}",
                title="F&F Recoveries", amount=Decimal(str(s.total_recoveries)),
                is_deduction=True, is_taxable=False, period_month=pm, period_year=py,
                actor=admin, reason=f"F&F recoveries {s.settlement_number}",
            )
            s.deduction_adjustment_id = ded.id
        s.payroll_ref = f"FNF-{case.case_number}"

    s.status = SettlementStatus.PAID
    s.settlement_method = method
    s.paid_by_id = admin.id
    s.paid_at = datetime.now(timezone.utc)
    s.period_month = pm
    s.period_year = py
    write_exit_audit(db, entity_type="SETTLEMENT", action=ExitAuditAction.SETTLEMENT_PAID,
                     exit_case_id=case.id, entity_id=s.id, actor_id=admin.id,
                     note=f"method={method} net={s.net_amount}")
    db.commit()
    db.refresh(s)
    return ExitSettlementResponse.model_validate(s)


@router.post("/{case_id:uuid}/settlement/reverse", response_model=ExitSettlementResponse)
def reverse_settlement(case_id: UUID, body: SettlementReverseBody, db: Session = Depends(get_db),
                       admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    s = case.settlement
    if not s or s.status != SettlementStatus.PAID:
        raise HTTPException(409, "Only a PAID settlement can be reversed")
    payroll_post.cancel_or_reverse(db, s.earning_adjustment_id, employee_id=case.employee_id,
                                   reversal_sub_type=f"FNF_SETTLEMENT_REVERSAL:{case.case_number}",
                                   title="F&F settlement reversal", actor=admin, reason=body.reason)
    payroll_post.cancel_or_reverse(db, s.deduction_adjustment_id, employee_id=case.employee_id,
                                   reversal_sub_type=f"FNF_RECOVERY_REVERSAL:{case.case_number}",
                                   title="F&F recovery reversal", actor=admin, reason=body.reason)
    # Drop the payroll-posting links so the settlement can be re-verified,
    # re-approved and re-paid cleanly after reversal (the compensating entries
    # above already unwound the original postings). Without this the pay
    # idempotency guard would dead-end a REVERSED settlement.
    s.earning_adjustment_id = None
    s.deduction_adjustment_id = None
    s.payroll_ref = None
    s.status = SettlementStatus.REVERSED
    s.reversed_at = datetime.now(timezone.utc)
    s.reversal_reason = body.reason
    write_exit_audit(db, entity_type="SETTLEMENT", action=ExitAuditAction.SETTLEMENT_REVERSED,
                     exit_case_id=case.id, entity_id=s.id, actor_id=admin.id, note=body.reason)
    db.commit()
    db.refresh(s)
    return ExitSettlementResponse.model_validate(s)


@router.post("/{case_id:uuid}/settlement/close", response_model=ExitSettlementResponse)
def close_settlement(case_id: UUID, body: Optional[SettlementCloseBody] = None,
                     db: Session = Depends(get_db),
                     admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    s = case.settlement
    if not s or s.status != SettlementStatus.PAID:
        raise HTTPException(409, "Only a PAID settlement can be closed")
    s.status = SettlementStatus.CLOSED
    s.closed_at = datetime.now(timezone.utc)
    # Closure is a clean PAID → CLOSED transition; the optional category +
    # remarks are folded into the audit note so the final account is reasoned.
    cat = (body.category or "").strip() if body else ""
    notes = (body.notes or "").strip() if body else ""
    audit_note = ((f"[{cat}] " if cat else "") + notes).strip() or None
    write_exit_audit(db, entity_type="SETTLEMENT", action=ExitAuditAction.SETTLEMENT_CLOSED,
                     exit_case_id=case.id, entity_id=s.id, actor_id=admin.id, note=audit_note)
    db.commit()
    db.refresh(s)
    return ExitSettlementResponse.model_validate(s)


@router.get("/{case_id:uuid}/settlement/payment-advice")
def settlement_payment_advice(case_id: UUID, fmt: str = Query("pdf", pattern="^(pdf|csv)$"),
                              db: Session = Depends(get_db),
                              admin: User = Depends(get_current_superuser)):
    """Finance-facing Full & Final payment advice — the single-payee analog of the
    payroll bank file. Carries the beneficiary's bank account + IFSC and the
    earnings/recoveries breakdown so a BANK_TRANSFER / CASH F&F can actually be
    executed (the disburse flow otherwise produces no payable document).

    Available once the settlement is APPROVED (so it can be prepared for finance
    ahead of the actual disbursement) and afterwards while PAID / CLOSED.
    """
    case = _get_case(db, case_id)
    s = case.settlement
    if not s:
        raise HTTPException(404, "No settlement for this case")
    if s.status not in (SettlementStatus.APPROVED, SettlementStatus.PAID, SettlementStatus.CLOSED):
        raise HTTPException(409, "Payment advice is available once the settlement is approved")

    if fmt == "csv":
        return Response(content=_padvice.payment_advice_csv(case, s), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="ff-advice-{s.settlement_number}.csv"'})
    try:
        data = _padvice.render_payment_advice_pdf(case, s)
    except OSError as e:
        if any(k in str(e) for k in ("libgobject", "libpango", "cannot load library")):
            raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run `python vendor/setup_gtk.py`")
        raise
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="ff-advice-{s.settlement_number}.pdf"'})


# ── Finalize exit + archive (lifecycle orchestration) ──

@router.post("/{case_id:uuid}/finalize-exit", response_model=ExitCaseDetailResponse)
def finalize_exit(case_id: UUID, body: FinalizeExitBody, db: Session = Depends(get_db),
                  admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    s = case.settlement
    if not s or s.status not in (SettlementStatus.PAID, SettlementStatus.CLOSED):
        raise HTTPException(409, "Settlement must be PAID/CLOSED before finalizing exit")
    if not svc.all_mandatory_cleared(db, case):
        raise HTTPException(409, "Clearance must be complete before finalizing exit")
    if case.status in (ExitCaseStatus.COMPLETED,):
        raise HTTPException(409, "Case already completed")
    rehire = body.eligible_for_rehire if body.eligible_for_rehire is not None else case.eligible_for_rehire
    _sync_employee_lifecycle(
        db, case, "exit", admin,
        LifecycleExitBody(exit_date=body.exit_date, eligible_for_rehire=rehire,
                          reason=f"Exit case {case.case_number}"),
    )
    db.refresh(case)
    case.exit_date = body.exit_date
    case.eligible_for_rehire = rehire
    frm = case.status.value
    case.status = ExitCaseStatus.COMPLETED
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.EXITED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     from_status=frm, to_status=case.status.value)
    db.commit()
    return _detail(db, _get_case(db, case_id))


@router.post("/{case_id:uuid}/archive", response_model=ExitCaseDetailResponse)
def archive_case(case_id: UUID, body: Optional[ArchiveCaseBody] = None,
                 db: Session = Depends(get_db),
                 admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    if case.status != ExitCaseStatus.COMPLETED:
        raise HTTPException(409, "Only a COMPLETED case can be archived")
    s = case.settlement
    if s and s.status not in (SettlementStatus.PAID, SettlementStatus.CLOSED):
        raise HTTPException(409, "Settlement must be PAID/CLOSED before archiving")
    # Archive is a clean transition; the optional category + remarks are folded
    # into the employee-history reason and the exit audit note so the archival
    # carries a reasoned sign-off (mirrors the F&F close ceremony).
    cat = (body.category or "").strip() if body else ""
    notes = (body.notes or "").strip() if body else ""
    detail_note = ((f"[{cat}] " if cat else "") + notes).strip()
    reason = detail_note or f"Exit case {case.case_number}"
    _sync_employee_lifecycle(db, case, "archive", admin,
                             LifecycleArchiveBody(reason=reason))
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.ARCHIVED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=admin.id,
                     to_status="ARCHIVED", note=detail_note or None)
    db.commit()
    return _detail(db, _get_case(db, case_id))


# ── Letters (rendering wired in Phase 4 via exit_documents package) ──

def _letter_doc_type(slug: str) -> DocTemplateType:
    if slug == "experience-letter":
        return DocTemplateType.EXPERIENCE_LETTER
    if slug == "relieving-letter":
        return DocTemplateType.RELIEVING_LETTER
    raise HTTPException(404, "Unknown letter type")


@router.post("/{case_id:uuid}/letters/{doc_type}/generate", response_model=ExitDocumentResponse)
def generate_letter(case_id: UUID, doc_type: str, body: LetterGenerateBody,
                    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    dt = _letter_doc_type(doc_type)
    # Gating.
    if dt == DocTemplateType.RELIEVING_LETTER:
        if not svc.all_mandatory_cleared(db, case):
            raise HTTPException(409, "Relieving letter needs clearance complete")
        if not (case.settlement and case.settlement.status in (SettlementStatus.PAID, SettlementStatus.CLOSED)):
            raise HTTPException(409, "Relieving letter needs F&F settlement PAID/CLOSED")
    elif dt == DocTemplateType.EXPERIENCE_LETTER:
        if case.employee and case.employee.lifecycle_state not in (LifecycleState.EXITED, LifecycleState.ARCHIVED):
            raise HTTPException(409, "Experience letter can be generated only after the employee has EXITED")
    try:
        from app.utils.hr.exit_documents import render_letter
    except Exception:
        raise HTTPException(503, "Letter generation is not available yet (Phase 4).")
    doc = render_letter(db, case, dt, template_id=body.template_id, actor=admin)
    write_exit_audit(db, entity_type="DOCUMENT", action=ExitAuditAction.LETTER_GENERATED,
                     exit_case_id=case.id, entity_id=doc.id, actor_id=admin.id, note=dt.value)
    db.commit()
    db.refresh(doc)
    return ExitDocumentResponse.model_validate(doc)


@router.post("/{case_id:uuid}/letters/{doc_type}/issue", response_model=ExitDocumentResponse)
def issue_letter(case_id: UUID, doc_type: str, db: Session = Depends(get_db),
                 admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    dt = _letter_doc_type(doc_type)
    doc = next((d for d in case.documents if d.doc_type == dt), None)
    if not doc or doc.status != ExitDocStatus.GENERATED:
        raise HTTPException(409, "Letter must be generated before issuing")
    doc.status = ExitDocStatus.ISSUED
    doc.issued_by_id = admin.id
    doc.issued_at = datetime.now(timezone.utc)
    # Now (and only now) mirror the credential into the employee's vault.
    try:
        from app.utils.hr.exit_documents import publish_letter
        publish_letter(db, case, doc, admin)
    except Exception:
        pass
    # Open / refresh the portal security window — the link is live for a few days
    # from this delivery moment (re-issuing another letter extends it to cover both).
    svc.start_portal_window(db, case)
    write_exit_audit(db, entity_type="DOCUMENT", action=ExitAuditAction.LETTER_ISSUED,
                     exit_case_id=case.id, entity_id=doc.id, actor_id=admin.id, note=dt.value)
    db.commit()
    db.refresh(doc)
    return ExitDocumentResponse.model_validate(doc)


@router.post("/{case_id:uuid}/letters/{doc_type}/revoke", response_model=ExitDocumentResponse)
def revoke_letter(case_id: UUID, doc_type: str, body: LetterRevokeBody, db: Session = Depends(get_db),
                  admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    dt = _letter_doc_type(doc_type)
    doc = next((d for d in case.documents if d.doc_type == dt), None)
    if not doc or doc.status not in (ExitDocStatus.GENERATED, ExitDocStatus.ISSUED):
        raise HTTPException(409, "No active letter to revoke")
    doc.status = ExitDocStatus.REVOKED
    doc.revoked_at = datetime.now(timezone.utc)
    doc.revoke_reason = body.reason
    doc.verification_code = None   # invalidate the QR
    # Withdraw the mirror from the employee's vault.
    try:
        from app.utils.hr.exit_documents import withdraw_letter
        withdraw_letter(db, doc, admin)
    except Exception:
        pass
    write_exit_audit(db, entity_type="DOCUMENT", action=ExitAuditAction.LETTER_REVOKED,
                     exit_case_id=case.id, entity_id=doc.id, actor_id=admin.id, note=body.reason)
    db.commit()
    db.refresh(doc)
    return ExitDocumentResponse.model_validate(doc)


@router.get("/{case_id:uuid}/letters/{doc_type}/download")
def download_letter(case_id: UUID, doc_type: str, db: Session = Depends(get_db),
                    admin: User = Depends(get_current_superuser)):
    case = _get_case(db, case_id)
    dt = _letter_doc_type(doc_type)
    doc = next((d for d in case.documents if d.doc_type == dt), None)
    if not doc or doc.status == ExitDocStatus.NOT_GENERATED or not doc.drive_document_id:
        raise HTTPException(404, "Letter not generated yet")
    from app.models.drive_document import DriveDocument
    from app.utils.hr.exit_documents import letter_disk_path
    dd = db.query(DriveDocument).filter(DriveDocument.id == doc.drive_document_id).first()
    if not dd:
        raise HTTPException(404, "Stored file missing")
    import os
    path = letter_disk_path(dd)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Stored file missing on disk")
    with open(path, "rb") as fh:
        data = fh.read()
    fname = f"{dt.value.lower()}_{case.case_number}.pdf"
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{fname}"'})
