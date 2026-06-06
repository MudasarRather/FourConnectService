from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Float, Boolean, Date, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.database import Base


class Handover(Base):
    __tablename__ = "project_handovers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Step 1: Overview
    project_name = Column(String, nullable=True)
    project_code = Column(String, nullable=True)
    client_organization = Column(String, nullable=True)
    department = Column(String, nullable=True)
    start_date = Column(Date, nullable=True)
    completion_date = Column(Date, nullable=True)
    project_manager = Column(String, nullable=True)
    project_summary = Column(Text, nullable=True)

    # Step 4: Technical Architecture
    architecture_description = Column(Text, nullable=True)
    tech_stack_backend = Column(String, nullable=True)
    tech_stack_frontend = Column(String, nullable=True)
    tech_stack_database = Column(String, nullable=True)
    architecture_diagram_url = Column(String, nullable=True)

    # Step 9: Operational & Maintenance
    backup_frequency = Column(String, nullable=True)
    backup_location = Column(String, nullable=True)
    backup_type = Column(String, nullable=True)
    monitoring_tools = Column(String, nullable=True)
    alert_system = Column(String, nullable=True)
    dashboard_url = Column(String, nullable=True)
    maintenance_schedule = Column(String, nullable=True)
    patch_management_plan = Column(String, nullable=True)

    # Step 10: Support & SLA
    sla_id = Column(UUID(as_uuid=True), ForeignKey("sla_agreements.id", ondelete="SET NULL"), nullable=True)
    support_start_date = Column(Date, nullable=True)
    support_end_date = Column(Date, nullable=True)
    support_type = Column(String, nullable=True)

    # Step 12: Financial Closure
    total_project_value = Column(Float, nullable=True)
    amount_received = Column(Float, default=0.0)
    pending_amount = Column(Float, default=0.0)
    currency = Column(String, default="INR")

    # Step 1 (cont.): vendor responsible for the delivered system
    system_vendor = Column(String, nullable=True)

    # Step 15: Client remarks / acceptance notes (overall)
    client_remarks = Column(Text, nullable=True)

    # Global
    status = Column(String, default="Draft")
    rejection_reason = Column(Text, nullable=True)
    version = Column(String, default="v1.0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    project = relationship("Project", backref="handovers")
    created_by = relationship("User", backref="created_handovers")
    sla = relationship("SlaAgreement", backref="handovers")

    stakeholders = relationship("HandoverStakeholder", back_populates="handover", cascade="all, delete-orphan")
    modules = relationship("HandoverModule", back_populates="handover", cascade="all, delete-orphan")
    assets = relationship("HandoverAsset", back_populates="handover", cascade="all, delete-orphan")
    servers = relationship("HandoverServer", back_populates="handover", cascade="all, delete-orphan")
    credentials = relationship("HandoverCredential", back_populates="handover", cascade="all, delete-orphan")
    documents = relationship("HandoverDocument", back_populates="handover", cascade="all, delete-orphan")
    training = relationship("HandoverTraining", back_populates="handover", cascade="all, delete-orphan")
    financial_invoices = relationship("HandoverFinancial", back_populates="handover", cascade="all, delete-orphan")
    issues = relationship("HandoverIssue", back_populates="handover", cascade="all, delete-orphan")
    approvals = relationship("HandoverApproval", back_populates="handover", cascade="all, delete-orphan")
    deliverables = relationship("HandoverDeliverable", back_populates="handover", cascade="all, delete-orphan")
    feedback = relationship("HandoverFeedback", back_populates="handover", cascade="all, delete-orphan")


class HandoverStakeholder(Base):
    __tablename__ = "handover_stakeholders"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False)
    name = Column(String, nullable=False)
    organization = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    handover = relationship("Handover", back_populates="stakeholders")


class HandoverModule(Base):
    __tablename__ = "handover_modules"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    module_name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String, default="Delivered")
    delivery_date = Column(Date, nullable=True)
    handover = relationship("Handover", back_populates="modules")


class HandoverServer(Base):
    __tablename__ = "handover_servers"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    server_name = Column(String, nullable=False)
    ip_address = Column(String, nullable=True)
    role = Column(String, nullable=True)
    os = Column(String, nullable=True)
    location = Column(String, nullable=True)
    hosting_type = Column(String, nullable=True)
    handover = relationship("Handover", back_populates="servers")


class HandoverAsset(Base):
    __tablename__ = "handover_assets"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    asset_name = Column(String, nullable=False)
    model = Column(String, nullable=True)
    serial_number = Column(String, nullable=True)
    quantity = Column(Integer, default=1)
    assigned_to = Column(String, nullable=True)
    location = Column(String, nullable=True)
    handover = relationship("Handover", back_populates="assets")


class HandoverCredential(Base):
    __tablename__ = "handover_credentials"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    system = Column(String, nullable=False)
    username = Column(String, nullable=True)
    access_level = Column(String, nullable=True)
    delivered_to = Column(String, nullable=True)
    password = Column(String, nullable=True)
    handover = relationship("Handover", back_populates="credentials")


class HandoverDocument(Base):
    __tablename__ = "handover_documents"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    document_name = Column(String, nullable=False)
    doc_type = Column(String, nullable=True)
    version = Column(String, nullable=True)
    link_url = Column(String, nullable=True)
    handover = relationship("Handover", back_populates="documents")


class HandoverTraining(Base):
    __tablename__ = "handover_training"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    topic = Column(String, nullable=False)
    trainer = Column(String, nullable=True)
    training_date = Column(Date, nullable=True)
    participants = Column(String, nullable=True)
    training_mode = Column(String, nullable=True)
    completion_status = Column(String, default="Pending")
    handover = relationship("Handover", back_populates="training")


class HandoverFinancial(Base):
    __tablename__ = "handover_financials"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    invoice_no = Column(String, nullable=False)
    invoice_date = Column(Date, nullable=True)
    amount = Column(Float, nullable=False)
    status = Column(String, default="Pending")
    handover = relationship("Handover", back_populates="financial_invoices")


class HandoverIssue(Base):
    __tablename__ = "handover_issues"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    issue_type = Column(String, nullable=True)
    issue_desc = Column(String, nullable=False)
    impact = Column(String, nullable=True)
    owner = Column(String, nullable=True)
    expected_resolution = Column(String, nullable=True)
    handover = relationship("Handover", back_populates="issues")


class HandoverDeliverable(Base):
    """Step 15 — client-facing acceptance of delivered items, each with an optional
    client remark. Seeded from delivered modules/assets in the wizard, then editable."""
    __tablename__ = "handover_deliverables"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    item_name = Column(String, nullable=False)
    category = Column(String, nullable=True)        # Module / Asset / Document / Server / Other
    status = Column(String, default="Delivered")    # Delivered / Partial / Pending
    client_remark = Column(Text, nullable=True)
    handover = relationship("Handover", back_populates="deliverables")


class HandoverFeedback(Base):
    """Step 14 — corporate client acceptance: per-criterion rating + comment
    (e.g. Installation, Service & Support, Training, Documentation, Timeliness)."""
    __tablename__ = "handover_feedback"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    criterion = Column(String, nullable=False)
    rating = Column(String, nullable=True)      # Excellent / Good / Satisfactory / Needs Improvement
    comment = Column(Text, nullable=True)
    handover = relationship("Handover", back_populates="feedback")


class HandoverApproval(Base):
    __tablename__ = "handover_approvals"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handover_id = Column(UUID(as_uuid=True), ForeignKey("project_handovers.id", ondelete="CASCADE"), nullable=False)
    party = Column(String, nullable=False)
    name = Column(String, nullable=False)
    designation = Column(String, nullable=True)
    signature_date = Column(Date, nullable=True)
    has_signed = Column(Boolean, default=False)
    handover = relationship("Handover", back_populates="approvals")
