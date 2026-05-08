from sqlalchemy import Column, String, Float, DateTime, Boolean, ForeignKey, Text, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import uuid
from datetime import datetime
from app.database import Base

class SlaAgreement(Base):
    __tablename__ = "sla_agreements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), index=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    
    # Step 1: Client Info
    client_organization_name = Column(String, nullable=True)
    client_address = Column(Text, nullable=True)
    client_contact_person = Column(String, nullable=True)
    client_email = Column(String, nullable=True)
    client_phone = Column(String, nullable=True)
    
    provider_name = Column(String, nullable=True)
    provider_address = Column(Text, nullable=True)
    provider_registration_number = Column(String, nullable=True)
    provider_tax_id = Column(String, nullable=True)

    # Step 2: Overview
    title = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    services_covered = Column(Text, nullable=True)
    agreement_type = Column(String, nullable=True)
    
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    renewal_type = Column(String, nullable=True)
    
    version = Column(String, default="1.0")
    contract_reference = Column(String, nullable=True)
    template_id = Column(String, nullable=True)
    
    # Step 6: Monitoring
    monitoring_tools = Column(JSONB, default=list)
    reporting_frequency = Column(String, nullable=True)
    report_delivery_method = Column(String, nullable=True)
    monitoring_dashboard_url = Column(String, nullable=True)
    alert_notification_email = Column(String, nullable=True)
    
    # Step 7: Security
    security_measures = Column(JSONB, default=list)
    compliance_standards = Column(JSONB, default=list)
    data_retention_policy = Column(String, nullable=True)
    incident_reporting_time = Column(String, nullable=True)
    
    # Step 8: Payment
    agreement_value = Column(Float, nullable=True)
    currency = Column(String, nullable=True)
    billing_frequency = Column(String, nullable=True)
    payment_method = Column(String, nullable=True)
    
    # Step 9: Legal
    liability_limit = Column(Text, nullable=True)
    termination_conditions = Column(Text, nullable=True)
    force_majeure_clause = Column(Text, nullable=True)
    confidentiality_clause = Column(Text, nullable=True)
    intellectual_property_clause = Column(Text, nullable=True)
    
    # Status
    status = Column(String, default="Draft") # Draft, Approval, Active, Expired
    rejection_reason = Column(Text, nullable=True)
    
    # Activity tracking
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    project = relationship("Project")
    created_by = relationship("User")
    
    services = relationship("SlaServiceScope", back_populates="agreement", cascade="all, delete-orphan")
    escalations = relationship("SlaEscalation", back_populates="agreement", cascade="all, delete-orphan")
    penalties = relationship("SlaPenalty", back_populates="agreement", cascade="all, delete-orphan")
    signatories = relationship("SlaSignatory", back_populates="agreement", cascade="all, delete-orphan")
    documents = relationship("SlaDocument", back_populates="agreement", cascade="all, delete-orphan")

class SlaServiceScope(Base):
    __tablename__ = "sla_service_scopes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    agreement_id = Column(UUID(as_uuid=True), ForeignKey("sla_agreements.id"))
    
    service_name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    service_category = Column(String, nullable=True)
    
    # Dynamic matrix links
    metrics = relationship("SlaMetric", back_populates="service_scope", cascade="all, delete-orphan")
    agreement = relationship("SlaAgreement", back_populates="services")

class SlaMetric(Base):
    __tablename__ = "sla_metrics"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    service_scope_id = Column(UUID(as_uuid=True), ForeignKey("sla_service_scopes.id"))
    
    service_type = Column(String, nullable=True)
    priority_level = Column(String, nullable=True) # Critical, High, Medium, Low
    response_time = Column(String, nullable=True)
    resolution_time = Column(String, nullable=True)
    uptime_commitment = Column(String, nullable=True)
    measurement_method = Column(String, nullable=True)
    
    service_scope = relationship("SlaServiceScope", back_populates="metrics")

class SlaEscalation(Base):
    __tablename__ = "sla_escalations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    agreement_id = Column(UUID(as_uuid=True), ForeignKey("sla_agreements.id"))
    
    level = Column(String, nullable=True)
    role = Column(String, nullable=True)
    contact_person = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    response_time = Column(String, nullable=True)
    
    support_availability = Column(String, nullable=True)
    support_start_time = Column(String, nullable=True)
    support_end_time = Column(String, nullable=True)
    timezone = Column(String, nullable=True)
    
    agreement = relationship("SlaAgreement", back_populates="escalations")

class SlaPenalty(Base):
    __tablename__ = "sla_penalties"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    agreement_id = Column(UUID(as_uuid=True), ForeignKey("sla_agreements.id"))
    
    sla_violation = Column(String, nullable=True)
    penalty_type = Column(String, nullable=True)
    penalty_value = Column(String, nullable=True)
    maximum_limit = Column(String, nullable=True)
    
    agreement = relationship("SlaAgreement", back_populates="penalties")

class SlaSignatory(Base):
    __tablename__ = "sla_signatories"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    agreement_id = Column(UUID(as_uuid=True), ForeignKey("sla_agreements.id"))
    
    party = Column(String, nullable=True) # Service Provider / Client
    name = Column(String, nullable=True)
    designation = Column(String, nullable=True)
    email = Column(String, nullable=True)
    signature_url = Column(String, nullable=True)
    signed_date = Column(DateTime, nullable=True)
    
    agreement = relationship("SlaAgreement", back_populates="signatories")

class SlaDocument(Base):
    __tablename__ = "sla_documents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    agreement_id = Column(UUID(as_uuid=True), ForeignKey("sla_agreements.id"))
    
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String, nullable=True) # PDF / DOCX
    version = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    agreement = relationship("SlaAgreement", back_populates="documents")
