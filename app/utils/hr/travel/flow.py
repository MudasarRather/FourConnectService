"""HR Travel — request creation, submission and per-stage decision flow.

Shared by the admin router and the self-service / manager-queue router so the
business rules live in exactly one place. Mirrors the Reimbursements flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.travel_request import TravelRequest
from app.models.hr.travel_category import TravelCategory
from app.models.hr.travel_policy import TravelPolicy
from app.models.hr.travel_type import TravelRequestStatus, TravelDecision, TravelAuditAction
from app.utils.hr.lifecycle_guard import guard_on_payroll, guard_within_tenure
from app.utils.hr.travel.chain import (
    normalize_chain_config, build_request_steps, step_status,
    auto_skip_unresolvable, mirror_final_columns, assert_transition,
)
from app.utils.hr.travel.service import (
    generate_request_number, validate_details_against_schema, write_travel_audit,
)


def get_category(db: Session, category_id: Optional[UUID]) -> Optional[TravelCategory]:
    if not category_id:
        return None
    cat = db.query(TravelCategory).filter(
        TravelCategory.id == category_id, TravelCategory.is_deleted == False,  # noqa: E712
    ).first()
    if not cat:
        raise HTTPException(404, "Travel category not found")
    if not cat.is_active:
        raise HTTPException(409, "This travel category is inactive")
    return cat


def get_policy_for(db: Session, *, grade_id: Optional[UUID]) -> Optional[TravelPolicy]:
    """Most specific active policy: a grade-scoped policy beats a global one."""
    q = db.query(TravelPolicy).filter(
        TravelPolicy.is_deleted == False, TravelPolicy.is_active == True)  # noqa: E712
    if grade_id:
        scoped = q.filter(TravelPolicy.grade_id == grade_id).first()
        if scoped:
            return scoped
    return q.filter(TravelPolicy.grade_id.is_(None)).first()


def _num_days(departure_date, return_date) -> int:
    if not departure_date or not return_date:
        return 1
    return max(1, (return_date - departure_date).days + 1)


# ── multi-city itinerary helpers ──────────────────────────────────────────────
def _parse_date(s):
    from datetime import date as _date, datetime as _dt
    if isinstance(s, _date):
        return s
    if isinstance(s, str):
        for fmt in ("%Y-%m-%d",):
            try:
                return _dt.strptime(s[:10], fmt).date()
            except (ValueError, TypeError):
                pass
        try:
            return _dt.fromisoformat(s).date()
        except (ValueError, TypeError):
            return None
    return None


def _legs_to_dicts(itinerary):
    """Normalize a list of pydantic TravelLeg / dicts to JSON-safe dicts
    (date → ISO string, enum → value) for the JSONB column."""
    out = []
    for lg in itinerary or []:
        d = lg.model_dump() if hasattr(lg, "model_dump") else dict(lg)
        dep = d.get("departure_date")
        if hasattr(dep, "isoformat"):
            d["departure_date"] = dep.isoformat()
        tc = d.get("to_city_category")
        if tc is not None and hasattr(tc, "value"):
            d["to_city_category"] = tc.value
        mode = str(d.get("mode") or "FLIGHT").upper()
        if mode not in ("FLIGHT", "TRAIN", "BUS", "TAXI", "RENTAL"):
            mode = "FLIGHT"
        out.append({
            "from_location": d.get("from_location"),
            "to_location": d.get("to_location"),
            "departure_date": d.get("departure_date"),
            "mode": mode,
            "to_city_category": d.get("to_city_category"),
        })
    return out


def _modes_to_flags(legs) -> dict:
    """Union of per-leg transport modes → trip-level logistics flags. A multi-city
    trip mixing flight + train sets BOTH flight_required and train_required, so the
    booking surface offers each and the cost/preference steps stay consistent."""
    modes = {str(l.get("mode") or "FLIGHT").upper() for l in (legs or [])}
    return {
        "flight_required": "FLIGHT" in modes,
        "train_required": "TRAIN" in modes,
        "local_transport_required": bool(modes & {"BUS", "TAXI", "RENTAL"}),
    }


def derive_envelope(legs):
    """First origin → final destination, first departure → last return. The single
    from/to + dates that every downstream consumer reads, derived from the legs."""
    if not legs:
        return None
    first, last = legs[0], legs[-1]
    dep = _parse_date(first.get("departure_date"))
    ret = _parse_date(last.get("departure_date"))
    return {
        "from_location": first.get("from_location"),
        "to_location": last.get("to_location"),
        "departure_date": dep,
        "return_date": ret,
    }


def _est_total(payload) -> Decimal:
    return (
        Decimal(str(payload.est_travel_cost or 0))
        + Decimal(str(payload.est_accommodation_cost or 0))
        + Decimal(str(payload.est_local_cost or 0))
        + Decimal(str(payload.est_food_cost or 0))
        + Decimal(str(payload.est_misc_cost or 0))
    )


def build_new_request(db: Session, *, employee: Employee, category: Optional[TravelCategory],
                      payload, actor: User) -> TravelRequest:
    """Construct a TravelRequest row (status DRAFT, no chain yet)."""
    if category is not None:
        validate_details_against_schema(payload.details, category.field_schema)
    attachments = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in (payload.attachments or [])]

    # Resolve the trip shape and derive the canonical envelope (from/to + dates)
    # so downstream consumers keep reading the single-route fields unchanged.
    trip_type = str(getattr(payload, "trip_type", None) or "ROUND_TRIP").upper()
    itinerary = _legs_to_dicts(getattr(payload, "itinerary", None)) if trip_type == "MULTI_CITY" else None
    from_loc, to_loc = payload.from_location, payload.to_location
    dep, ret = payload.departure_date, payload.return_date
    if trip_type == "MULTI_CITY" and itinerary:
        env = derive_envelope(itinerary)
        if env:
            from_loc = env["from_location"] or from_loc
            to_loc = env["to_location"] or to_loc
            dep = env["departure_date"] or dep
            ret = env["return_date"] or ret
    elif trip_type == "ONE_WAY":
        ret = dep

    # Multi-city derives its transport flags from the per-leg modes (hotel/advance stay user-set).
    mc_flags = _modes_to_flags(itinerary) if (trip_type == "MULTI_CITY" and itinerary) else None

    req = TravelRequest(
        travel_reference_number=generate_request_number(db),
        employee_id=employee.id,
        department_id=employee.department_id,
        designation_id=employee.designation_id,
        grade_id=employee.grade_id,
        purpose=payload.purpose,
        travel_type=payload.travel_type,
        category_id=category.id if category else None,
        priority=payload.priority,
        trip_type=trip_type,
        itinerary=itinerary,
        from_location=from_loc,
        to_location=to_loc,
        from_location_id=payload.from_location_id,
        to_location_id=payload.to_location_id,
        to_city_category=payload.to_city_category,
        departure_date=dep,
        return_date=ret,
        num_days=_num_days(dep, ret),
        project_id=payload.project_id,
        cost_center=payload.cost_center,
        budget_head=payload.budget_head,
        funding_source=payload.funding_source,
        flight_required=(mc_flags["flight_required"] if mc_flags else payload.flight_required),
        train_required=(mc_flags["train_required"] if mc_flags else payload.train_required),
        hotel_required=payload.hotel_required,
        # local transport = a road leg OR the traveller's own in-city need (don't drop their choice)
        local_transport_required=((mc_flags["local_transport_required"] or payload.local_transport_required) if mc_flags else payload.local_transport_required),
        advance_required=payload.advance_required,
        est_travel_cost=payload.est_travel_cost,
        est_accommodation_cost=payload.est_accommodation_cost,
        est_local_cost=payload.est_local_cost,
        est_food_cost=payload.est_food_cost,
        est_misc_cost=payload.est_misc_cost,
        est_total_cost=_est_total(payload),
        currency=payload.currency or "INR",
        attachments=attachments,
        details=payload.details or {},
        status=TravelRequestStatus.DRAFT,
        created_by_id=actor.id,
    )
    db.add(req)
    db.flush()
    return req


def recompute_request_derived(req: TravelRequest) -> None:
    """Refresh the derived envelope (multi-city), num_days + est_total_cost after an edit."""
    tt = str(req.trip_type or "ROUND_TRIP").upper()
    if tt == "MULTI_CITY" and req.itinerary:
        legs = _legs_to_dicts(req.itinerary)
        req.itinerary = legs                       # reassign so SQLAlchemy flags the JSONB change
        env = derive_envelope(legs)
        if env:
            if env["from_location"]:
                req.from_location = env["from_location"]
            if env["to_location"]:
                req.to_location = env["to_location"]
            if env["departure_date"]:
                req.departure_date = env["departure_date"]
            if env["return_date"]:
                req.return_date = env["return_date"]
        # keep the transport flags in lock-step with the leg modes (local also honours
        # the traveller's own in-city toggle, already set on req by the edit handler)
        flags = _modes_to_flags(legs)
        req.flight_required = flags["flight_required"]
        req.train_required = flags["train_required"]
        req.local_transport_required = flags["local_transport_required"] or bool(req.local_transport_required)
    elif tt == "ONE_WAY":
        req.return_date = req.departure_date
        req.itinerary = None
    else:
        req.itinerary = None
    req.num_days = _num_days(req.departure_date, req.return_date)
    req.est_total_cost = (
        Decimal(str(req.est_travel_cost or 0))
        + Decimal(str(req.est_accommodation_cost or 0))
        + Decimal(str(req.est_local_cost or 0))
        + Decimal(str(req.est_food_cost or 0))
        + Decimal(str(req.est_misc_cost or 0))
    )


def submit_request(db: Session, req: TravelRequest, employee: Employee, actor: User) -> Optional[UUID]:
    """DRAFT/RETURNED → PENDING_APPROVAL. Snapshots the chain. Returns the next
    approver's user id (or None) for notification."""
    if req.status not in (TravelRequestStatus.DRAFT, TravelRequestStatus.RETURNED):
        raise HTTPException(409, f"Cannot submit a {req.status.value} request")
    policy = get_policy_for(db, grade_id=req.grade_id)
    chain_cfg = normalize_chain_config(policy.approval_chain if policy else None)
    steps = build_request_steps(chain_cfg, employee, req.est_total_cost)
    cur_idx = auto_skip_unresolvable(steps, 0)
    from_status = req.status.value
    req.approval_steps = steps
    flag_modified(req, "approval_steps")
    req.current_step = cur_idx
    req.return_reason = None
    req.returned_at = None
    req.status = step_status(steps, cur_idx)
    if req.status == TravelRequestStatus.APPROVED:
        req.approved_at = datetime.now(timezone.utc)
    req.submitted_at = datetime.now(timezone.utc)
    req.submitted_by_id = actor.id
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=TravelAuditAction.SUBMIT, actor_id=actor.id,
                       from_status=from_status, to_status=req.status.value,
                       note=f"Submitted {req.travel_reference_number}")
    next_approver = None
    if cur_idx < len(steps):
        next_approver = steps[cur_idx].get("approver_user_id")
    return UUID(next_approver) if next_approver else None


def apply_decision(db: Session, req: TravelRequest, *, decision: TravelDecision,
                   notes: Optional[str], actor: User) -> Tuple[TravelRequestStatus, Optional[UUID], str]:
    """Apply a per-stage decision at the current step. Returns
    (new_status, next_approver_user_id, notification_event). Caller must have
    already verified the actor can act on the current step."""
    if req.status != TravelRequestStatus.PENDING_APPROVAL:
        raise HTTPException(409, f"Request is not awaiting approval (status {req.status.value})")
    steps = list(req.approval_steps or [])
    idx = int(req.current_step or 0)
    if idx >= len(steps):
        raise HTTPException(409, "Request is fully resolved")
    cur = steps[idx]
    now_iso = datetime.now(timezone.utc).isoformat()
    from_status = req.status.value
    next_approver: Optional[UUID] = None

    if decision == TravelDecision.APPROVED:
        # Lifecycle leak guard: a request raised while the employee was active must
        # NOT stay approvable once they've left payroll. Re-check at decision time
        # (state can change between submit and approval) — require the traveller to
        # still be ON PAYROLL (ACTIVE / ON_PROBATION / ON_NOTICE), which blocks the
        # SUSPENDED and the fully separated (EXITED / ARCHIVED / INACTIVE); and for
        # anyone leaving, block a trip scheduled past their last working day. Covers
        # BOTH the admin queue and the manager self-service queue (shared fn).
        # REJECT / RETURN stay allowed so a stale request can still be closed out.
        emp = db.query(Employee).filter(Employee.id == req.employee_id).first()
        guard_on_payroll(emp, "approve this travel request")
        guard_within_tenure(emp, req.return_date or req.departure_date, "approve travel")
        cur["decision"] = TravelDecision.APPROVED.value
        cur["decided_by_id"] = str(actor.id)
        cur["decided_at"] = now_iso
        cur["notes"] = notes
        new_idx = auto_skip_unresolvable(steps, idx + 1)
        req.approval_steps = steps
        flag_modified(req, "approval_steps")
        req.current_step = new_idx
        if new_idx >= len(steps):
            assert_transition(req.status, TravelRequestStatus.APPROVED)
            req.status = TravelRequestStatus.APPROVED
            req.approved_at = datetime.now(timezone.utc)
            mirror_final_columns(req)
            event = "approved"
        else:
            req.status = step_status(steps, new_idx)
            mirror_final_columns(req)
            na = steps[new_idx].get("approver_user_id")
            next_approver = UUID(na) if na else None
            event = "advanced"
    elif decision == TravelDecision.REJECTED:
        cur["decision"] = TravelDecision.REJECTED.value
        cur["decided_by_id"] = str(actor.id)
        cur["decided_at"] = now_iso
        cur["notes"] = notes
        req.approval_steps = steps
        flag_modified(req, "approval_steps")
        assert_transition(req.status, TravelRequestStatus.REJECTED)
        req.status = TravelRequestStatus.REJECTED
        req.rejected_at = datetime.now(timezone.utc)
        req.reject_reason = notes
        event = "rejected"
    elif decision == TravelDecision.RETURNED:
        cur["decision"] = TravelDecision.RETURNED.value
        cur["decided_by_id"] = str(actor.id)
        cur["decided_at"] = now_iso
        cur["notes"] = notes
        req.approval_steps = steps
        flag_modified(req, "approval_steps")
        assert_transition(req.status, TravelRequestStatus.RETURNED)
        req.status = TravelRequestStatus.RETURNED
        req.returned_at = datetime.now(timezone.utc)
        req.return_reason = notes
        event = "returned"
    else:
        raise HTTPException(422, "Unsupported decision")

    action_map = {
        "approved": TravelAuditAction.APPROVE, "advanced": TravelAuditAction.APPROVE,
        "rejected": TravelAuditAction.REJECT, "returned": TravelAuditAction.RETURN,
    }
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=action_map[event], actor_id=actor.id,
                       from_status=from_status, to_status=req.status.value,
                       note=notes or f"{decision.value} at step {idx}")
    return req.status, next_approver, event
