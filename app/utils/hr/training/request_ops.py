"""HR Training & Development — training-request orchestration.

Shared submit / decide / fulfill logic so the admin (HR) router and the
self-service (employee + manager) router never duplicate the workflow. Mirrors
the reimbursements manager-decide pattern; callers must hold a row lock when
deciding (``with_for_update``) to avoid double-advance.
"""
from __future__ import annotations

from datetime import datetime, timezone, date, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.training import TrainingAssignment, TrainingAssignmentStatus
from app.models.hr.training_request import (
    TrainingRequest, TrainingRequestStatus, TrainingRequestDecision,
)
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit
from app.utils.hr.training.chain import (
    build_request_steps, auto_skip_unresolvable, can_act_on_step,
    assert_request_transition, mirror_request_final_columns,
)

_S = TrainingRequestStatus


def _notify(db: Session, user_id, *, type_: str, title: str, message: str, url: str):
    if not user_id:
        return
    try:
        from app.models.notification import Notification
        db.add(Notification(user_id=user_id, type=type_, title=title,
                            message=message, action_url=url, is_read=False))
    except Exception:
        import traceback
        traceback.print_exc()


def _active_step(req: TrainingRequest) -> Optional[dict]:
    steps = req.approval_steps or []
    if 0 <= req.current_step < len(steps):
        return steps[req.current_step]
    return None


def submit_request(db: Session, req: TrainingRequest, employee: Employee, actor: User) -> None:
    if req.status not in (_S.DRAFT, _S.RETURNED):
        raise HTTPException(409, f"Cannot submit a request in {req.status.value}")
    steps = build_request_steps(employee)
    req.current_step = auto_skip_unresolvable(steps, 0)
    req.approval_steps = steps
    flag_modified(req, "approval_steps")
    assert_request_transition(req.status, _S.PENDING_APPROVAL)
    # If every stage auto-skipped, fall through to fully APPROVED (HR gate always
    # present in the default chain, so this only happens if chain is customised away).
    if req.current_step >= len(steps):
        req.status = _S.APPROVED
        req.approved_at = datetime.now(timezone.utc)
    else:
        req.status = _S.PENDING_APPROVAL
    req.submitted_at = datetime.now(timezone.utc)
    req.submitted_by_id = actor.id
    write_training_audit(db, entity_type="REQUEST", entity_id=req.id,
                         action=TrainingAuditAction.SUBMIT, actor_id=actor.id,
                         to_status=req.status.value)
    nxt = _active_step(req)
    _notify(db, getattr(employee, "user_id", None), type_="training_request_submitted",
            title="Training request submitted", message=f"{req.request_number} submitted",
            url="/user/self-service/training")
    if nxt and nxt.get("approver_user_id"):
        try:
            from uuid import UUID
            _notify(db, UUID(nxt["approver_user_id"]), type_="training_request_pending",
                    title="Training request awaiting you",
                    message=f"{req.request_number} needs your decision",
                    url="/user/self-service/team-approvals")
        except Exception:
            pass


def decide_request(db: Session, req: TrainingRequest, user: User, decision: str,
                   notes: Optional[str]) -> None:
    if req.status not in (_S.PENDING_APPROVAL,):
        raise HTTPException(409, f"Request is not awaiting approval (status {req.status.value})")
    step = _active_step(req)
    if not step:
        raise HTTPException(409, "No active approval stage")
    if not can_act_on_step(user, step):
        raise HTTPException(403, "You are not the approver for this stage")

    now_iso = datetime.now(timezone.utc).isoformat()
    decision = (decision or "").upper()
    step["decided_by_id"] = str(user.id)
    step["decided_at"] = now_iso
    step["notes"] = notes

    if decision == "APPROVE":
        step["decision"] = TrainingRequestDecision.APPROVED.value
        nxt = auto_skip_unresolvable(req.approval_steps, req.current_step + 1)
        req.current_step = nxt
        if nxt >= len(req.approval_steps):
            assert_request_transition(req.status, _S.APPROVED)
            req.status = _S.APPROVED
            req.approved_at = datetime.now(timezone.utc)
            mirror_request_final_columns(req)
            _notify(db, _emp_user_id(db, req), type_="training_request_approved",
                    title="Training request approved",
                    message=f"{req.request_number} approved — awaiting fulfilment",
                    url="/user/self-service/training")
        else:
            following = req.approval_steps[nxt]
            if following.get("approver_user_id"):
                try:
                    from uuid import UUID
                    _notify(db, UUID(following["approver_user_id"]),
                            type_="training_request_pending",
                            title="Training request awaiting you",
                            message=f"{req.request_number} needs your decision",
                            url="/user/self-service/team-approvals")
                except Exception:
                    pass
        write_training_audit(db, entity_type="REQUEST", entity_id=req.id,
                             action=TrainingAuditAction.APPROVE, actor_id=user.id,
                             to_status=req.status.value, note=notes)
    elif decision == "REJECT":
        step["decision"] = TrainingRequestDecision.REJECTED.value
        assert_request_transition(req.status, _S.REJECTED)
        req.status = _S.REJECTED
        req.reject_reason = notes
        write_training_audit(db, entity_type="REQUEST", entity_id=req.id,
                             action=TrainingAuditAction.REJECT, actor_id=user.id,
                             to_status=_S.REJECTED.value, note=notes)
        _notify(db, _emp_user_id(db, req), type_="training_request_rejected",
                title="Training request declined", message=f"{req.request_number} was declined",
                url="/user/self-service/training")
    elif decision == "RETURN":
        step["decision"] = TrainingRequestDecision.RETURNED.value
        assert_request_transition(req.status, _S.RETURNED)
        req.status = _S.RETURNED
        req.return_reason = notes
        write_training_audit(db, entity_type="REQUEST", entity_id=req.id,
                             action=TrainingAuditAction.RETURN, actor_id=user.id,
                             to_status=_S.RETURNED.value, note=notes)
        _notify(db, _emp_user_id(db, req), type_="training_request_returned",
                title="Training request returned",
                message=f"{req.request_number} returned for correction",
                url="/user/self-service/training")
    else:
        raise HTTPException(422, "decision must be APPROVE, REJECT, or RETURN")

    flag_modified(req, "approval_steps")


def fulfill_request(db: Session, req: TrainingRequest, actor: User, *,
                    due_date: Optional[date] = None, notes: Optional[str] = None,
                    program_id=None) -> TrainingAssignment:
    if req.status != _S.APPROVED:
        raise HTTPException(409, "Only an APPROVED request can be fulfilled")
    if req.resulting_assignment_id:
        raise HTTPException(409, "Request already fulfilled")
    # External-provider requests carry no program. HR may attach one at fulfilment
    # so the employee still lands in a concrete enrolment.
    if not req.program_id and program_id:
        from app.models.hr.training import TrainingProgram
        prog = db.query(TrainingProgram.id).filter(
            TrainingProgram.id == program_id,
            TrainingProgram.is_deleted == False,  # noqa: E712
        ).first()
        if not prog:
            raise HTTPException(404, "Linked program not found")
        req.program_id = program_id
    if not req.program_id:
        raise HTTPException(422, "This request has no linked program; attach a program to fulfil it")
    a = TrainingAssignment(
        program_id=req.program_id,
        employee_id=req.employee_id,
        assigned_date=date.today(),
        due_date=due_date or (date.today() + timedelta(days=30)),
        status=TrainingAssignmentStatus.NOT_STARTED,
        enrollment_source="REQUEST",
        notes=notes or f"Fulfilled from request {req.request_number}",
    )
    db.add(a)
    db.flush()
    req.resulting_assignment_id = a.id
    assert_request_transition(req.status, _S.FULFILLED)
    req.status = _S.FULFILLED
    write_training_audit(db, entity_type="REQUEST", entity_id=req.id,
                         action=TrainingAuditAction.FULFILL, actor_id=actor.id,
                         to_status=_S.FULFILLED.value,
                         payload={"assignment_id": str(a.id)})
    _notify(db, _emp_user_id(db, req), type_="training_request_fulfilled",
            title="Training enrolled", message=f"You've been enrolled from {req.request_number}",
            url="/user/self-service/training")
    return a


def _emp_user_id(db: Session, req: TrainingRequest):
    row = db.query(Employee.user_id).filter(Employee.id == req.employee_id).first()
    return row[0] if row else None


_EDITABLE = {_S.DRAFT, _S.RETURNED}
_WITHDRAWABLE = {_S.DRAFT, _S.PENDING_APPROVAL, _S.RETURNED}


def to_request_response(db: Session, req: TrainingRequest) -> dict:
    """Build the TrainingRequestResponse dict (Pydantic validates on the way out)."""
    from app.models.hr.training import TrainingProgram
    from app.utils.hr.training.service import emp_display, enrich_steps_with_names
    disp = emp_display(db, req.employee_id)
    pname = None
    if req.program_id:
        r = db.query(TrainingProgram.name).filter(TrainingProgram.id == req.program_id).first()
        pname = r[0] if r else None
    return {
        "id": req.id, "request_number": req.request_number, "employee_id": req.employee_id,
        "employee_name": disp.get("name"), "employee_code": disp.get("code"),
        "department_name": disp.get("dept"), "designation_name": disp.get("desg"),
        "program_id": req.program_id,
        "program_name": pname, "title": req.title, "description": req.description,
        "justification": req.justification, "external_provider": req.external_provider,
        "estimated_cost": req.estimated_cost, "currency": req.currency,
        "preferred_start_date": req.preferred_start_date, "status": req.status,
        "approval_steps": enrich_steps_with_names(db, list(req.approval_steps or [])),
        "current_step": int(req.current_step or 0), "approved_at": req.approved_at,
        "approver_notes": req.approver_notes, "reject_reason": req.reject_reason,
        "return_reason": req.return_reason, "resulting_assignment_id": req.resulting_assignment_id,
        "submitted_at": req.submitted_at, "created_at": req.created_at,
        "can_edit": req.status in _EDITABLE, "can_withdraw": req.status in _WITHDRAWABLE,
    }
