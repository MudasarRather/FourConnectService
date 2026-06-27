"""Schemas for HR Settings — Payroll Rules."""
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PayrollRuleUpsert(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str = Field(..., min_length=1, max_length=50)
    fiscal_year: Optional[str] = Field(None, max_length=7)   # null = global default
    value_num: Optional[float] = None
    value_str: Optional[str] = None
    value_json: Optional[Any] = None


class PayrollRuleResolved(BaseModel):
    key: str
    value: Any = None
    configured: bool = False


class PayrollRulesResponse(BaseModel):
    fiscal_year: Optional[str] = None
    rules: List[PayrollRuleResolved] = Field(default_factory=list)
