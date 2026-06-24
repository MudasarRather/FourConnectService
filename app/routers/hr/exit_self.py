"""HR Exit Management — employee self-service + manager surface (prefix /hr/me/exit).

Employees submit/withdraw their own resignation, track progress, complete the
exit interview, and download issued letters. Managers act on their own team's
cases. All reads use ``try_self_employee``; writes use ``resolve_self_employee``.
"""
from __future__ import annotations

from datetime import datetime, timezone, date, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_user
from app.models.hr.employee import Employee, LifecycleState
from app.models.hr.exit_case import ExitCase
from app.models.hr.exit_clearance import ExitClearanceItem
from app.models.hr.exit_document import ExitDocument
from app.models.hr.exit_type import (
    ExitCaseStatus, ExitDocStatus, InterviewStatus, ExitAuditAction,
    ClearanceItemStatus,
)
from app.models.hr.employee_document import DocTemplateType
from app.schemas.hr.exit_management import (
    MyResignationCreate, ExitCaseUpdate, WithdrawBody, ManagerDecisionBody,
    InterviewSubmitBody, ExitInterviewResponse,
    HandoverSubmitBody, ClearanceSignoffBody, ClearanceItemResponse,
)
from app.utils.hr.exit_management import service as svc
from app.utils.hr.exit_management.audit import write_exit_audit
from app.utils.hr.exit_management.notice_serving import (
    notice_start_moment, notice_metrics, _attendance_during_notice,
    notice_serving_window_end,
)

router = APIRouter(prefix="/hr/me/exit", tags=["HR — My Exit"])

# Window in which work/knowledge/client handover can be filed + signed off: the
# exit is accepted (clearance lanes seeded) but not yet settled / relieved.
HANDOVER_WINDOW = (
    ExitCaseStatus.ACCEPTED, ExitCaseStatus.NOTICE_PERIOD, ExitCaseStatus.CLEARANCE,
)


def _handover_item(case: ExitCase, item_id: UUID) -> ExitClearanceItem:
    """Resolve a clearance item on this case + assert it is a handover lane."""
    item = next((i for i in (case.clearance_items or []) if i.id == item_id), None)
    if not item:
        raise HTTPException(404, "Clearance item not found on this case")
    if not svc.is_self_handover(item):
        raise HTTPException(400, "This clearance lane is not an employee handover.")
    return item


def _load_case(db: Session, case_id: UUID) -> ExitCase:
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


def _self_detail(db: Session, case: ExitCase) -> dict:
    """Employee-facing case payload (interview hides raw responses if confidential)."""
    base = svc.case_to_response(db, case)
    # Most lanes are read-only to the employee. The MANAGER + PROJECT "handover"
    # lanes are employee-submitted → manager-signed-off, so for those we expose the
    # item id, the prior submission, and sign-off attribution so self-service can
    # render the handover form + status without a second round-trip.
    clr = []
    for i in sorted(case.clearance_items or [], key=lambda x: x.sort_order):
        handover = svc.is_self_handover(i)
        row = {"id": str(i.id), "item_key": i.item_key, "department": i.department.value,
               "title": i.title, "description": i.description, "status": i.status.value,
               "is_mandatory": i.is_mandatory, "is_self_handover": handover}
        if handover:
            row["submission"] = i.submission
            row["signed_off_by_name"] = svc._user_name(db, i.signed_off_by_id)
            row["signed_off_at"] = i.signed_off_at
        clr.append(row)
    base["clearance_items"] = clr
    s = case.settlement
    base["settlement"] = None if not s else {
        "status": s.status.value, "total_earnings": float(s.total_earnings or 0),
        "total_recoveries": float(s.total_recoveries or 0), "net_amount": float(s.net_amount or 0),
    }
    iv = case.interview
    iv_pending = bool(iv) and iv.status == InterviewStatus.PENDING
    base["interview"] = None if not iv else {
        "status": iv.status.value, "scheduled_at": iv.scheduled_at,
        "conducted_at": iv.conducted_at,
        "mode": iv.mode, "details": iv.details,
        "conducted_by_name": svc._user_name(db, iv.conducted_by_id),
        # The slot exists but HR hasn't scheduled/invited yet — nothing is actionable.
        "awaiting_schedule": iv_pending,
        # IN_PERSON / VIDEO are HR-led — the employee attends, they don't fill the form.
        # And a PENDING slot is never self-completable until HR schedules it.
        "self_complete": (not iv_pending) and (iv.mode or "FORM") == "FORM",
        "questions": (case.policy.interview_questions if case.policy and case.policy.interview_questions else svc.DEFAULT_INTERVIEW_QUESTIONS),
    }
    base["documents"] = [
        {"doc_type": d.doc_type.value, "status": d.status.value, "id": str(d.id),
         "issued_at": d.issued_at}
        for d in (case.documents or [])
    ]
    # When notice actually began (date + time + working-hours check).
    base["notice_start"] = notice_start_moment(db, case)
    # Authoritative notice progress, anchored on the REAL notice start → last
    # working date, so self-service shows the same served/remaining/progress the
    # admin notice board does (no parallel client-side date math = no drift).
    base["notice_metrics"] = notice_metrics(case, case.policy)
    # Light attendance reality during the served window so the employee can see
    # whether they are actually serving their notice. Unrecorded / absent days are
    # settled as loss-of-pay in the Full & Final — they do NOT change the last
    # working day (the countdown is calendar-based by policy).
    serving = None
    try:
        start = case.notice_period_start_date
        if start and case.employee:
            # Count notice days that are genuinely DONE. A day is done when its
            # shift has ended — today included once the employee's shift-end time
            # has passed (e.g. they never clocked in and the working day is over).
            # Until then today is in-progress and excluded, so the "not recorded"
            # count never inflates by a day the employee may yet serve. Shared with
            # the admin snapshot so both views agree.
            end = notice_serving_window_end(db, case.employee, case.last_working_date, date.today())
            if end >= start:
                serving = _attendance_during_notice(db, case.employee, start, end)
    except Exception:
        serving = None
    base["notice_serving"] = serving
    return base


@router.get("")
def my_exit(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = svc.try_self_employee(db, user)
    if not emp:
        return {"unlinked": True, "case": None}
    case = (
        db.query(ExitCase)
        .options(joinedload(ExitCase.settlement), joinedload(ExitCase.interview),
                 joinedload(ExitCase.clearance_items), joinedload(ExitCase.documents),
                 joinedload(ExitCase.policy),
                 joinedload(ExitCase.employee).joinedload(Employee.user),
                 joinedload(ExitCase.employee).joinedload(Employee.designation),
                 joinedload(ExitCase.department))
        .filter(ExitCase.employee_id == emp.id, ExitCase.is_deleted == False)  # noqa: E712
        .order_by(ExitCase.created_at.desc())
        .first()
    )
    # Lazily backfill the document-portal token so the employee can bookmark their
    # permanent link even on a case accepted before this feature shipped.
    if case and not case.public_token:
        svc.ensure_public_token(db, case)
        db.commit()
    return {"unlinked": False, "employee_id": str(emp.id),
            "lifecycle_state": emp.lifecycle_state.value if emp.lifecycle_state else None,
            "case": _self_detail(db, case) if case else None}


@router.post("/resign")
def resign(body: MyResignationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = svc.resolve_self_employee(db, user)
    if emp.lifecycle_state not in (LifecycleState.ACTIVE, LifecycleState.ON_PROBATION):
        raise HTTPException(409, f"You cannot resign while {emp.lifecycle_state.value}")
    existing = svc.open_case_for_employee(db, emp.id)
    if existing:
        raise HTTPException(409, "You already have an open resignation")
    case = ExitCase(
        case_number=svc.generate_case_number(db),
        employee_id=emp.id,
        resignation_type=body.resignation_type,
        reason_category=body.reason_category,
        reason_detail=body.reason_detail,
        status=ExitCaseStatus.MANAGER_REVIEW if emp.reporting_manager_id else ExitCaseStatus.SUBMITTED,
        initiated_by="EMPLOYEE",
        resignation_date=date.today(),
        requested_last_working_date=body.requested_last_working_date,
        manager_id=emp.reporting_manager_id,
        department_id=emp.department_id,
        designation_id=emp.designation_id,
        grade_id=emp.grade_id,
        employee_category=emp.employee_category,
        joining_date_snapshot=emp.joining_date,
        personal_email=(body.personal_email or "").strip() or None,
        created_by_id=user.id,
    )
    db.add(case)
    db.flush()
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.SUBMITTED,
                     exit_case_id=case.id, entity_id=case.id, actor_id=user.id,
                     to_status=case.status.value, note="Employee-initiated resignation")
    db.commit()
    return _self_detail(db, _load_case(db, case.id))


@router.patch("/{case_id:uuid}")
def edit_my_draft(case_id: UUID, body: ExitCaseUpdate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    emp = svc.resolve_self_employee(db, user)
    case = _load_case(db, case_id)
    if case.employee_id != emp.id:
        raise HTTPException(403, "Not your case")
    if case.status not in (ExitCaseStatus.DRAFT, ExitCaseStatus.SUBMITTED, ExitCaseStatus.MANAGER_REVIEW):
        raise HTTPException(409, "Can no longer edit this resignation")
    # Employees can only touch reason + requested LWD + personal email.
    for k in ("reason_category", "reason_detail", "requested_last_working_date", "personal_email"):
        v = getattr(body, k, None)
        if v is not None:
            setattr(case, k, v)
    db.commit()
    return _self_detail(db, _load_case(db, case_id))


@router.post("/{case_id:uuid}/withdraw")
def withdraw(case_id: UUID, body: WithdrawBody, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    emp = svc.resolve_self_employee(db, user)
    case = _load_case(db, case_id)
    if case.employee_id != emp.id:
        raise HTTPException(403, "Not your case")
    if case.status not in (ExitCaseStatus.DRAFT, ExitCaseStatus.SUBMITTED, ExitCaseStatus.MANAGER_REVIEW):
        raise HTTPException(409, "Resignation can no longer be withdrawn (already in process)")
    frm = case.status.value
    case.status = ExitCaseStatus.WITHDRAWN
    case.withdraw_reason = body.reason
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.WITHDRAWN,
                     exit_case_id=case.id, entity_id=case.id, actor_id=user.id,
                     from_status=frm, to_status=case.status.value, note=body.reason)
    db.commit()
    return _self_detail(db, _load_case(db, case_id))


@router.post("/{case_id:uuid}/interview/submit", response_model=ExitInterviewResponse)
def submit_my_interview(case_id: UUID, body: InterviewSubmitBody, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    emp = svc.resolve_self_employee(db, user)
    case = _load_case(db, case_id)
    if case.employee_id != emp.id:
        raise HTTPException(403, "Not your case")
    iv = case.interview
    if not iv:
        from app.models.hr.exit_interview import ExitInterview
        iv = ExitInterview(exit_case_id=case.id, responses=[], ratings={})
        db.add(iv)
        db.flush()
    # Workflow guards: the slot must have been scheduled by HR first; an HR-led
    # session (in-person / video) is recorded by the interviewer, not self-submitted;
    # and a completed interview can't be overwritten.
    if iv.status == InterviewStatus.COMPLETED:
        raise HTTPException(409, "Your exit interview has already been submitted.")
    if iv.status == InterviewStatus.PENDING:
        raise HTTPException(409, "Your exit interview hasn't been scheduled yet — HR will "
                                 "invite you shortly. There's nothing to complete right now.")
    if (iv.mode or "FORM") in ("IN_PERSON", "VIDEO"):
        raise HTTPException(409, "This exit interview is conducted by HR — your interviewer "
                                 "will record it during the session. There's no form to fill.")
    iv.responses = body.responses or []
    iv.ratings = body.ratings or {}
    iv.would_recommend = body.would_recommend
    iv.primary_reason_category = body.primary_reason_category
    iv.feedback_summary = body.feedback_summary
    iv.status = InterviewStatus.COMPLETED
    iv.conducted_at = datetime.now(timezone.utc)
    write_exit_audit(db, entity_type="INTERVIEW", action=ExitAuditAction.INTERVIEW_COMPLETED,
                     exit_case_id=case.id, entity_id=iv.id, actor_id=user.id, note="Self-submitted")
    db.commit()
    db.refresh(iv)
    data = ExitInterviewResponse.model_validate(iv).model_dump()
    data["questions"] = (case.policy.interview_questions if case.policy and case.policy.interview_questions else svc.DEFAULT_INTERVIEW_QUESTIONS)
    return data


@router.post("/{case_id:uuid}/clearance/{item_id:uuid}/handover", response_model=ClearanceItemResponse)
def submit_handover(case_id: UUID, item_id: UUID, body: HandoverSubmitBody,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Employee files (or re-files) their handover for a MANAGER / PROJECT lane.

    Moves the lane to IN_PROGRESS and routes sign-off to the reporting manager.
    Re-submitting a lane the manager sent back (BLOCKED) reopens it for review.
    """
    emp = svc.resolve_self_employee(db, user)
    case = _load_case(db, case_id)
    if case.employee_id != emp.id:
        raise HTTPException(403, "Not your case")
    if case.status not in HANDOVER_WINDOW:
        raise HTTPException(409, "Handover can be submitted only once your exit is accepted "
                                 "and before it is settled.")
    item = _handover_item(case, item_id)
    if item.status == ClearanceItemStatus.CLEARED:
        raise HTTPException(409, "This handover has already been cleared by your manager.")

    now = datetime.now(timezone.utc)
    history = list((item.submission or {}).get("history") or [])
    history.append({"event": "submitted", "at": now.isoformat(), "by": str(user.id),
                    "by_name": svc._user_name(db, user.id), "note": None})
    # Reassign a fresh dict (JSONB here is not Mutable-wrapped — in-place edits
    # would not be flushed).
    item.submission = {
        "notes": body.notes,
        "successor_name": body.successor_name,
        "checklist": body.checklist or {},
        "attachments": [a.model_dump() for a in (body.attachments or [])],
        "submitted_at": now.isoformat(),
        "submitted_by_id": str(user.id),
        "history": history,
    }
    item.status = ClearanceItemStatus.IN_PROGRESS
    if case.manager_id:
        item.assignee_user_id = case.manager_id
    db.flush()
    svc.recompute_clearance_progress(db, case)
    write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_ITEM_UPDATED,
                     exit_case_id=case.id, entity_id=item.id, actor_id=user.id,
                     to_status=item.status.value, note=f"Handover submitted: {item.title}")
    db.commit()
    db.refresh(item)
    return svc.clearance_item_to_response(db, item)


@router.get("/{case_id:uuid}/letters/{doc_type}/download")
def download_my_letter(case_id: UUID, doc_type: str, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    emp = svc.resolve_self_employee(db, user)
    case = _load_case(db, case_id)
    if case.employee_id != emp.id:
        raise HTTPException(403, "Not your case")
    dt = DocTemplateType.EXPERIENCE_LETTER if doc_type == "experience-letter" else (
        DocTemplateType.RELIEVING_LETTER if doc_type == "relieving-letter" else None)
    if dt is None:
        raise HTTPException(404, "Unknown letter type")
    doc = next((d for d in case.documents if d.doc_type == dt), None)
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
                    headers={"Content-Disposition": f'inline; filename="{dt.value.lower()}.pdf"'})


# ─── Manager surface ───

@router.get("/team/cases")
def my_team_cases(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cases = (
        db.query(ExitCase)
        .options(joinedload(ExitCase.employee).joinedload(Employee.user),
                 joinedload(ExitCase.department))
        .filter(ExitCase.manager_id == user.id, ExitCase.is_deleted == False)  # noqa: E712
        .order_by(ExitCase.created_at.desc())
        .all()
    )
    return {"items": [svc.case_to_response(db, c) for c in cases], "total": len(cases)}


@router.post("/{case_id:uuid}/manager-decision")
def team_manager_decision(case_id: UUID, body: ManagerDecisionBody, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    case = _load_case(db, case_id)
    if case.manager_id != user.id:
        raise HTTPException(403, "You are not the reporting manager for this case")
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
        case.status = ExitCaseStatus.SUBMITTED
    write_exit_audit(db, entity_type="CASE", action=ExitAuditAction.MANAGER_DECISION,
                     exit_case_id=case.id, entity_id=case.id, actor_id=user.id,
                     from_status=frm, to_status=case.status.value, note=body.decision)
    db.commit()
    return svc.case_to_response(db, _load_case(db, case_id))


@router.get("/team/{case_id:uuid}/clearance")
def team_case_clearance(case_id: UUID, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Reporting manager: the handover (MANAGER + PROJECT) lanes for one team case.

    The /team/cases list doesn't carry clearance items, so this is the manager's
    read surface for sign-off. Managers can't reach the superuser admin endpoints.
    """
    case = _load_case(db, case_id)
    if case.manager_id != user.id:
        raise HTTPException(403, "You are not the reporting manager for this case")
    items = sorted(case.clearance_items or [], key=lambda x: x.sort_order)
    handover = [svc.clearance_item_to_response(db, i) for i in items if svc.is_self_handover(i)]
    return {"case_id": str(case.id), "progress_pct": case.clearance_progress_pct or 0,
            "in_window": case.status in HANDOVER_WINDOW, "items": handover}


@router.post("/team/{case_id:uuid}/clearance/{item_id:uuid}/signoff", response_model=ClearanceItemResponse)
def team_signoff_clearance(case_id: UUID, item_id: UUID, body: ClearanceSignoffBody,
                           db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Reporting manager signs off a handover lane (CLEARED) or sends it back (BLOCKED).

    HR retains the override via the superuser clearance endpoints — this surface is
    additive, not a replacement.
    """
    case = _load_case(db, case_id)
    if case.manager_id != user.id:
        raise HTTPException(403, "You are not the reporting manager for this case")
    if case.status not in HANDOVER_WINDOW:
        raise HTTPException(409, "This case is not in a state where handover can be signed off.")
    item = _handover_item(case, item_id)

    now = datetime.now(timezone.utc)
    sub = dict(item.submission or {})
    history = list(sub.get("history") or [])
    frm = item.status.value
    if body.decision == "CLEARED":
        item.status = ClearanceItemStatus.CLEARED
        item.signed_off_by_id = user.id
        item.signed_off_at = now
        event = "signed_off"
    else:  # BLOCKED → sent back to the employee for rework
        item.status = ClearanceItemStatus.BLOCKED
        item.signed_off_by_id = None
        item.signed_off_at = None
        event = "sent_back"
    if body.note:
        # Mirror the note into remarks so HR's Gatehouse shows it inline too.
        verb = "Cleared" if body.decision == "CLEARED" else "Sent back"
        tag = f"[{verb} by {svc._user_name(db, user.id)}] {body.note}"
        item.remarks = (f"{item.remarks.strip()}\n{tag}"
                        if item.remarks and item.remarks.strip() else tag)
    history.append({"event": event, "at": now.isoformat(), "by": str(user.id),
                    "by_name": svc._user_name(db, user.id), "note": body.note})
    sub["history"] = history
    item.submission = sub  # fresh dict identity → flushed
    db.flush()
    svc.recompute_clearance_progress(db, case)
    write_exit_audit(db, entity_type="CLEARANCE", action=ExitAuditAction.CLEARANCE_ITEM_UPDATED,
                     exit_case_id=case.id, entity_id=item.id, actor_id=user.id,
                     from_status=frm, to_status=item.status.value,
                     note=f"Manager {body.decision.lower()}: {item.title}")
    db.commit()
    db.refresh(item)
    return svc.clearance_item_to_response(db, item)
