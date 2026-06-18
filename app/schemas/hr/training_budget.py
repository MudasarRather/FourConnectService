"""HR Training & Development — Budget schemas."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.training_budget import BudgetPeriodType, BudgetCostType


class TrainingBudgetCreate(BaseModel):
    name: str
    period_type: BudgetPeriodType = BudgetPeriodType.ANNUAL
    fiscal_year: int
    period_index: Optional[int] = None
    department_id: Optional[UUID] = None
    allocated_amount: Decimal = Decimal("0")
    currency: str = "INR"


class TrainingBudgetUpdate(BaseModel):
    name: Optional[str] = None
    period_type: Optional[BudgetPeriodType] = None
    fiscal_year: Optional[int] = None
    period_index: Optional[int] = None
    department_id: Optional[UUID] = None
    allocated_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    is_active: Optional[bool] = None


class TrainingBudgetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    period_type: BudgetPeriodType
    fiscal_year: int
    period_index: Optional[int] = None
    department_id: Optional[UUID] = None
    department_name: Optional[str] = None
    allocated_amount: Decimal
    currency: str
    spent_amount: Decimal
    committed_amount: Decimal
    remaining: Optional[Decimal] = None
    utilization_pct: Optional[float] = None
    item_count: Optional[int] = None
    is_active: bool
    created_at: datetime


class TrainingBudgetItemCreate(BaseModel):
    program_id: Optional[UUID] = None
    assignment_id: Optional[UUID] = None
    request_id: Optional[UUID] = None
    trainer_id: Optional[UUID] = None
    title: Optional[str] = None
    amount: Decimal
    cost_type: BudgetCostType = BudgetCostType.OTHER
    is_committed: bool = False
    incurred_date: Optional[date] = None
    notes: Optional[str] = None


class TrainingBudgetItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    budget_id: UUID
    program_id: Optional[UUID] = None
    program_name: Optional[str] = None
    trainer_id: Optional[UUID] = None
    trainer_name: Optional[str] = None
    title: Optional[str] = None
    amount: Decimal
    cost_type: BudgetCostType
    is_committed: bool
    incurred_date: Optional[date] = None
    notes: Optional[str] = None
    created_at: datetime


class BudgetSummaryRow(BaseModel):
    department_id: Optional[UUID] = None
    department_name: Optional[str] = None
    allocated: Decimal
    spent: Decimal
    committed: Decimal
    remaining: Decimal


class BudgetCostTypeRow(BaseModel):
    cost_type: BudgetCostType
    amount: Decimal
    committed: Decimal
    count: int


class BudgetSummaryResponse(BaseModel):
    fiscal_year: int
    total_allocated: Decimal
    total_spent: Decimal
    total_committed: Decimal
    total_remaining: Decimal
    budget_count: int = 0
    by_department: List[BudgetSummaryRow] = []
    by_cost_type: List[BudgetCostTypeRow] = []
