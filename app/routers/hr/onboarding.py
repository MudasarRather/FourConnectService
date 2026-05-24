"""HR Onboarding — process, checklist, approvals, tasks, dashboard, reports."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from math import ceil
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, func, and_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee, LifecycleState
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.recruitment import Offer, OfferStatus, Candidate
from app.models.hr.onboarding import (
    OnboardingProcess, OnboardingStatus, OnboardingStage,
    OnboardingChecklistItem, OnboardingChecklistTemplate,
    OnboardingDocument, DocumentSlotStatus,
    JoiningApproval, ApprovalDecision,
    OnboardingTask, TaskStatus as OnbTaskStatus,
    EmployeeIdentity,
    ChecklistItemStatus,
)
from app.models.hr.training import TrainingAssignment, TrainingAssignmentStatus
from app.models.hr.asset import AssetAllocation, AllocationStatus
from app.schemas.hr.onboarding import (
    OnboardingProcessUpdate, OnboardingProcessResponse, OnboardingProcessListResponse,
    ChecklistItemCreate, ChecklistItemUpdate, ChecklistItemResponse,
    ChecklistTemplateCreate, ChecklistTemplateUpdate, ChecklistTemplateResponse,
    JoiningApprovalCreate, JoiningApprovalDecideBody, JoiningApprovalResponse,
    OnboardingTaskCreate, OnboardingTaskUpdate, OnboardingTaskResponse,
    OnboardingStageState, JourneyStateResponse,
    DashboardStatsResponse, HotTaskResponse,
    PendingJoiningResponse, ProcessDetailResponse,
    OnboardingDocumentSlotResponse, EmployeeIdentityResponse, WelcomeKitResponse,
)
from app.utils.dependencies import get_current_superuser


router = APIRouter(prefix="/hr/onboarding", tags=["HR — Onboarding"])


# ─────────────────────────────────── Helpers ───────────────────────────────────

STAGE_ORDER = [
    OnboardingStage.PRE_JOIN,
    OnboardingStage.APPROVAL,
    OnboardingStage.DOCS,
    OnboardingStage.IDENTITY,
    OnboardingStage.ASSETS,
    OnboardingStage.TRAINING,
    OnboardingStage.ACTIVE,
]

STAGE_LABELS = {
    OnboardingStage.PRE_JOIN:  "Pre-Join",
    OnboardingStage.APPROVAL:  "Approvals",
    OnboardingStage.DOCS:      "Documents",
    OnboardingStage.IDENTITY:  "Identity",
    OnboardingStage.ASSETS:    "Assets",
    OnboardingStage.TRAINING:  "Training",
    OnboardingStage.ACTIVE:    "Active",
}


def _employee_snapshot_fields(db: Session, employee_id: UUID) -> dict:
    """Return {employee_name, employee_code, employee_designation, employee_department}."""
    row = (
        db.query(
            Employee.employee_id, Employee.employee_code,
            User.full_name,
            Designation.name.label("designation_name"),
            Department.name.label("department_name"),
        )
        .join(User, User.id == Employee.user_id)
        .outerjoin(Designation, Designation.id == Employee.designation_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .filter(Employee.id == employee_id)
        .first()
    )
    if not row:
        return {}
    return {
        "employee_name": row.full_name,
        "employee_code": row.employee_id or row.employee_code,
        "employee_designation": row.designation_name,
        "employee_department": row.department_name,
    }


def _to_process_response(db: Session, p: OnboardingProcess) -> OnboardingProcessResponse:
    snap = _employee_snapshot_fields(db, p.employee_id)
    return OnboardingProcessResponse(
        id=p.id,
        employee_id=p.employee_id,
        offer_id=p.offer_id,
        status=p.status,
        current_stage=p.current_stage,
        progress_pct=p.progress_pct,
        target_joining_date=p.target_joining_date,
        actual_joining_date=p.actual_joining_date,
        started_at=p.started_at,
        completed_at=p.completed_at,
        on_hold_reason=p.on_hold_reason,
        created_at=p.created_at,
        updated_at=p.updated_at,
        **snap,
    )


def _user_name(db: Session, user_id: Optional[UUID]) -> Optional[str]:
    if not user_id:
        return None
    row = db.query(User.full_name).filter(User.id == user_id).first()
    return row[0] if row else None


def _to_checklist_response(db: Session, item: OnboardingChecklistItem) -> ChecklistItemResponse:
    return ChecklistItemResponse(
        id=item.id,
        process_id=item.process_id,
        template_id=item.template_id,
        category=item.category,
        task_name=item.task_name,
        description=item.description,
        assigned_to_user_id=item.assigned_to_user_id,
        assigned_to_name=_user_name(db, item.assigned_to_user_id),
        due_date=item.due_date,
        status=item.status,
        is_mandatory=item.is_mandatory,
        completed_by_user_id=item.completed_by_user_id,
        completed_by_name=_user_name(db, item.completed_by_user_id),
        completed_at=item.completed_at,
        remarks=item.remarks,
        sort_order=item.sort_order,
    )


def _to_approval_response(db: Session, a: JoiningApproval) -> JoiningApprovalResponse:
    return JoiningApprovalResponse(
        id=a.id,
        process_id=a.process_id,
        approver_role=a.approver_role,
        approver_user_id=a.approver_user_id,
        approver_name=_user_name(db, a.approver_user_id),
        status=a.status,
        sort_order=a.sort_order,
        decision_at=a.decision_at,
        decision_notes=a.decision_notes,
    )


def _to_task_response(db: Session, t: OnboardingTask) -> OnboardingTaskResponse:
    return OnboardingTaskResponse(
        id=t.id,
        process_id=t.process_id,
        title=t.title,
        description=t.description,
        category=t.category,
        assigned_to_user_id=t.assigned_to_user_id,
        assigned_to_name=_user_name(db, t.assigned_to_user_id),
        due_date=t.due_date,
        status=t.status,
        priority=t.priority,
        completed_at=t.completed_at,
        sla_hours=t.sla_hours,
        escalation_user_id=t.escalation_user_id,
        depends_on_task_id=t.depends_on_task_id,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _recalculate_progress(db: Session, process: OnboardingProcess) -> None:
    """Recompute progress_pct + current_stage based on item/doc/asset/training state."""
    items = db.query(OnboardingChecklistItem).filter(
        OnboardingChecklistItem.process_id == process.id,
    ).all()
    docs = db.query(OnboardingDocument).filter(
        OnboardingDocument.process_id == process.id,
        OnboardingDocument.is_mandatory == True,  # noqa: E712
    ).all()
    approvals = db.query(JoiningApproval).filter(
        JoiningApproval.process_id == process.id,
    ).all()
    assets = db.query(AssetAllocation).filter(
        AssetAllocation.process_id == process.id,
        AssetAllocation.status == AllocationStatus.ALLOCATED,
    ).count()
    training = db.query(TrainingAssignment).filter(
        TrainingAssignment.process_id == process.id,
    ).all()

    total_units = 0
    done_units = 0
    # Checklist
    for it in items:
        total_units += 1
        if it.status in (ChecklistItemStatus.COMPLETED, ChecklistItemStatus.WAIVED):
            done_units += 1
    # Mandatory docs
    for d in docs:
        total_units += 1
        if d.status == DocumentSlotStatus.VERIFIED:
            done_units += 1
    # Approvals
    for a in approvals:
        total_units += 1
        if a.status in (ApprovalDecision.APPROVED, ApprovalDecision.WAIVED):
            done_units += 1
    # Training
    for t in training:
        total_units += 1
        if t.status in (TrainingAssignmentStatus.COMPLETED, TrainingAssignmentStatus.WAIVED):
            done_units += 1

    pct = int(round((done_units / total_units) * 100)) if total_units else 0
    process.progress_pct = pct

    # Stage progression — coarse-grained inference
    docs_all_verified = all(d.status == DocumentSlotStatus.VERIFIED for d in docs) if docs else True
    approvals_done = (
        all(a.status in (ApprovalDecision.APPROVED, ApprovalDecision.WAIVED) for a in approvals)
        if approvals else True
    )
    identity_ready = bool(
        db.query(EmployeeIdentity)
        .filter(EmployeeIdentity.employee_id == process.employee_id, EmployeeIdentity.status == "ISSUED")
        .first()
    )
    training_done = all(
        t.status in (TrainingAssignmentStatus.COMPLETED, TrainingAssignmentStatus.WAIVED) for t in training
    ) if training else True

    if pct >= 100:
        process.current_stage = OnboardingStage.ACTIVE
        process.status = OnboardingStatus.COMPLETED
        process.completed_at = datetime.utcnow()
    elif training_done and identity_ready and docs_all_verified and approvals_done and assets > 0:
        process.current_stage = OnboardingStage.TRAINING
    elif identity_ready and docs_all_verified and approvals_done:
        process.current_stage = OnboardingStage.ASSETS
    elif docs_all_verified and approvals_done:
        process.current_stage = OnboardingStage.IDENTITY
    elif approvals_done:
        process.current_stage = OnboardingStage.DOCS
    else:
        process.current_stage = OnboardingStage.APPROVAL if approvals else OnboardingStage.PRE_JOIN


# ───────────────────────────── Dashboard / stats ─────────────────────────────

@router.get("/dashboard/stats", response_model=DashboardStatsResponse)
def dashboard_stats(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    today = date.today()
    base_proc = db.query(OnboardingProcess).filter(OnboardingProcess.is_deleted == False)  # noqa: E712

    pending_offers = (
        db.query(Offer)
        .filter(Offer.status == OfferStatus.ACCEPTED, Offer.employee_id.is_(None))
        .count()
    )
    today_joining = base_proc.filter(OnboardingProcess.target_joining_date == today).count()
    pending_docs = (
        db.query(OnboardingProcess.id)
        .join(OnboardingDocument, OnboardingDocument.process_id == OnboardingProcess.id)
        .filter(
            OnboardingDocument.is_mandatory == True,  # noqa: E712
            OnboardingDocument.status.in_([DocumentSlotStatus.PENDING, DocumentSlotStatus.REJECTED]),
            OnboardingProcess.status == OnboardingStatus.IN_PROGRESS,
        )
        .distinct()
        .count()
    )
    pending_asset_alloc = (
        db.query(OnboardingProcess.id)
        .outerjoin(AssetAllocation, and_(
            AssetAllocation.process_id == OnboardingProcess.id,
            AssetAllocation.status == AllocationStatus.ALLOCATED,
        ))
        .filter(OnboardingProcess.status == OnboardingStatus.IN_PROGRESS)
        .group_by(OnboardingProcess.id)
        .having(func.count(AssetAllocation.id) == 0)
        .count()
    )
    probation_employees = (
        db.query(Employee)
        .filter(Employee.is_deleted == False, Employee.lifecycle_state == LifecycleState.ON_PROBATION)  # noqa: E712
        .count()
    )
    training_pending = (
        db.query(TrainingAssignment)
        .filter(TrainingAssignment.status.in_([
            TrainingAssignmentStatus.NOT_STARTED,
            TrainingAssignmentStatus.IN_PROGRESS,
        ]))
        .count()
    )
    incomplete = base_proc.filter(OnboardingProcess.status == OnboardingStatus.IN_PROGRESS).count()

    dept_rows = (
        db.query(Department.name, func.count(OnboardingProcess.id))
        .join(Employee, Employee.department_id == Department.id)
        .join(OnboardingProcess, OnboardingProcess.employee_id == Employee.id)
        .filter(OnboardingProcess.status.in_([
            OnboardingStatus.IN_PROGRESS, OnboardingStatus.ON_HOLD,
        ]))
        .group_by(Department.name)
        .all()
    )
    department_wise = [{"department": name or "Unassigned", "count": int(c)} for name, c in dept_rows]

    return DashboardStatsResponse(
        pending_joinings=int(pending_offers),
        today_joining=int(today_joining),
        pending_documents=int(pending_docs),
        pending_asset_allocation=int(pending_asset_alloc),
        probation_employees=int(probation_employees),
        training_pending=int(training_pending),
        incomplete_onboarding=int(incomplete),
        department_wise_joining=department_wise,
    )


@router.get("/dashboard/hot-tasks", response_model=List[HotTaskResponse])
def dashboard_hot_tasks(
    limit: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    today = date.today()
    tasks = (
        db.query(OnboardingTask)
        .filter(OnboardingTask.status.in_([OnbTaskStatus.TODO, OnbTaskStatus.IN_PROGRESS, OnbTaskStatus.BLOCKED]))
        .order_by(OnboardingTask.priority.desc(), OnboardingTask.due_date.asc().nullslast())
        .limit(limit)
        .all()
    )
    result: List[HotTaskResponse] = []
    for t in tasks:
        proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == t.process_id).first()
        emp_name = None
        if proc:
            snap = _employee_snapshot_fields(db, proc.employee_id)
            emp_name = snap.get("employee_name")
        sla_breach = bool(t.due_date and t.due_date < today)
        result.append(HotTaskResponse(
            id=t.id, process_id=t.process_id, employee_name=emp_name,
            title=t.title, due_date=t.due_date, status=t.status, priority=t.priority,
            sla_breach=sla_breach,
        ))
    return result


# ───────────────────────────── Pending Joining ─────────────────────────────

@router.get("/pending-joining", response_model=List[PendingJoiningResponse])
def pending_joining_tray(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    offers = (
        db.query(Offer)
        .options(
            joinedload(Offer.candidate),
            joinedload(Offer.department),
            joinedload(Offer.location),
        )
        .filter(Offer.status == OfferStatus.ACCEPTED)
        .order_by(Offer.joining_date.asc().nullslast(), Offer.updated_at.desc())
        .all()
    )
    result: List[PendingJoiningResponse] = []
    for o in offers:
        cand = o.candidate
        result.append(PendingJoiningResponse(
            offer_id=o.id,
            offer_code=o.offer_code,
            candidate_id=cand.id if cand else o.candidate_id,
            candidate_name=cand.full_name if cand else "Candidate",
            candidate_email=cand.email if cand else None,
            candidate_mobile=cand.mobile if cand else None,
            designation=o.designation,  # String column on Offer
            department=o.department.name if o.department else None,
            location=o.location.name if o.location else None,
            joining_date=o.joining_date,
            offered_salary=float(o.offered_salary) if o.offered_salary else None,
            accepted_at=o.updated_at,
            has_employee=o.employee_id is not None,
        ))
    return result


# ───────────────────────────── Backfill ─────────────────────────────

@router.post("/processes/backfill")
def backfill_processes(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Create OnboardingProcess (+ checklist/docs/identity/kit/accounts) for any
    existing Employee that doesn't have one yet. Idempotent — safe to call
    multiple times. Returns count of processes newly created."""
    from app.utils.hr.onboarding_bootstrap import bootstrap_onboarding
    from app.models.hr.recruitment import Offer

    employees_without_process = (
        db.query(Employee)
        .outerjoin(OnboardingProcess, OnboardingProcess.employee_id == Employee.id)
        .filter(Employee.is_deleted == False)  # noqa: E712
        .filter(OnboardingProcess.id.is_(None))
        .all()
    )

    created = 0
    for emp in employees_without_process:
        offer = (
            db.query(Offer).filter(Offer.employee_id == emp.id).first()
            if emp.id else None
        )
        try:
            bootstrap_onboarding(db, employee=emp, offer=offer, actor_id=admin.id)
            created += 1
        except Exception as exc:
            # Best effort — don't blow up the whole backfill on one employee.
            with open("crash.log", "a", encoding="utf-8") as f:
                f.write(f"Backfill failed for employee {emp.id}: {exc}\n")
    db.commit()
    return {"created": created, "skipped": 0, "total_scanned": len(employees_without_process)}


# ───────────────────────────── Process listing / detail ─────────────────────────────

@router.get("/processes", response_model=OnboardingProcessListResponse)
def list_processes(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=500),
    onboarding_status: Optional[OnboardingStatus] = None,
    current_stage: Optional[OnboardingStage] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = (
        db.query(OnboardingProcess)
        .filter(OnboardingProcess.is_deleted == False)  # noqa: E712
    )
    if onboarding_status:
        q = q.filter(OnboardingProcess.status == onboarding_status)
    if current_stage:
        q = q.filter(OnboardingProcess.current_stage == current_stage)
    if search:
        like = f"%{search.lower()}%"
        q = (
            q.join(Employee, Employee.id == OnboardingProcess.employee_id)
             .join(User, User.id == Employee.user_id)
             .filter(or_(
                 func.lower(User.full_name).like(like),
                 func.lower(Employee.employee_id).like(like),
             ))
        )

    total = q.count()
    rows = (
        q.order_by(OnboardingProcess.created_at.desc())
         .offset((page - 1) * limit)
         .limit(limit)
         .all()
    )
    items = [_to_process_response(db, p) for p in rows]
    total_pages = ceil(total / limit) if limit else 1
    return OnboardingProcessListResponse(items=items, total=total, page=page, limit=limit, total_pages=total_pages)


@router.get("/processes/{process_id}", response_model=ProcessDetailResponse)
def get_process_detail(
    process_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    proc = (
        db.query(OnboardingProcess)
        .options(
            selectinload(OnboardingProcess.checklist_items),
            selectinload(OnboardingProcess.documents),
            selectinload(OnboardingProcess.approvals),
            selectinload(OnboardingProcess.tasks),
        )
        .filter(OnboardingProcess.id == process_id, OnboardingProcess.is_deleted == False)  # noqa: E712
        .first()
    )
    if not proc:
        raise HTTPException(404, "Onboarding process not found")

    identity_row = db.query(EmployeeIdentity).filter(EmployeeIdentity.employee_id == proc.employee_id).first()
    from app.models.hr.onboarding import WelcomeKit as _WK
    kit_row = db.query(_WK).filter(_WK.employee_id == proc.employee_id).first()

    # Build document slot responses (joining drive file URLs)
    from app.models.drive_document import DriveDocument
    doc_responses: List[OnboardingDocumentSlotResponse] = []
    for d in proc.documents:
        drive_url = None
        drive_name = None
        if d.drive_document_id:
            dd = db.query(DriveDocument).filter(DriveDocument.id == d.drive_document_id).first()
            if dd:
                drive_url = dd.file_url
                drive_name = dd.file_name
        doc_responses.append(OnboardingDocumentSlotResponse(
            id=d.id, process_id=d.process_id,
            doc_type_key=d.doc_type_key, doc_type_label=d.doc_type_label,
            is_mandatory=d.is_mandatory,
            drive_document_id=d.drive_document_id,
            drive_file_url=drive_url,
            drive_file_name=drive_name,
            status=d.status, expiry_date=d.expiry_date, ocr_data=d.ocr_data,
            verified_by_user_id=d.verified_by_user_id,
            verified_by_name=_user_name(db, d.verified_by_user_id),
            verified_at=d.verified_at, rejection_reason=d.rejection_reason,
            sort_order=d.sort_order,
        ))

    return ProcessDetailResponse(
        process=_to_process_response(db, proc),
        checklist=[_to_checklist_response(db, i) for i in proc.checklist_items],
        documents=doc_responses,
        approvals=[_to_approval_response(db, a) for a in proc.approvals],
        tasks=[_to_task_response(db, t) for t in proc.tasks],
        identity=EmployeeIdentityResponse.model_validate(identity_row) if identity_row else None,
        welcome_kit=WelcomeKitResponse.model_validate(kit_row) if kit_row else None,
    )


@router.get("/processes/by-employee/{employee_id}", response_model=ProcessDetailResponse)
def get_process_by_employee(
    employee_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    proc = (
        db.query(OnboardingProcess)
        .filter(OnboardingProcess.employee_id == employee_id, OnboardingProcess.is_deleted == False)  # noqa: E712
        .first()
    )
    if not proc:
        raise HTTPException(404, "No onboarding process exists for this employee")
    return get_process_detail(proc.id, db=db, _admin=_admin)


@router.patch("/processes/{process_id}", response_model=OnboardingProcessResponse)
def update_process(
    process_id: UUID,
    payload: OnboardingProcessUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == process_id).first()
    if not proc:
        raise HTTPException(404, "Process not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(proc, k, v)
    if payload.status == OnboardingStatus.COMPLETED and proc.completed_at is None:
        proc.completed_at = datetime.utcnow()
    proc.last_updated_by_id = admin.id
    db.commit()
    db.refresh(proc)
    return _to_process_response(db, proc)


# ───────────────────────────── Journey state ─────────────────────────────

@router.get("/journey-state", response_model=JourneyStateResponse)
def journey_state(
    process_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Aggregate stage state across all processes, OR a single process if process_id given."""
    proc = None
    if process_id:
        proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == process_id).first()
        if not proc:
            raise HTTPException(404, "Process not found")

    stage_states: List[OnboardingStageState] = []

    if proc:
        current_idx = STAGE_ORDER.index(proc.current_stage)
        completed_idx = current_idx if proc.status == OnboardingStatus.COMPLETED else current_idx - 1
        for i, key in enumerate(STAGE_ORDER):
            is_complete = i <= completed_idx
            is_active = (i == current_idx and proc.status != OnboardingStatus.COMPLETED)
            stage_states.append(OnboardingStageState(
                key=key, label=STAGE_LABELS[key],
                count=0, percent=100 if is_complete else (proc.progress_pct if is_active else 0),
                is_active=is_active, is_complete=is_complete,
            ))
        return JourneyStateResponse(
            process_id=proc.id,
            current_stage=proc.current_stage,
            progress_pct=proc.progress_pct,
            stages=stage_states,
        )

    # Aggregate across all in-progress processes
    counts = (
        db.query(OnboardingProcess.current_stage, func.count(OnboardingProcess.id))
        .filter(OnboardingProcess.is_deleted == False)  # noqa: E712
        .filter(OnboardingProcess.status.in_([OnboardingStatus.IN_PROGRESS, OnboardingStatus.ON_HOLD]))
        .group_by(OnboardingProcess.current_stage)
        .all()
    )
    count_map = {stage: int(c) for stage, c in counts}
    total_in_progress = sum(count_map.values()) or 1
    for i, key in enumerate(STAGE_ORDER):
        c = count_map.get(key, 0)
        stage_states.append(OnboardingStageState(
            key=key, label=STAGE_LABELS[key],
            count=c, percent=int(round((c / total_in_progress) * 100)),
            is_active=(c > 0), is_complete=False,
        ))
    return JourneyStateResponse(process_id=None, current_stage=None, progress_pct=None, stages=stage_states)


# ───────────────────────────── Checklist ─────────────────────────────

@router.get("/processes/{process_id}/checklist", response_model=List[ChecklistItemResponse])
def list_checklist(
    process_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    items = (
        db.query(OnboardingChecklistItem)
        .filter(OnboardingChecklistItem.process_id == process_id)
        .order_by(OnboardingChecklistItem.sort_order.asc())
        .all()
    )
    return [_to_checklist_response(db, i) for i in items]


@router.post("/checklist-items", response_model=ChecklistItemResponse, status_code=http_status.HTTP_201_CREATED)
def create_checklist_item(
    payload: ChecklistItemCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if not db.query(OnboardingProcess).filter(OnboardingProcess.id == payload.process_id).first():
        raise HTTPException(404, "Process not found")
    item = OnboardingChecklistItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_checklist_response(db, item)


@router.patch("/checklist-items/{item_id}", response_model=ChecklistItemResponse)
def update_checklist_item(
    item_id: UUID,
    payload: ChecklistItemUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    item = db.query(OnboardingChecklistItem).filter(OnboardingChecklistItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Checklist item not found")
    data = payload.model_dump(exclude_unset=True)
    prev_status = item.status
    for k, v in data.items():
        setattr(item, k, v)
    if payload.status == ChecklistItemStatus.COMPLETED and prev_status != ChecklistItemStatus.COMPLETED:
        item.completed_at = datetime.utcnow()
        item.completed_by_user_id = admin.id
    proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == item.process_id).first()
    if proc:
        _recalculate_progress(db, proc)
    db.commit()
    db.refresh(item)
    return _to_checklist_response(db, item)


@router.delete("/checklist-items/{item_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_checklist_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    item = db.query(OnboardingChecklistItem).filter(OnboardingChecklistItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Not found")
    db.delete(item)
    proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == item.process_id).first()
    if proc:
        _recalculate_progress(db, proc)
    db.commit()


# ───────────────────────────── Checklist templates ─────────────────────────────

@router.get("/checklist-templates", response_model=List[ChecklistTemplateResponse])
def list_templates(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(OnboardingChecklistTemplate)
        .order_by(OnboardingChecklistTemplate.sort_order.asc())
        .all()
    )
    return [ChecklistTemplateResponse.model_validate(r) for r in rows]


@router.post("/checklist-templates", response_model=ChecklistTemplateResponse, status_code=http_status.HTTP_201_CREATED)
def create_template(
    payload: ChecklistTemplateCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    tpl = OnboardingChecklistTemplate(**payload.model_dump())
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return ChecklistTemplateResponse.model_validate(tpl)


@router.patch("/checklist-templates/{tpl_id}", response_model=ChecklistTemplateResponse)
def update_template(
    tpl_id: UUID,
    payload: ChecklistTemplateUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    tpl = db.query(OnboardingChecklistTemplate).filter(OnboardingChecklistTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(404, "Not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(tpl, k, v)
    db.commit()
    db.refresh(tpl)
    return ChecklistTemplateResponse.model_validate(tpl)


# ───────────────────────────── Approvals ─────────────────────────────

@router.get("/processes/{process_id}/approvals", response_model=List[JoiningApprovalResponse])
def list_approvals(
    process_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(JoiningApproval)
        .filter(JoiningApproval.process_id == process_id)
        .order_by(JoiningApproval.sort_order.asc())
        .all()
    )
    return [_to_approval_response(db, a) for a in rows]


@router.post("/approvals", response_model=JoiningApprovalResponse, status_code=http_status.HTTP_201_CREATED)
def create_approval(
    payload: JoiningApprovalCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    if not db.query(OnboardingProcess).filter(OnboardingProcess.id == payload.process_id).first():
        raise HTTPException(404, "Process not found")
    a = JoiningApproval(**payload.model_dump())
    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_approval_response(db, a)


@router.patch("/approvals/{approval_id}/decide", response_model=JoiningApprovalResponse)
def decide_approval(
    approval_id: UUID,
    payload: JoiningApprovalDecideBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = db.query(JoiningApproval).filter(JoiningApproval.id == approval_id).first()
    if not a:
        raise HTTPException(404, "Approval not found")
    if a.status != ApprovalDecision.PENDING:
        raise HTTPException(409, f"Approval already {a.status.value}")
    a.status = payload.decision
    a.approver_user_id = a.approver_user_id or admin.id
    a.decision_at = datetime.utcnow()
    a.decision_notes = payload.notes
    proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == a.process_id).first()
    if proc:
        _recalculate_progress(db, proc)
    db.commit()
    db.refresh(a)
    return _to_approval_response(db, a)


@router.delete("/approvals/{approval_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_approval(
    approval_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = db.query(JoiningApproval).filter(JoiningApproval.id == approval_id).first()
    if not a:
        raise HTTPException(404, "Not found")
    db.delete(a)
    db.commit()


# ───────────────────────────── Tasks ─────────────────────────────

@router.get("/processes/{process_id}/tasks", response_model=List[OnboardingTaskResponse])
def list_tasks(
    process_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(OnboardingTask)
        .filter(OnboardingTask.process_id == process_id)
        .order_by(OnboardingTask.created_at.desc())
        .all()
    )
    return [_to_task_response(db, t) for t in rows]


@router.get("/tasks", response_model=List[OnboardingTaskResponse])
def list_all_tasks(
    status_filter: Optional[OnbTaskStatus] = None,
    process_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(OnboardingTask)
    if status_filter:
        q = q.filter(OnboardingTask.status == status_filter)
    if process_id:
        q = q.filter(OnboardingTask.process_id == process_id)
    rows = q.order_by(OnboardingTask.priority.desc(), OnboardingTask.due_date.asc().nullslast()).limit(500).all()
    return [_to_task_response(db, t) for t in rows]


@router.post("/tasks", response_model=OnboardingTaskResponse, status_code=http_status.HTTP_201_CREATED)
def create_task(
    payload: OnboardingTaskCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    if not db.query(OnboardingProcess).filter(OnboardingProcess.id == payload.process_id).first():
        raise HTTPException(404, "Process not found")
    t = OnboardingTask(**payload.model_dump())
    db.add(t)
    db.commit()
    db.refresh(t)
    return _to_task_response(db, t)


@router.patch("/tasks/{task_id}", response_model=OnboardingTaskResponse)
def update_task(
    task_id: UUID,
    payload: OnboardingTaskUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = db.query(OnboardingTask).filter(OnboardingTask.id == task_id).first()
    if not t:
        raise HTTPException(404, "Task not found")
    prev = t.status
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    if payload.status == OnbTaskStatus.DONE and prev != OnbTaskStatus.DONE:
        t.completed_at = datetime.utcnow()
        t.completed_by_user_id = admin.id
    db.commit()
    db.refresh(t)
    return _to_task_response(db, t)


@router.delete("/tasks/{task_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    t = db.query(OnboardingTask).filter(OnboardingTask.id == task_id).first()
    if not t:
        raise HTTPException(404, "Not found")
    db.delete(t)
    db.commit()


# ───────────────────────────── Reports ─────────────────────────────

@router.get("/reports/joining-summary")
def report_joining_summary(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    today = date.today()
    months = []
    for i in range(5, -1, -1):
        anchor = today.replace(day=1) - timedelta(days=30 * i)
        m_start = anchor.replace(day=1)
        # Next month start
        if m_start.month == 12:
            m_end = m_start.replace(year=m_start.year + 1, month=1)
        else:
            m_end = m_start.replace(month=m_start.month + 1)
        count = (
            db.query(OnboardingProcess)
            .filter(OnboardingProcess.created_at >= m_start)
            .filter(OnboardingProcess.created_at < m_end)
            .count()
        )
        months.append({"label": m_start.strftime("%b %Y"), "count": int(count)})
    return {"months": months}


@router.get("/reports/missing-documents")
def report_missing_documents(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(OnboardingDocument.doc_type_label, func.count(OnboardingDocument.id))
        .filter(OnboardingDocument.is_mandatory == True)  # noqa: E712
        .filter(OnboardingDocument.status.in_([DocumentSlotStatus.PENDING, DocumentSlotStatus.REJECTED, DocumentSlotStatus.EXPIRED]))
        .group_by(OnboardingDocument.doc_type_label)
        .order_by(func.count(OnboardingDocument.id).desc())
        .all()
    )
    return {"items": [{"document": d, "count": int(c)} for d, c in rows]}


@router.get("/reports/probation")
def report_probation(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(Employee)
        .join(User, User.id == Employee.user_id)
        .filter(Employee.is_deleted == False, Employee.lifecycle_state == LifecycleState.ON_PROBATION)  # noqa: E712
        .all()
    )
    return {"items": [
        {
            "id": str(e.id),
            "employee_id": e.employee_id,
            "name": e.user.full_name if e.user else "",
            "joining_date": e.joining_date.isoformat() if e.joining_date else None,
            "confirmation_date": e.confirmation_date.isoformat() if e.confirmation_date else None,
        } for e in rows
    ]}


@router.get("/reports/asset-allocation")
def report_asset_allocation(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(AssetAllocation)
        .filter(AssetAllocation.status == AllocationStatus.ALLOCATED)
        .all()
    )
    return {"total_allocated": len(rows)}


@router.get("/reports/pending-onboarding")
def report_pending_onboarding(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(OnboardingProcess)
        .filter(OnboardingProcess.status == OnboardingStatus.IN_PROGRESS)
        .order_by(OnboardingProcess.target_joining_date.asc().nullslast())
        .all()
    )
    items = []
    for p in rows:
        snap = _employee_snapshot_fields(db, p.employee_id)
        items.append({
            "id": str(p.id),
            "employee_name": snap.get("employee_name"),
            "employee_code": snap.get("employee_code"),
            "department": snap.get("employee_department"),
            "current_stage": p.current_stage.value,
            "progress_pct": p.progress_pct,
            "target_joining_date": p.target_joining_date.isoformat() if p.target_joining_date else None,
        })
    return {"items": items}
