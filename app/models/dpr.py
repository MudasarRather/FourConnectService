from sqlalchemy import Column, String, Text, Boolean, Date, ForeignKey, Float, DateTime, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime
from app.database import Base

class DprDocument(Base):
    __tablename__ = "dpr_documents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    dpr_code = Column(String, unique=True, nullable=True)
    title = Column(String, nullable=False)
    version = Column(String, default="v1.0")
    status = Column(String, default="Draft") # Draft, Internal Review, Approved, Rejected
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    rejection_reason = Column(Text, nullable=True)

    # Relationships
    project = relationship("Project")
    created_by = relationship("User")
    
    overview = relationship("DprOverview", back_populates="dpr", uselist=False, cascade="all, delete-orphan")
    client = relationship("DprClient", back_populates="dpr", uselist=False, cascade="all, delete-orphan")
    problem_statement = relationship("DprProblemStatement", back_populates="dpr", uselist=False, cascade="all, delete-orphan")
    objectives = relationship("DprObjective", back_populates="dpr", cascade="all, delete-orphan")
    scope = relationship("DprScope", back_populates="dpr", uselist=False, cascade="all, delete-orphan")
    architecture = relationship("DprArchitecture", back_populates="dpr", uselist=False, cascade="all, delete-orphan")
    implementation = relationship("DprImplementation", back_populates="dpr", uselist=False, cascade="all, delete-orphan")
    milestones = relationship("DprMilestone", back_populates="dpr", cascade="all, delete-orphan")
    team = relationship("DprTeamMember", back_populates="dpr", cascade="all, delete-orphan")
    budget = relationship("DprBudget", back_populates="dpr", uselist=False, cascade="all, delete-orphan")
    budget_items = relationship("DprBudgetItem", back_populates="dpr", cascade="all, delete-orphan")
    risks = relationship("DprRisk", back_populates="dpr", cascade="all, delete-orphan")
    compliance = relationship("DprCompliance", back_populates="dpr", uselist=False, cascade="all, delete-orphan")
    outcomes = relationship("DprOutcome", back_populates="dpr", uselist=False, cascade="all, delete-orphan")
    attachments = relationship("DprAttachment", back_populates="dpr", cascade="all, delete-orphan")
    approvals = relationship("DprApproval", back_populates="dpr", cascade="all, delete-orphan")

# 1. Project Overview
class DprOverview(Base):
    __tablename__ = "dpr_overview"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"), unique=True)
    project_name = Column(String)
    project_code = Column(String)
    start_date = Column(Date)
    end_date = Column(Date)
    description = Column(Text)
    dpr = relationship("DprDocument", back_populates="overview")

# 2. Client Details
class DprClient(Base):
    __tablename__ = "dpr_client"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"), unique=True)
    client_name = Column(String)
    organization = Column(String)
    contact_person = Column(String)
    email = Column(String)
    phone = Column(String)
    address = Column(Text)
    dpr = relationship("DprDocument", back_populates="client")

# 3. Problem Statement
class DprProblemStatement(Base):
    __tablename__ = "dpr_problem_statements"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"), unique=True)
    statement = Column(Text)
    current_challenges = Column(Text)
    impact_analysis = Column(Text)
    dpr = relationship("DprDocument", back_populates="problem_statement")

# 4. Project Objectives
class DprObjective(Base):
    __tablename__ = "dpr_objectives"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"))
    title = Column(String)
    description = Column(Text)
    priority = Column(String) # High, Medium, Low
    dpr = relationship("DprDocument", back_populates="objectives")

# 5. Scope of Work
class DprScope(Base):
    __tablename__ = "dpr_scope"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"), unique=True)
    in_scope = Column(Text)
    out_of_scope = Column(Text)
    assumptions = Column(Text)
    constraints = Column(Text)
    dpr = relationship("DprDocument", back_populates="scope")

# 6. Technical Architecture
class DprArchitecture(Base):
    __tablename__ = "dpr_architecture"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"), unique=True)
    description = Column(Text)
    tech_stack = Column(JSON) # {"backend": "...", "frontend": "..."}
    diagram_url = Column(String)
    dpr = relationship("DprDocument", back_populates="architecture")

# 7. Implementation Plan
class DprImplementation(Base):
    __tablename__ = "dpr_implementation"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"), unique=True)
    methodology = Column(String) # Agile, Waterfall, Hybrid
    phases = Column(Text)
    deployment_strategy = Column(Text)
    dpr = relationship("DprDocument", back_populates="implementation")

# 8. Timeline / Milestones
class DprMilestone(Base):
    __tablename__ = "dpr_milestones"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"))
    title = Column(String)
    description = Column(Text)
    due_date = Column(Date)
    deliverables = Column(Text)
    dpr = relationship("DprDocument", back_populates="milestones")

# 9. Team Structure
class DprTeamMember(Base):
    __tablename__ = "dpr_team_structure"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"))
    name = Column(String)
    role = Column(String)
    responsibility = Column(Text)
    dpr = relationship("DprDocument", back_populates="team")

# 10. Budget & Costing
class DprBudget(Base):
    __tablename__ = "dpr_budget"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"), unique=True)
    total_amount = Column(Float, default=0.0)
    currency = Column(String, default="INR")
    notes = Column(Text)
    dpr = relationship("DprDocument", back_populates="budget")

class DprBudgetItem(Base):
    __tablename__ = "dpr_budget_items"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"))
    category = Column(String) # Software, Hardware, Resource, etc.
    description = Column(Text)
    amount = Column(Float, default=0.0)
    dpr = relationship("DprDocument", back_populates="budget_items")

# 11. Risk Assessment
class DprRisk(Base):
    __tablename__ = "dpr_risks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"))
    risk_description = Column(Text)
    impact = Column(String) # High, Medium, Low
    mitigation_plan = Column(Text)
    dpr = relationship("DprDocument", back_populates="risks")

# 12. Compliance
class DprCompliance(Base):
    __tablename__ = "dpr_compliance"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"), unique=True)
    legal_requirements = Column(Text)
    regulatory_standards = Column(Text)
    security_policies = Column(Text)
    dpr = relationship("DprDocument", back_populates="compliance")

# 13. Expected Outcomes
class DprOutcome(Base):
    __tablename__ = "dpr_outcomes"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"), unique=True)
    tangible_benefits = Column(Text)
    intangible_benefits = Column(Text)
    kpis = Column(Text)
    dpr = relationship("DprDocument", back_populates="outcomes")

# 14. Attachments
class DprAttachment(Base):
    __tablename__ = "dpr_attachments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"))
    file_name = Column(String)
    file_url = Column(String)
    file_type = Column(String)
    dpr = relationship("DprDocument", back_populates="attachments")

# 15. Review & Approvals
class DprApproval(Base):
    __tablename__ = "dpr_approvals"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dpr_id = Column(UUID(as_uuid=True), ForeignKey("dpr_documents.id"))
    approver_name = Column(String)
    approver_role = Column(String)
    approval_status = Column(String, default="Pending") # Pending, Approved, Rejected
    approval_date = Column(DateTime, nullable=True)
    comments = Column(Text)
    signature_url = Column(String, nullable=True)
    dpr = relationship("DprDocument", back_populates="approvals")
