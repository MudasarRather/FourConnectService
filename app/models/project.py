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
    cost_center = Column(String, nullable=False)
    
    # Timeline
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    
    # Step 3: Financials
    budget_amount = Column(Float, default=0.0)
    currency = Column(String, default="USD")
    budget_type = Column(String, nullable=True) # Capex, Opex
    
    # Meta
    status = Column(String, default="Draft") # Draft, Pending Approval, Active, Rejected
    is_approved = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    
    # File attachments
    project_order_path = Column(String, nullable=True)  # Path to uploaded Project Order PDF
    
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    created_by = relationship("User", back_populates="projects")
    tasks = relationship("Task", back_populates="project")
    milestones = relationship("Milestone", back_populates="project", cascade="all, delete-orphan")
