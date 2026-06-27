"""HR Payroll — Monthly Payroll Batches (the pay-run state machine)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.attendance import Attendance
from app.models.hr.leave_encashment import LeaveEncashment
from app.models.hr.leave_type import EncashmentStatus
from app.models.hr.payroll_batch import PayrollBatch, PayrollBatchStatus
from app.models.hr.payslip import Payslip, PayslipStatus
from app.models.hr.payroll_config import PayrollAuditLog, PayrollAuditAction
from app.schemas.hr.payroll import (
    PayrollBatchCreate, PayrollBatchResponse, PayrollBatchListResponse,
    BatchActionBody, BatchDeleteBody, BatchProgress, PayslipListResponse, PayrollAuditListResponse,
    EligibilityRequest, EligibilityResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.payroll.service import (
    next_batch_no, write_audit, generate_batch, can_transition, month_bounds,
    post_adjustments_paid, post_overtime_processed, resolve_eligibility, stale_payslips,
)

router = APIRouter(prefix="/hr/payroll/batches", tags=["HR — Payroll Batches"])

_ACTION_TO_STATUS = {
    "verify": PayrollBatchStatus.VERIFIED,
    "approve": PayrollBatchStatus.APPROVED,
    "release": PayrollBatchStatus.RELEASED,
    "lock": PayrollBatchStatus.LOCKED,
}


def _enrich(b: PayrollBatch, db: Session) -> dict:
    dname = None
    if b.department_id:
        d = db.query(Department.name).filter(Department.id == b.department_id).first()
        dname = d[0] if d else None
    out = {k: getattr(b, k) for k in (
        "id", "batch_no", "period_month", "period_year", "pay_date", "status", "department_id",
        "total_employees", "total_gross", "total_deductions", "total_net", "total_employer_cost",
        "notes", "generated_at", "verified_at", "approved_at", "released_at", "locked_at", "created_at")}
    out["department_name"] = dname
    return out


def _get_batch(db, batch_id) -> PayrollBatch:
    b = db.query(PayrollBatch).filter(PayrollBatch.id == batch_id, PayrollBatch.is_deleted == False).first()  # noqa: E712
    if not b:
        raise HTTPException(404, "Payroll batch not found")
    return b


@router.get("/", response_model=PayrollBatchListResponse)
def list_batches(year: Optional[int] = None, month: Optional[int] = None,
                 status: Optional[PayrollBatchStatus] = None,
                 skip: int = 0, limit: int = Query(50, ge=1, le=200),
                 db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    q = db.query(PayrollBatch).filter(PayrollBatch.is_deleted == False)  # noqa: E712
    if year:
        q = q.filter(PayrollBatch.period_year == year)
    if month:
        q = q.filter(PayrollBatch.period_month == month)
    if status:
        q = q.filter(PayrollBatch.status == status)
    total = q.count()
    items = q.order_by(PayrollBatch.period_year.desc(), PayrollBatch.period_month.desc(),
                       PayrollBatch.created_at.desc()).offset(skip).limit(limit).all()
    return {"items": [_enrich(b, db) for b in items], "total": total}


@router.post("/", response_model=PayrollBatchResponse, status_code=201)
def create_batch(payload: PayrollBatchCreate, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_superuser)):
    dup = db.query(PayrollBatch.id).filter(
        PayrollBatch.period_month == payload.period_month, PayrollBatch.period_year == payload.period_year,
        PayrollBatch.department_id == payload.department_id, PayrollBatch.is_deleted == False,  # noqa: E712
        PayrollBatch.status != PayrollBatchStatus.CANCELLED,
    ).first()
    if dup:
        raise HTTPException(409, "A payroll batch for this period and scope already exists")
    b = PayrollBatch(
        batch_no=next_batch_no(db, payload.period_year, payload.period_month),
        period_month=payload.period_month, period_year=payload.period_year,
        pay_date=payload.pay_date, department_id=payload.department_id, notes=payload.notes,
        status=PayrollBatchStatus.DRAFT, created_by_id=current_user.id,
    )
    db.add(b)
    db.flush()
    write_audit(db, entity_type="BATCH", entity_id=b.id, action=PayrollAuditAction.CREATE,
                batch_id=b.id, actor_id=current_user.id, to_status="DRAFT")
    db.commit()
    db.refresh(b)
    return _enrich(b, db)


@router.post("/eligibility", response_model=EligibilityResponse)
def run_eligibility(payload: EligibilityRequest, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    """Pre-flight roster for a period/scope: who will be paid, who is blocked and
    why, plus final-settlement (exited) flags. Drives the run-wizard preview and
    the post-generate exceptions panel so a run never silently produces 0."""
    return resolve_eligibility(db, payload.period_year, payload.period_month, payload.department_id)


@router.get("/{batch_id}", response_model=PayrollBatchResponse)
def get_batch(batch_id: UUID, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_superuser)):
    return _enrich(_get_batch(db, batch_id), db)


@router.post("/{batch_id}/generate", response_model=PayrollBatchResponse)
def generate(batch_id: UUID, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_superuser)):
    b = _get_batch(db, batch_id)
    try:
        generate_batch(db, b, current_user.id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    db.refresh(b)
    return _enrich(b, db)


def _transition(db, b: PayrollBatch, action: str, actor: User, body: Optional[BatchActionBody]):
    target = _ACTION_TO_STATUS[action]
    if not can_transition(b.status, target):
        raise HTTPException(409, f"Cannot {action} a batch in status {b.status.value}")
    # Freshness gate — never sign off or pay figures that no longer match attendance.
    # A run generated before its period's attendance was finalized (or before later
    # corrections / exit-related absences) carries stale Loss-of-Pay; releasing it
    # would overpay (e.g. a full month to someone who never clocked in). Force a
    # re-generate first. (Re-generate sends the batch back to GENERATED, dropping
    # non-released payslips and rebuilding them from current attendance.)
    if action in ("approve", "release"):
        stale = stale_payslips(db, b)
        if stale:
            sample = ", ".join(
                f"{s['employee_code'] or s['employee_id'][:8]} (LOP {s['stored_lop']:g}→{s['current_lop']:g})"
                for s in stale[:5]
            )
            more = f" +{len(stale) - 5} more" if len(stale) > 5 else ""
            raise HTTPException(
                409,
                f"Cannot {action}: {len(stale)} payslip(s) have Loss-of-Pay that no longer "
                f"matches attendance — re-generate the run first. e.g. {sample}{more}",
            )
    prev = b.status.value
    now = datetime.now(timezone.utc)
    b.status = target
    if action == "verify":
        b.verified_at, b.verified_by_id = now, actor.id
    elif action == "approve":
        b.approved_at, b.approved_by_id = now, actor.id
    elif action == "release":
        b.released_at, b.released_by_id = now, actor.id
        if body and body.pay_date:
            b.pay_date = body.pay_date
        db.query(Payslip).filter(Payslip.batch_id == b.id,
                                 Payslip.status != PayslipStatus.CANCELLED).update(
            {"status": PayslipStatus.RELEASED}, synchronize_session=False)
        _post_encashment(db, b, actor.id)
        post_adjustments_paid(db, b, actor.id)
        post_overtime_processed(db, b, actor.id)
    elif action == "lock":
        b.locked_at, b.locked_by_id = now, actor.id
        start, end = month_bounds(b.period_year, b.period_month)
        aq = db.query(Attendance).filter(Attendance.date >= start, Attendance.date <= end,
                                         Attendance.is_deleted == False)  # noqa: E712
        if b.department_id:
            aq = aq.filter(Attendance.employee_id.in_(
                db.query(Employee.id).filter(Employee.department_id == b.department_id)))
        aq.update({"is_locked": True}, synchronize_session=False)
    write_audit(db, entity_type="BATCH", entity_id=b.id, action=PayrollAuditAction[action.upper()],
                batch_id=b.id, actor_id=actor.id, from_status=prev, to_status=target.value,
                note=body.note if body else None)
    db.commit()
    if action == "release":
        try:
            from app.utils.hr.notify import dispatch
            period = f"{int(b.period_month):02d}/{b.period_year}"
            rows = (db.query(Employee.user_id)
                    .join(Payslip, Payslip.employee_id == Employee.id)
                    .filter(Payslip.batch_id == b.id, Payslip.status == PayslipStatus.RELEASED)
                    .all())
            for (uid,) in rows:
                dispatch(db, "PAYROLL_PROCESSED", uid, context={
                    "title": "Payroll processed",
                    "message": f"Payroll for {period} has been processed — your payslip is ready.",
                    "action_url": "/user/self-service/payslips",
                })
            db.commit()
        except Exception:
            db.rollback()
    db.refresh(b)
    return _enrich(b, db)


def _post_encashment(db, b: PayrollBatch, actor_id):
    """On release, mark approved unpaid encashments as paid against this batch."""
    emp_ids = [r[0] for r in db.query(Payslip.employee_id).filter(Payslip.batch_id == b.id).all()]
    if not emp_ids:
        return
    rows = db.query(LeaveEncashment).filter(
        LeaveEncashment.employee_id.in_(emp_ids), LeaveEncashment.is_deleted == False,  # noqa: E712
        LeaveEncashment.status == EncashmentStatus.APPROVED, LeaveEncashment.paid_at.is_(None),
    ).all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.paid_at = now
        r.paid_by_id = actor_id
        r.payroll_ref = b.batch_no


@router.post("/{batch_id}/verify", response_model=PayrollBatchResponse)
def verify(batch_id: UUID, body: Optional[BatchActionBody] = None, db: Session = Depends(get_db),
           current_user: User = Depends(get_current_superuser)):
    return _transition(db, _get_batch(db, batch_id), "verify", current_user, body)


@router.post("/{batch_id}/approve", response_model=PayrollBatchResponse)
def approve(batch_id: UUID, body: Optional[BatchActionBody] = None, db: Session = Depends(get_db),
            current_user: User = Depends(get_current_superuser)):
    return _transition(db, _get_batch(db, batch_id), "approve", current_user, body)


@router.post("/{batch_id}/release", response_model=PayrollBatchResponse)
def release(batch_id: UUID, body: Optional[BatchActionBody] = None, db: Session = Depends(get_db),
            current_user: User = Depends(get_current_superuser)):
    return _transition(db, _get_batch(db, batch_id), "release", current_user, body)


@router.post("/{batch_id}/lock", response_model=PayrollBatchResponse)
def lock(batch_id: UUID, body: Optional[BatchActionBody] = None, db: Session = Depends(get_db),
         current_user: User = Depends(get_current_superuser)):
    return _transition(db, _get_batch(db, batch_id), "lock", current_user, body)


@router.post("/{batch_id}/reopen", response_model=PayrollBatchResponse)
def reopen(batch_id: UUID, db: Session = Depends(get_db),
           current_user: User = Depends(get_current_superuser)):
    b = _get_batch(db, batch_id)
    if b.status not in (PayrollBatchStatus.GENERATED, PayrollBatchStatus.VERIFIED):
        raise HTTPException(409, f"Cannot reopen a batch in status {b.status.value}")
    prev = b.status.value
    b.status = PayrollBatchStatus.DRAFT
    write_audit(db, entity_type="BATCH", entity_id=b.id, action=PayrollAuditAction.REOPEN,
                batch_id=b.id, actor_id=current_user.id, from_status=prev, to_status="DRAFT")
    db.commit()
    db.refresh(b)
    return _enrich(b, db)


@router.post("/{batch_id}/return", response_model=PayrollBatchResponse)
def return_for_recalc(batch_id: UUID, body: Optional[BatchActionBody] = None, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_superuser)):
    b = _get_batch(db, batch_id)
    if b.status not in (PayrollBatchStatus.VERIFIED, PayrollBatchStatus.APPROVED):
        raise HTTPException(409, f"Cannot return a batch in status {b.status.value}")
    prev = b.status.value
    b.status = PayrollBatchStatus.GENERATED
    write_audit(db, entity_type="BATCH", entity_id=b.id, action=PayrollAuditAction.RETURN,
                batch_id=b.id, actor_id=current_user.id, from_status=prev, to_status="GENERATED",
                note=body.note if body else None)
    db.commit()
    db.refresh(b)
    return _enrich(b, db)


@router.post("/{batch_id}/cancel", response_model=PayrollBatchResponse)
def cancel(batch_id: UUID, body: Optional[BatchActionBody] = None, db: Session = Depends(get_db),
           current_user: User = Depends(get_current_superuser)):
    b = _get_batch(db, batch_id)
    if b.status in (PayrollBatchStatus.RELEASED, PayrollBatchStatus.LOCKED, PayrollBatchStatus.CANCELLED):
        raise HTTPException(409, f"Cannot cancel a batch in status {b.status.value}")
    prev = b.status.value
    b.status = PayrollBatchStatus.CANCELLED
    b.cancelled_at = datetime.now(timezone.utc)
    b.cancelled_by_id = current_user.id
    b.cancel_reason = body.note if body else None
    write_audit(db, entity_type="BATCH", entity_id=b.id, action=PayrollAuditAction.CANCEL,
                batch_id=b.id, actor_id=current_user.id, from_status=prev, to_status="CANCELLED",
                note=body.note if body else None)
    db.commit()
    db.refresh(b)
    return _enrich(b, db)


@router.post("/{batch_id}/delete")
def delete_batch(batch_id: UUID, body: BatchDeleteBody, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_superuser)):
    """Soft-delete a pay run (retained in the DB for audit) and cascade-hide its
    payslips. RELEASED/LOCKED runs are part of the disbursement record and must
    be cancelled/returned instead — never deleted."""
    b = _get_batch(db, batch_id)
    if b.status in (PayrollBatchStatus.RELEASED, PayrollBatchStatus.LOCKED):
        raise HTTPException(
            409,
            f"A {b.status.value.lower()} pay run is part of the disbursement record and cannot be "
            f"deleted — cancel or return it instead.",
        )
    reason = body.reason.strip()
    extra = (body.note or "").strip()
    prev = b.status.value
    b.is_deleted = True
    b.last_updated_by_id = current_user.id
    b.notes = f"DELETED: {reason}" + (f" — {extra}" if extra else "")
    # cascade soft-delete the run's payslips so they vanish from payslip lists too
    db.query(Payslip).filter(Payslip.batch_id == b.id, Payslip.is_deleted == False).update(  # noqa: E712
        {Payslip.is_deleted: True}, synchronize_session=False)
    write_audit(db, entity_type="BATCH", entity_id=b.id, action=PayrollAuditAction.DELETE,
                batch_id=b.id, actor_id=current_user.id, from_status=prev, note=reason)
    db.commit()
    return {"status": "deleted", "id": str(b.id), "batch_no": b.batch_no}


@router.get("/{batch_id}/payslips", response_model=PayslipListResponse)
def batch_payslips(batch_id: UUID, q: Optional[str] = None, skip: int = 0,
                   limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    _get_batch(db, batch_id)
    query = db.query(Payslip).options(joinedload(Payslip.employee)).filter(
        Payslip.batch_id == batch_id, Payslip.is_deleted == False)  # noqa: E712
    total = query.count()
    rows = query.order_by(Payslip.net_pay.desc()).offset(skip).limit(limit).all()
    items = []
    for s in rows:
        emp = s.employee
        uname = None
        if emp and emp.user:
            uname = getattr(emp.user, "full_name", None) or getattr(emp.user, "email", None)
        items.append({
            "id": s.id, "payslip_no": s.payslip_no, "period_month": s.period_month,
            "period_year": s.period_year, "status": s.status, "gross_earnings": s.gross_earnings,
            "total_deductions": s.total_deductions, "net_pay": s.net_pay, "employee_id": s.employee_id,
            "employee_name": uname, "employee_code": emp.employee_id if emp else None,
            "department_name": None,
        })
    return {"items": items, "total": total}


@router.get("/{batch_id}/progress", response_model=BatchProgress)
def batch_progress(batch_id: UUID, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    b = _get_batch(db, batch_id)
    done = db.query(Payslip.id).filter(Payslip.batch_id == batch_id,
                                       Payslip.is_deleted == False).count()  # noqa: E712
    total = max(done, b.total_employees or 0)
    pct = round((done / total) * 100, 1) if total else 0.0
    return BatchProgress(status=b.status, done=done, total=total, pct=pct)


_BF_MONTHS = ["", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]


def _bank_rows(b, db: Session):
    """Per-payee disbursement rows for a batch, with bank-detail validity flags."""
    slips = db.query(Payslip).options(joinedload(Payslip.employee)).filter(
        Payslip.batch_id == b.id, Payslip.is_deleted == False).all()  # noqa: E712
    dept_ids = {s.employee.department_id for s in slips if s.employee and s.employee.department_id}
    dept_map = {}
    if dept_ids:
        for did, dname in db.query(Department.id, Department.name).filter(Department.id.in_(dept_ids)).all():
            dept_map[did] = dname
    rows = []
    for s in slips:
        emp = s.employee
        name = None
        if emp and emp.user:
            name = getattr(emp.user, "full_name", None) or getattr(emp.user, "email", None)
        rows.append({
            "code": emp.employee_id if emp else "",
            "name": name or "",
            "department": (dept_map.get(emp.department_id) if emp else "") or "",
            "bank": s.bank_name or "",
            "account": s.account_number or "",
            "ifsc": s.ifsc or "",
            "gross": float(s.gross_earnings or 0),
            "deductions": float(s.total_deductions or 0),
            "net": float(s.net_pay or 0),
            "valid": bool(s.account_number and s.ifsc),
        })
    rows.sort(key=lambda r: r["code"])
    return rows


def _bank_summary(rows):
    """Aggregate disbursement figures shared by the CSV/Excel/PDF exporters."""
    by_bank = {}
    for r in rows:
        k = r["bank"] or "Unspecified"
        e = by_bank.setdefault(k, {"bank": k, "count": 0, "amount": 0.0})
        e["count"] += 1
        e["amount"] += r["net"]
    return {
        "total_payees": len(rows),
        "ready": sum(1 for r in rows if r["valid"]),
        "missing": sum(1 for r in rows if not r["valid"]),
        "total_gross": sum(r["gross"] for r in rows),
        "total_deductions": sum(r["deductions"] for r in rows),
        "total_net": sum(r["net"] for r in rows),
        "by_bank": sorted(by_bank.values(), key=lambda x: -x["amount"]),
    }


def _bf_inr(v) -> str:
    return f'{float(v or 0):,.2f}'


def _bank_xlsx(b, rows) -> bytes:
    """Detailed, styled 'Salary Disbursement Advice' workbook (xlsxwriter):
    KPI strip → per-payee table (gross/deductions/net + validity) → totals →
    bank-wise breakdown → signatory block."""
    import xlsxwriter  # lazy
    out = io.BytesIO()
    wb = xlsxwriter.Workbook(out, {"in_memory": True})
    ws = wb.add_worksheet("Disbursement")
    ws.hide_gridlines(2)
    MONEY = '"₹"#,##0.00'

    f_title = wb.add_format({"bold": True, "font_size": 17, "font_color": "#8a5a06"})
    f_sub = wb.add_format({"font_size": 10, "font_color": "#8a6d3b"})
    f_mlbl = wb.add_format({"font_size": 8, "bold": True, "font_color": "#b39a5e"})
    f_mval = wb.add_format({"font_size": 11, "font_color": "#1f1710", "bold": True})
    f_kpi_lbl = wb.add_format({"font_size": 8, "bold": True, "font_color": "#b39a5e", "bg_color": "#faf5e9", "border": 1, "border_color": "#efe2c4"})
    f_kpi_val = wb.add_format({"font_size": 13, "bold": True, "font_color": "#1f1710", "bg_color": "#faf5e9", "border": 1, "border_color": "#efe2c4"})
    f_kpi_money = wb.add_format({"font_size": 13, "bold": True, "font_color": "#047857", "bg_color": "#ecfdf5", "border": 1, "border_color": "#bfe6cf", "num_format": MONEY})
    f_sec = wb.add_format({"bold": True, "font_size": 11, "font_color": "#8a5a06"})
    f_hdr = wb.add_format({"bold": True, "font_color": "#fff8e6", "bg_color": "#b8860b", "border": 1, "border_color": "#9a6f08", "align": "left", "valign": "vcenter"})
    f_hdr_r = wb.add_format({"bold": True, "font_color": "#fff8e6", "bg_color": "#b8860b", "border": 1, "border_color": "#9a6f08", "align": "right", "valign": "vcenter"})
    f_cell = wb.add_format({"font_size": 10, "border": 1, "border_color": "#ecdfbf", "valign": "vcenter"})
    f_mono = wb.add_format({"font_size": 10, "border": 1, "border_color": "#ecdfbf", "font_name": "Consolas"})
    f_money = wb.add_format({"font_size": 10, "border": 1, "border_color": "#ecdfbf", "num_format": MONEY, "align": "right"})
    f_money_mut = wb.add_format({"font_size": 10, "border": 1, "border_color": "#ecdfbf", "num_format": MONEY, "align": "right", "font_color": "#8a6d3b"})
    f_ok = wb.add_format({"font_size": 9, "border": 1, "border_color": "#ecdfbf", "font_color": "#047857", "bold": True, "align": "center"})
    f_miss = wb.add_format({"font_size": 9, "border": 1, "border_color": "#f0c9b6", "bg_color": "#fde2d6", "font_color": "#9a3412", "bold": True, "align": "center"})
    f_miss_cell = wb.add_format({"font_size": 10, "border": 1, "border_color": "#f0c9b6", "bg_color": "#fde2d6", "font_color": "#9a3412"})
    f_miss_money = wb.add_format({"font_size": 10, "border": 1, "border_color": "#f0c9b6", "bg_color": "#fde2d6", "font_color": "#9a3412", "num_format": MONEY, "align": "right"})
    f_tot_lbl = wb.add_format({"bold": True, "font_size": 11, "font_color": "#1f1710", "bg_color": "#faf5e9", "border": 1, "border_color": "#e3cf9f", "align": "right"})
    f_tot_money = wb.add_format({"bold": True, "font_size": 11, "font_color": "#8a5a06", "bg_color": "#faf5e9", "border": 1, "border_color": "#e3cf9f", "num_format": MONEY, "align": "right"})
    f_sig = wb.add_format({"font_size": 9, "font_color": "#8a6d3b", "top": 1, "top_color": "#b39a5e", "align": "center"})
    f_foot = wb.add_format({"font_size": 8, "font_color": "#b0a48c", "italic": True})

    s = _bank_summary(rows)
    period = f"{_BF_MONTHS[b.period_month]} {b.period_year}"
    gen = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    pay_date = b.pay_date.strftime("%d %b %Y") if b.pay_date else "—"

    ws.merge_range("A1:D1", "Fourreck Technologies", f_title)
    ws.merge_range("A2:D2", "Salary Disbursement Advice", f_sub)
    ws.write("G1", "BATCH", f_mlbl); ws.write("H1", b.batch_no, f_mval)
    ws.write("G2", "PERIOD", f_mlbl); ws.write("H2", period, f_mval)
    ws.write("J1", "STATUS", f_mlbl); ws.write("K1", b.status.value, f_mval)
    ws.write("J2", "PAY DATE", f_mlbl); ws.write("K2", pay_date, f_mval)

    # KPI strip (rows 4-5)
    ws.write("A4", "TOTAL PAYEES", f_kpi_lbl); ws.write_number("A5", s["total_payees"], f_kpi_val)
    ws.write("B4", "VALID", f_kpi_lbl); ws.write_number("B5", s["ready"], f_kpi_val)
    ws.write("C4", "MISSING", f_kpi_lbl); ws.write_number("C5", s["missing"], f_kpi_val)
    ws.merge_range("D4:E4", "GROSS", f_kpi_lbl); ws.merge_range("D5:E5", s["total_gross"], f_kpi_money)
    ws.merge_range("F4:G4", "DEDUCTIONS", f_kpi_lbl); ws.merge_range("F5:G5", s["total_deductions"], f_kpi_money)
    ws.merge_range("H4:K4", "NET DISBURSEMENT", f_kpi_lbl); ws.merge_range("H5:K5", s["total_net"], f_kpi_money)

    headers = ["#", "Emp Code", "Employee Name", "Department", "Bank", "Account No", "IFSC", "Gross", "Deductions", "Net Amount", "Status"]
    right_cols = {"Gross", "Deductions", "Net Amount"}
    hr = 6
    for c, h in enumerate(headers):
        ws.write(hr, c, h, f_hdr_r if h in right_cols else f_hdr)
    ws.freeze_panes(hr + 1, 0)
    ws.autofilter(hr, 0, hr + len(rows), len(headers) - 1)

    r = hr + 1
    for i, row in enumerate(rows, start=1):
        miss = not row["valid"]
        base = f_miss_cell if miss else f_cell
        mono = f_miss_cell if miss else f_mono
        net_f = f_miss_money if miss else f_money
        ws.write_number(r, 0, i, base)
        ws.write(r, 1, row["code"], mono)
        ws.write(r, 2, row["name"], base)
        ws.write(r, 3, row["department"], base)
        ws.write(r, 4, row["bank"], base)
        ws.write(r, 5, row["account"], mono)
        ws.write(r, 6, row["ifsc"], mono)
        ws.write_number(r, 7, row["gross"], f_money_mut)
        ws.write_number(r, 8, row["deductions"], f_money_mut)
        ws.write_number(r, 9, row["net"], net_f)
        ws.write(r, 10, "READY" if row["valid"] else "MISSING", f_ok if row["valid"] else f_miss)
        r += 1

    ws.merge_range(r, 0, r, 6, "Totals", f_tot_lbl)
    ws.write_number(r, 7, s["total_gross"], f_tot_money)
    ws.write_number(r, 8, s["total_deductions"], f_tot_money)
    ws.write_number(r, 9, s["total_net"], f_tot_money)
    ws.write(r, 10, "", f_tot_lbl)
    r += 3

    # bank-wise breakdown
    ws.write(r, 0, "Bank-wise breakdown", f_sec); r += 1
    for c, h in enumerate(["Bank", "Payees", "Amount"]):
        ws.write(r, c, h, f_hdr_r if h == "Amount" else f_hdr)
    r += 1
    for bk in s["by_bank"]:
        ws.write(r, 0, bk["bank"], f_cell)
        ws.write_number(r, 1, bk["count"], f_cell)
        ws.write_number(r, 2, bk["amount"], f_money)
        r += 1
    r += 2

    # signatory block
    for c, lbl in zip((1, 4, 7), ("Prepared by", "Checked by", "Approved by")):
        ws.write(r, c, "", f_sig); ws.write(r, c + 1, "", f_sig)
        ws.write(r + 1, c, lbl, f_foot)
    r += 3
    ws.write(r, 0, f"Confidential · system-generated on {gen} · does not require a signature to be valid in the payroll system.", f_foot)

    for col, w in enumerate([4, 12, 22, 16, 16, 16, 13, 13, 13, 14, 10]):
        ws.set_column(col, col, w)
    ws.set_row(hr, 22)
    wb.close()
    return out.getvalue()


def _bank_pdf(b, rows) -> bytes:
    """Ultra-modern 'Salary Disbursement Advice' PDF (WeasyPrint)."""
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # noqa: WPS433 (lazy — needs GTK on PATH)

    s = _bank_summary(rows)
    period = f"{_BF_MONTHS[b.period_month]} {b.period_year}"
    gen = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    pay_date = b.pay_date.strftime("%d %b %Y") if b.pay_date else "—"
    ready_pct = round(s["ready"] / s["total_payees"] * 100) if s["total_payees"] else 0

    bank_rows = "".join(
        f'<tr><td>{bk["bank"]}</td><td class="c">{bk["count"]}</td>'
        f'<td class="amt">₹ {_bf_inr(bk["amount"])}</td>'
        f'<td class="barcell"><span class="bar" style="width:{max(4, round(bk["amount"]/s["total_net"]*100)) if s["total_net"] else 0}%"></span></td></tr>'
        for bk in s["by_bank"]
    )
    payee_rows = "".join(
        f'<tr class="{"miss" if not r["valid"] else ""}">'
        f'<td class="c">{i}</td><td class="mono">{r["code"]}</td><td>{r["name"]}</td>'
        f'<td>{r["department"] or "—"}</td><td>{r["bank"] or "—"}</td>'
        f'<td class="mono">{r["account"] or "—"}</td>'
        f'<td class="mono">{r["ifsc"] or "—"}</td>'
        f'<td class="amt">₹ {_bf_inr(r["net"])}</td>'
        f'<td class="c"><span class="pill {"ok" if r["valid"] else "no"}">{"READY" if r["valid"] else "MISSING"}</span></td></tr>'
        for i, r in enumerate(rows, start=1)
    )
    miss_note = (
        f'<div class="warn">⚠ {s["missing"]} payee(s) are missing bank account / IFSC and are flagged below. '
        f'Correct their profiles before uploading the NEFT file to the bank.</div>' if s["missing"] else ""
    )

    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
    @page {{ size: A4; margin: 13mm 12mm 16mm;
      @bottom-left {{ content: "Confidential · Fourreck Technologies — Disbursement Advice"; font-size: 7.5px; color: #b9a982; }}
      @bottom-right {{ content: "Page " counter(page) " of " counter(pages); font-size: 7.5px; color: #b9a982; }}
    }}
    * {{ font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; box-sizing: border-box; }}
    body {{ color: #1f1710; font-size: 10px; margin: 0; }}
    .wm {{ position: fixed; top: 40%; left: -6%; right: 0; text-align: center; z-index: 0; transform: rotate(-22deg);
      font-size: 80px; font-weight: 800; letter-spacing: 12px; color: rgba(184,134,11,0.045); text-transform: uppercase; }}
    .masthead {{ position: relative; overflow: hidden; border-radius: 16px; padding: 17px 22px; color: #fff8e6;
      background: linear-gradient(120deg, #8a5a06 0%, #b8860b 42%, #f59e0b 100%); display: flex; justify-content: space-between; align-items: center; }}
    .masthead::after {{ content: ""; position: absolute; top: -40px; right: -30px; width: 170px; height: 170px; border-radius: 50%;
      background: radial-gradient(circle, rgba(255,255,255,0.18), transparent 65%); }}
    .brand {{ font-size: 21px; font-weight: 800; }} .tag {{ margin-top: 2px; font-size: 9px; text-transform: uppercase; letter-spacing: 3px; color: #ffe9b8; }}
    .mh-right {{ position: relative; text-align: right; z-index: 1; }}
    .mh-right .no {{ font-size: 15px; font-weight: 800; }} .mh-right .per {{ font-size: 10px; color: #ffedc7; }}
    .mh-right .meta2 {{ font-size: 9px; color: #ffe9b8; margin-top: 4px; }}
    .kpis {{ display: flex; gap: 10px; margin-top: 12px; }}
    .kpi {{ flex: 1; border: 1px solid #efe2c4; border-radius: 12px; padding: 10px 12px; background: #fffdf8; }}
    .kpi.net {{ background: #ecfdf5; border-color: #bfe6cf; }}
    .kpi .l {{ font-size: 8px; text-transform: uppercase; letter-spacing: 1px; color: #b39a5e; }}
    .kpi .v {{ font-size: 17px; font-weight: 800; color: #1f1710; margin-top: 2px; }} .kpi.net .v {{ color: #047857; }}
    .kpi .v small {{ font-size: 10px; color: #8a6d3b; font-weight: 600; }}
    .warn {{ margin-top: 11px; padding: 9px 13px; border-radius: 10px; background: #fff4e0; border: 1px solid #f1c98a; color: #9a3412; font-size: 9.5px; }}
    h3 {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #8a5a06; margin: 16px 0 7px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    .banktbl td, .banktbl th {{ padding: 6px 10px; border-bottom: 1px solid #f0e8d6; font-size: 9.5px; }}
    .banktbl th {{ text-align: left; color: #b39a5e; text-transform: uppercase; font-size: 8px; letter-spacing: 1px; }}
    .barcell {{ width: 34%; }} .bar {{ display: inline-block; height: 7px; border-radius: 999px; background: linear-gradient(90deg, #b8860b, #10b981); }}
    .payeetbl {{ border: 1px solid #e8dcc0; border-radius: 10px; overflow: hidden; }}
    .payeetbl thead th {{ background: #1f1710; color: #fbbf24; padding: 7px 9px; font-size: 8px; text-transform: uppercase; letter-spacing: .6px; text-align: left; }}
    .payeetbl thead th.amt, .payeetbl thead th.c {{ text-align: right; }} .payeetbl thead th.c {{ text-align: center; }}
    .payeetbl td {{ padding: 6px 9px; border-bottom: 1px solid #f4ecda; font-size: 9px; }}
    .payeetbl tr:nth-child(even) td {{ background: #fffdf6; }}
    .payeetbl tr.miss td {{ background: #fde2d6 !important; color: #9a3412; }}
    .mono {{ font-family: 'Consolas', monospace; }} .amt {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 700; }} .c {{ text-align: center; }}
    .pill {{ font-size: 7.5px; font-weight: 800; padding: 2px 7px; border-radius: 5px; }}
    .pill.ok {{ background: #d1fae5; color: #047857; }} .pill.no {{ background: #ffd9c7; color: #9a3412; }}
    tfoot td {{ font-weight: 800; background: #faf5e9; border-top: 2px solid #e3cf9f; font-size: 10px; }}
    .sign {{ display: flex; justify-content: space-between; margin-top: 26px; }}
    .sign div {{ width: 28%; border-top: 1px solid #c9b07a; padding-top: 5px; text-align: center; font-size: 9px; color: #8a6d3b; }}
    </style></head><body>
      <div class="wm">Fourreck</div>
      <div class="masthead">
        <div><div class="brand">Fourreck Technologies</div><div class="tag">Salary Disbursement Advice</div></div>
        <div class="mh-right"><div class="no">{b.batch_no}</div><div class="per">{period}</div>
          <div class="meta2">Status {b.status.value} · Pay date {pay_date}</div></div>
      </div>

      <div class="kpis">
        <div class="kpi"><div class="l">Total Payees</div><div class="v">{s["total_payees"]} <small>· {ready_pct}% ready</small></div></div>
        <div class="kpi"><div class="l">Valid / Missing</div><div class="v">{s["ready"]} <small>/ {s["missing"]} missing</small></div></div>
        <div class="kpi"><div class="l">Gross · Deductions</div><div class="v" style="font-size:13px">₹ {_bf_inr(s["total_gross"])} <small>− ₹ {_bf_inr(s["total_deductions"])}</small></div></div>
        <div class="kpi net"><div class="l">Net Disbursement</div><div class="v">₹ {_bf_inr(s["total_net"])}</div></div>
      </div>
      {miss_note}

      <h3>Bank-wise breakdown</h3>
      <table class="banktbl"><thead><tr><th>Bank</th><th>Payees</th><th style="text-align:right">Amount</th><th></th></tr></thead>
        <tbody>{bank_rows}</tbody></table>

      <h3>Payee disbursement register</h3>
      <table class="payeetbl"><thead><tr>
        <th class="c">#</th><th>Emp Code</th><th>Name</th><th>Department</th><th>Bank</th><th>Account</th><th>IFSC</th>
        <th class="amt">Net Amount</th><th class="c">Status</th></tr></thead>
        <tbody>{payee_rows}</tbody>
        <tfoot><tr><td colspan="7">Total net disbursement</td><td class="amt">₹ {_bf_inr(s["total_net"])}</td><td></td></tr></tfoot>
      </table>

      <div class="sign"><div>Prepared by</div><div>Checked by</div><div>Approved by</div></div>
    </body></html>"""

    return HTML(string=html).write_pdf()


@router.get("/{batch_id}/bank-file/summary")
def bank_file_summary(batch_id: UUID, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_superuser)):
    """Disbursement readiness: payees ready vs missing bank details + bank-wise split."""
    b = _get_batch(db, batch_id)
    rows = _bank_rows(b, db)
    ready = sum(1 for r in rows if r["valid"])
    by_bank: dict = {}
    for r in rows:
        k = r["bank"] or "Unspecified"
        e = by_bank.setdefault(k, {"bank": k, "count": 0, "amount": 0.0})
        e["count"] += 1
        e["amount"] += r["net"]
    return {
        "batch_no": b.batch_no, "status": b.status.value,
        "period_month": b.period_month, "period_year": b.period_year,
        "pay_date": b.pay_date.isoformat() if b.pay_date else None,
        "total_payees": len(rows), "ready": ready, "missing": len(rows) - ready,
        "total_net": sum(r["net"] for r in rows),
        "missing_codes": [r["code"] for r in rows if not r["valid"]][:50],
        "by_bank": sorted(by_bank.values(), key=lambda x: -x["amount"]),
    }


@router.get("/{batch_id}/bank-file")
def bank_file(batch_id: UUID, fmt: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
              db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    b = _get_batch(db, batch_id)
    if b.status not in (PayrollBatchStatus.APPROVED, PayrollBatchStatus.RELEASED, PayrollBatchStatus.LOCKED):
        raise HTTPException(409, "Bank file is available only after the run is approved/released")
    rows = _bank_rows(b, db)

    if fmt == "xlsx":
        data = _bank_xlsx(b, rows)
        return Response(content=data,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="disbursement-{b.batch_no}.xlsx"'})

    if fmt == "pdf":
        try:
            data = _bank_pdf(b, rows)
        except OSError as e:
            if any(k in str(e) for k in ("libgobject", "libpango", "cannot load library")):
                raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run `python vendor/setup_gtk.py`")
            raise
        return Response(content=data, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="disbursement-{b.batch_no}.pdf"'})

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Employee Code", "Employee Name", "Department", "Bank Name", "Account Number", "IFSC", "Net Amount"])
    for row in rows:
        w.writerow([row["code"], row["name"], row["department"], row["bank"], row["account"], row["ifsc"], f'{row["net"]:.2f}'])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="bank-{b.batch_no}.csv"'})


@router.get("/{batch_id}/audit", response_model=PayrollAuditListResponse)
def batch_audit(batch_id: UUID, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    rows = db.query(PayrollAuditLog).filter(PayrollAuditLog.batch_id == batch_id).order_by(
        PayrollAuditLog.created_at.desc()).all()
    return {"items": [{
        "id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id, "action": r.action,
        "batch_id": r.batch_id, "actor_id": r.actor_id, "from_status": r.from_status,
        "to_status": r.to_status, "note": r.note, "created_at": r.created_at, "actor_name": None,
    } for r in rows], "total": len(rows)}
