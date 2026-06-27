"""Schemas for HR Settings — Approval Workflows (unified editor over the
existing per-policy ``approval_chain`` JSONB on leave / travel / claim policies)."""
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ApprovalStage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approver_type: str = Field(..., max_length=30)        # MANAGER | HR | FINANCE | DEPT_HEAD | USER
    approver_user_id: Optional[str] = None                # pin a specific approver (optional)
    label: Optional[str] = None
    min_amount: Optional[float] = None                    # claim/travel: stage applies only above this


class ApprovalChainUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approval_chain: List[ApprovalStage] = Field(default_factory=list)
    reason: Optional[str] = Field(default=None, max_length=300)   # sealed into the settings audit ledger
