from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import date, datetime

# --- Common Mixins ---
class DprBase(BaseModel):
    class Config:
        from_attributes = True

# --- User Mini Schema (to avoid circular imports) ---
class DprUserRead(DprBase):
    id: UUID
    full_name: str
    email: str
    avatar_url: Optional[str] = None

# 1. Project Overview
class DprOverviewBase(DprBase):
    project_name: Optional[str] = None
    project_code: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    description: Optional[str] = None

class DprOverviewRead(DprOverviewBase):
    id: UUID

# 2. Client Details
class DprClientBase(DprBase):
    client_name: Optional[str] = None
    organization: Optional[str] = None
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

class DprClientRead(DprClientBase):
    id: UUID

# 3. Problem Statement
class DprProblemStatementBase(DprBase):
    statement: Optional[str] = None
    current_challenges: Optional[str] = None
    impact_analysis: Optional[str] = None

class DprProblemStatementRead(DprProblemStatementBase):
    id: UUID

# 4. Project Objectives
class DprObjectiveBase(DprBase):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = "Medium"

class DprObjectiveCreate(DprObjectiveBase):
    pass

class DprObjectiveRead(DprObjectiveBase):
    id: UUID

# 5. Scope of Work
class DprScopeBase(DprBase):
    in_scope: Optional[str] = None
    out_of_scope: Optional[str] = None
    assumptions: Optional[str] = None
    constraints: Optional[str] = None

class DprScopeRead(DprScopeBase):
    id: UUID

# 6. Technical Architecture
class DprArchitectureBase(DprBase):
    description: Optional[str] = None
    tech_stack: Optional[Dict[str, Any]] = None
    diagram_url: Optional[str] = None

class DprArchitectureRead(DprArchitectureBase):
    id: UUID

# 7. Implementation Plan
class DprImplementationBase(DprBase):
    methodology: Optional[str] = "Agile"
    phases: Optional[str] = None
    deployment_strategy: Optional[str] = None

class DprImplementationRead(DprImplementationBase):
    id: UUID

# 8. Timeline / Milestones
class DprMilestoneBase(DprBase):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[date] = None
    deliverables: Optional[str] = None

class DprMilestoneCreate(DprMilestoneBase):
    pass

class DprMilestoneRead(DprMilestoneBase):
    id: UUID

# 9. Team Structure
class DprTeamMemberBase(DprBase):
    name: Optional[str] = None
    role: Optional[str] = None
    responsibility: Optional[str] = None

class DprTeamMemberCreate(DprTeamMemberBase):
    pass

class DprTeamMemberRead(DprTeamMemberBase):
    id: UUID

# 10. Budget & Costing
class DprBudgetItemBase(DprBase):
    category: Optional[str] = None
    description: Optional[str] = None
    amount: float = 0.0

class DprBudgetItemCreate(DprBudgetItemBase):
    pass

class DprBudgetItemRead(DprBudgetItemBase):
    id: UUID

class DprBudgetBase(DprBase):
    total_amount: float = 0.0
    currency: str = "INR"
    notes: Optional[str] = None

class DprBudgetRead(DprBudgetBase):
    id: UUID

# 11. Risk Assessment
class DprRiskBase(DprBase):
    risk_description: Optional[str] = None
    impact: Optional[str] = "Medium"
    mitigation_plan: Optional[str] = None

class DprRiskCreate(DprRiskBase):
    pass

class DprRiskRead(DprRiskBase):
    id: UUID

# 12. Compliance
class DprComplianceBase(DprBase):
    legal_requirements: Optional[str] = None
    regulatory_standards: Optional[str] = None
    security_policies: Optional[str] = None

class DprComplianceRead(DprComplianceBase):
    id: UUID

# 13. Expected Outcomes
class DprOutcomeBase(DprBase):
    tangible_benefits: Optional[str] = None
    intangible_benefits: Optional[str] = None
    kpis: Optional[str] = None

class DprOutcomeRead(DprOutcomeBase):
    id: UUID

# 14. Attachments
class DprAttachmentBase(DprBase):
    file_name: Optional[str] = None
    file_url: Optional[str] = None
    file_type: Optional[str] = None

class DprAttachmentCreate(DprAttachmentBase):
    pass

class DprAttachmentRead(DprAttachmentBase):
    id: UUID

# 15. Review & Approvals
class DprApprovalBase(DprBase):
    approver_name: Optional[str] = None
    approver_role: Optional[str] = None
    approval_status: str = "Pending"
    approval_date: Optional[datetime] = None
    comments: Optional[str] = None
    signature_url: Optional[str] = None

class DprApprovalCreate(DprApprovalBase):
    pass

class DprApprovalRead(DprApprovalBase):
    id: UUID

# --- Main Document Schema ---
class DprDocumentBase(DprBase):
    project_id: Optional[UUID] = None
    dpr_code: Optional[str] = None
    title: str
    version: str = "v1.0"
    status: str = "Draft"
    rejection_reason: Optional[str] = None

class DprDocumentCreate(DprDocumentBase):
    # Sections as optional dicts or lists during creation
    overview: Optional[DprOverviewBase] = None
    client: Optional[DprClientBase] = None
    problem_statement: Optional[DprProblemStatementBase] = None
    objectives: Optional[List[DprObjectiveCreate]] = []
    scope: Optional[DprScopeBase] = None
    architecture: Optional[DprArchitectureBase] = None
    implementation: Optional[DprImplementationBase] = None
    milestones: Optional[List[DprMilestoneCreate]] = []
    team: Optional[List[DprTeamMemberCreate]] = []
    budget: Optional[DprBudgetBase] = None
    budget_items: Optional[List[DprBudgetItemCreate]] = []
    risks: Optional[List[DprRiskCreate]] = []
    compliance: Optional[DprComplianceBase] = None
    outcomes: Optional[DprOutcomeBase] = None
    attachments: Optional[List[DprAttachmentCreate]] = []
    approvals: Optional[List[DprApprovalCreate]] = []

class DprDocumentUpdate(DprDocumentCreate):
    title: Optional[str] = None

class DprDocumentRead(DprDocumentBase):
    id: UUID
    created_by_id: UUID
    created_at: datetime
    updated_at: datetime
    rejection_reason: Optional[str] = None
    
    # Relationships for detailed read
    created_by: Optional[DprUserRead] = None 
    
    # Detailed sections for full read
    overview: Optional[DprOverviewRead] = None
    client: Optional[DprClientRead] = None
    problem_statement: Optional[DprProblemStatementRead] = None
    objectives: List[DprObjectiveRead] = []
    scope: Optional[DprScopeRead] = None
    architecture: Optional[DprArchitectureRead] = None
    implementation: Optional[DprImplementationRead] = None
    milestones: List[DprMilestoneRead] = []
    team: List[DprTeamMemberRead] = []
    budget: Optional[DprBudgetRead] = None
    budget_items: List[DprBudgetItemRead] = []
    risks: List[DprRiskRead] = []
    compliance: Optional[DprComplianceRead] = None
    outcomes: Optional[DprOutcomeRead] = None
    attachments: List[DprAttachmentRead] = []
    approvals: List[DprApprovalRead] = []
