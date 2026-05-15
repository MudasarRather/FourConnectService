from sqlalchemy import Column, String, Float, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from datetime import datetime
from app.database import Base

class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    
    # Step 2: Core Fields
    project_type = Column(String, nullable=False)
    organization = Column(String, nullable=True)
    # cost_center column was dropped in round-2 refinement (DROP COLUMN migration).
    # budget_type column was dropped in the same migration — funding_type below replaces it.

    # Timeline
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)

    # Step 3: Financials
    budget_amount = Column(Float, default=0.0)
    currency = Column(String, default="USD")
    
    # Meta
    status = Column(String, default="Draft") # Draft, Pending Approval, Active, Rejected
    is_approved = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    
    # File attachments
    project_order_path = Column(String, nullable=True)  # Path to uploaded Project Order PDF

    # --- Government Order Details (form §1) ---
    government_order_no  = Column(String, nullable=True, index=True)
    order_date           = Column(DateTime, nullable=True)
    issuing_authority    = Column(String, nullable=True)
    order_received_date  = Column(DateTime, nullable=True)

    # --- Project Information additions (form §2) ---
    department           = Column(String, nullable=True)
    category             = Column(String, nullable=True)   # Infrastructure | Roads & Bridges | Water & Sanitation | Buildings & Construction | IT & Digital | Social Welfare | Defence | Energy | Other
    priority             = Column(String, nullable=True)   # High | Medium | Low

    # --- Location (form §3) ---
    state                = Column(String, nullable=True)
    district             = Column(String, nullable=True)

    # --- Budget additions (form §4) — distinct from budget_type (Capex/Opex) ---
    funding_type         = Column(String, nullable=True)   # Central Govt | State Govt | Central + State | External Aid | PPP

    # --- Team & Responsibility (form §5) ---
    project_head_name        = Column(String, nullable=True)
    project_head_designation = Column(String, nullable=True)
    project_head_contact     = Column(String, nullable=True)
    nodal_officer            = Column(String, nullable=True)
    contractor               = Column(String, nullable=True)

    # --- Operational lifecycle (orthogonal to approval `status`) ---
    # Order Received | Planning | Tendering | In Progress | Active | Completed
    lifecycle_status     = Column(String, nullable=True, default="Order Received")

    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    created_by = relationship("User", back_populates="projects")
    tasks = relationship("Task", back_populates="project")
    milestones = relationship("Milestone", back_populates="project", cascade="all, delete-orphan")
