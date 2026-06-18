"""HR Training & Development — Budget allocation + cost items + summary.

``spent_amount`` / ``committed_amount`` on a budget are recomputed from its items
on every item write (committed items count toward committed; the rest toward spent).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.department import Department
from app.models.hr.training import TrainingProgram
from app.models.hr.trainer import Trainer
from app.models.hr.training_budget import (
    TrainingBudget, TrainingBudgetItem, BudgetCostType,
)
from app.schemas.hr.training_budget import (
    TrainingBudgetCreate, TrainingBudgetUpdate, TrainingBudgetResponse,
    TrainingBudgetItemCreate, TrainingBudgetItemResponse,
    BudgetSummaryResponse, BudgetSummaryRow, BudgetCostTypeRow,
)
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Budget"])


def _dept_name(db: Session, did) -> Optional[str]:
    if not did:
        return None
    r = db.query(Department.name).filter(Department.id == did).first()
    return r[0] if r else None


def _recompute(db: Session, budget: TrainingBudget) -> None:
    spent = db.query(func.coalesce(func.sum(TrainingBudgetItem.amount), 0)).filter(
        TrainingBudgetItem.budget_id == budget.id, TrainingBudgetItem.is_committed == False,  # noqa: E712
    ).scalar() or 0
    committed = db.query(func.coalesce(func.sum(TrainingBudgetItem.amount), 0)).filter(
        TrainingBudgetItem.budget_id == budget.id, TrainingBudgetItem.is_committed == True,  # noqa: E712
    ).scalar() or 0
    budget.spent_amount = spent
    budget.committed_amount = committed


def _budget_resp(db: Session, b: TrainingBudget) -> TrainingBudgetResponse:
    ic = db.query(func.count(TrainingBudgetItem.id)).filter(TrainingBudgetItem.budget_id == b.id).scalar() or 0
    alloc = Decimal(str(b.allocated_amount or 0))
    used = Decimal(str(b.spent_amount or 0)) + Decimal(str(b.committed_amount or 0))
    remaining = alloc - used
    util = float(round(used / alloc * 100, 1)) if alloc > 0 else 0.0
    return TrainingBudgetResponse(
        id=b.id, name=b.name, period_type=b.period_type, fiscal_year=b.fiscal_year,
        period_index=b.period_index, department_id=b.department_id,
        department_name=_dept_name(db, b.department_id), allocated_amount=b.allocated_amount,
        currency=b.currency, spent_amount=b.spent_amount, committed_amount=b.committed_amount,
        remaining=remaining, utilization_pct=util, item_count=int(ic), is_active=b.is_active,
        created_at=b.created_at,
    )


@router.get("/budgets", response_model=List[TrainingBudgetResponse])
def list_budgets(
    fiscal_year: Optional[int] = None,
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(TrainingBudget).filter(TrainingBudget.is_deleted == False)  # noqa: E712
    if fiscal_year:
        q = q.filter(TrainingBudget.fiscal_year == fiscal_year)
    if department_id:
        q = q.filter(TrainingBudget.department_id == department_id)
    rows = q.order_by(TrainingBudget.fiscal_year.desc(), TrainingBudget.created_at.desc()).all()
    return [_budget_resp(db, b) for b in rows]


@router.post("/budgets", response_model=TrainingBudgetResponse, status_code=http_status.HTTP_201_CREATED)
def create_budget(
    payload: TrainingBudgetCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    b = TrainingBudget(**payload.model_dump(), created_by_id=admin.id)
    db.add(b)
    db.flush()
    write_training_audit(db, entity_type="BUDGET", entity_id=b.id,
                         action=TrainingAuditAction.CREATE, actor_id=admin.id, note=b.name)
    db.commit()
    db.refresh(b)
    return _budget_resp(db, b)


@router.patch("/budgets/{budget_id}", response_model=TrainingBudgetResponse)
def update_budget(
    budget_id: UUID,
    payload: TrainingBudgetUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    b = db.query(TrainingBudget).filter(TrainingBudget.id == budget_id, TrainingBudget.is_deleted == False).first()  # noqa: E712
    if not b:
        raise HTTPException(404, "Budget not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    write_training_audit(db, entity_type="BUDGET", entity_id=b.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id)
    db.commit()
    db.refresh(b)
    return _budget_resp(db, b)


@router.delete("/budgets/{budget_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_budget(
    budget_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    b = db.query(TrainingBudget).filter(TrainingBudget.id == budget_id, TrainingBudget.is_deleted == False).first()  # noqa: E712
    if not b:
        raise HTTPException(404, "Budget not found")
    b.is_deleted = True
    write_training_audit(db, entity_type="BUDGET", entity_id=b.id,
                         action=TrainingAuditAction.DELETE, actor_id=admin.id)
    db.commit()


# ─────────────────────────── items ───────────────────────────

def _item_resp(db: Session, it: TrainingBudgetItem) -> TrainingBudgetItemResponse:
    pn = db.query(TrainingProgram.name).filter(TrainingProgram.id == it.program_id).first() if it.program_id else None
    tn = db.query(Trainer.name).filter(Trainer.id == it.trainer_id).first() if it.trainer_id else None
    return TrainingBudgetItemResponse(
        id=it.id, budget_id=it.budget_id, program_id=it.program_id,
        program_name=pn[0] if pn else None, trainer_id=it.trainer_id, trainer_name=tn[0] if tn else None,
        title=it.title, amount=it.amount, cost_type=it.cost_type, is_committed=it.is_committed,
        incurred_date=it.incurred_date, notes=it.notes, created_at=it.created_at,
    )


@router.get("/budgets/{budget_id}/items", response_model=List[TrainingBudgetItemResponse])
def list_items(
    budget_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = db.query(TrainingBudgetItem).filter(TrainingBudgetItem.budget_id == budget_id).order_by(TrainingBudgetItem.created_at.desc()).all()
    return [_item_resp(db, it) for it in rows]


@router.post("/budgets/{budget_id}/items", response_model=TrainingBudgetItemResponse, status_code=http_status.HTTP_201_CREATED)
def add_item(
    budget_id: UUID,
    payload: TrainingBudgetItemCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    b = db.query(TrainingBudget).filter(TrainingBudget.id == budget_id, TrainingBudget.is_deleted == False).first()  # noqa: E712
    if not b:
        raise HTTPException(404, "Budget not found")
    it = TrainingBudgetItem(budget_id=budget_id, **payload.model_dump(), created_by_id=admin.id)
    db.add(it)
    db.flush()
    _recompute(db, b)
    write_training_audit(db, entity_type="BUDGET", entity_id=b.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id,
                         note=f"Cost line +{payload.amount}")
    db.commit()
    db.refresh(it)
    return _item_resp(db, it)


@router.delete("/budget-items/{item_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    it = db.query(TrainingBudgetItem).filter(TrainingBudgetItem.id == item_id).first()
    if not it:
        raise HTTPException(404, "Budget item not found")
    b = db.query(TrainingBudget).filter(TrainingBudget.id == it.budget_id).first()
    db.delete(it)
    db.flush()
    if b:
        _recompute(db, b)
    db.commit()


@router.get("/budget/summary", response_model=BudgetSummaryResponse)
def budget_summary(
    fiscal_year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    from datetime import date as _date
    fy = fiscal_year or _date.today().year
    rows = db.query(TrainingBudget).filter(
        TrainingBudget.is_deleted == False, TrainingBudget.fiscal_year == fy,  # noqa: E712
    ).all()
    by_dept = {}
    t_alloc = t_spent = t_comm = Decimal("0")
    for b in rows:
        key = str(b.department_id) if b.department_id else "__org__"
        d = by_dept.setdefault(key, {
            "department_id": b.department_id, "department_name": _dept_name(db, b.department_id) or "Org-wide",
            "allocated": Decimal("0"), "spent": Decimal("0"), "committed": Decimal("0"),
        })
        d["allocated"] += Decimal(str(b.allocated_amount or 0))
        d["spent"] += Decimal(str(b.spent_amount or 0))
        d["committed"] += Decimal(str(b.committed_amount or 0))
        t_alloc += Decimal(str(b.allocated_amount or 0))
        t_spent += Decimal(str(b.spent_amount or 0))
        t_comm += Decimal(str(b.committed_amount or 0))
    summary_rows = [
        BudgetSummaryRow(
            department_id=d["department_id"], department_name=d["department_name"],
            allocated=d["allocated"], spent=d["spent"], committed=d["committed"],
            remaining=d["allocated"] - d["spent"] - d["committed"],
        ) for d in by_dept.values()
    ]
    # spend split by cost category across every budget in the fiscal year
    by_cost_type: list[BudgetCostTypeRow] = []
    budget_ids = [b.id for b in rows]
    if budget_ids:
        ct_rows = (
            db.query(
                TrainingBudgetItem.cost_type,
                func.coalesce(func.sum(TrainingBudgetItem.amount), 0),
                func.coalesce(func.sum(
                    case((TrainingBudgetItem.is_committed == True, TrainingBudgetItem.amount), else_=0)  # noqa: E712
                ), 0),
                func.count(TrainingBudgetItem.id),
            )
            .filter(TrainingBudgetItem.budget_id.in_(budget_ids))
            .group_by(TrainingBudgetItem.cost_type)
            .all()
        )
        by_cost_type = [
            BudgetCostTypeRow(cost_type=r[0], amount=r[1] or 0, committed=r[2] or 0, count=int(r[3] or 0))
            for r in ct_rows
        ]
        by_cost_type.sort(key=lambda x: x.amount, reverse=True)
    return BudgetSummaryResponse(
        fiscal_year=fy, total_allocated=t_alloc, total_spent=t_spent,
        total_committed=t_comm, total_remaining=t_alloc - t_spent - t_comm,
        budget_count=len(rows), by_department=summary_rows, by_cost_type=by_cost_type,
    )
