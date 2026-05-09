from pydantic import BaseModel, root_validator
from typing import List, Optional, Any
from datetime import datetime
from uuid import UUID

# Sub-components

class SlaServiceScopeBase(BaseModel):
    service_name: str
    description: Optional[str] = None
    service_category: Optional[str] = None

class SlaMetricBase(BaseModel):
    service_type: Optional[str] = None
    priority_level: Optional[str] = None
    response_time: Optional[str] = None
    resolution_time: Optional[str] = None
    uptime_commitment: Optional[str] = None
    measurement_method: Optional[str] = None

class SlaEscalationBase(BaseModel):
    level: Optional[str] = None
    role: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    response_time: Optional[str] = None
    support_availability: Optional[str] = None
    support_start_time: Optional[str] = None
    support_end_time: Optional[str] = None
    timezone: Optional[str] = None

class SlaPenaltyBase(BaseModel):
    sla_violation: Optional[str] = None
    penalty_type: Optional[str] = None
    penalty_value: Optional[str] = None
    maximum_limit: Optional[str] = None

class SlaSignatoryBase(BaseModel):
    party: Optional[str] = None
    name: Optional[str] = None
    designation: Optional[str] = None
    email: Optional[str] = None
    signature_url: Optional[str] = None
    signed_date: Optional[datetime] = None

# We need a slightly modified service scope for creation/reading
class SlaServiceScopeCreate(SlaServiceScopeBase):
    metrics: List[SlaMetricBase] = []

class SlaServiceScopeResponse(SlaServiceScopeBase):
    id: UUID
    metrics: List[SlaMetricBase] = []
    class Config:
        orm_mode = True

class SlaEscalationResponse(SlaEscalationBase):
    id: UUID
    class Config:
        orm_mode = True

class SlaPenaltyResponse(SlaPenaltyBase):
    id: UUID
    class Config:
        orm_mode = True

class SlaSignatoryResponse(SlaSignatoryBase):
    id: UUID
    class Config:
        orm_mode = True


# Main Agreement

class SlaAgreementBase(BaseModel):
    project_id: Optional[UUID] = None
    
    # Step 1
    client_organization_name: Optional[str] = None
    client_address: Optional[str] = None
    client_contact_person: Optional[str] = None
    client_email: Optional[str] = None
    client_phone: Optional[str] = None
    provider_name: Optional[str] = None
    provider_address: Optional[str] = None
    provider_registration_number: Optional[str] = None
    provider_tax_id: Optional[str] = None

    # Step 2
    title: Optional[str] = None
    description: Optional[str] = None
    services_covered: Optional[str] = None
    agreement_type: Optional[str] = None
    start_date: Optional[Any] = None
    end_date: Optional[Any] = None
    renewal_type: Optional[str] = None
    version: Optional[str] = "1.0"
    contract_reference: Optional[str] = None
    template_id: Optional[str] = None

    # Step 6
    monitoring_tools: Optional[List[str]] = []
    reporting_frequency: Optional[str] = None
    report_delivery_method: Optional[str] = None
    monitoring_dashboard_url: Optional[str] = None
    alert_notification_email: Optional[str] = None

    # Step 7
    security_measures: Optional[List[str]] = []
    compliance_standards: Optional[List[str]] = []
    data_retention_policy: Optional[str] = None
    incident_reporting_time: Optional[str] = None

    # Step 8
    agreement_value: Optional[float] = None
    currency: Optional[str] = None
    billing_frequency: Optional[str] = None
    payment_method: Optional[str] = None

    # Step 9
    liability_limit: Optional[str] = None
    termination_conditions: Optional[str] = None
    force_majeure_clause: Optional[str] = None
    confidentiality_clause: Optional[str] = None
    intellectual_property_clause: Optional[str] = None
    
    status: Optional[str] = "Draft"
    rejection_reason: Optional[str] = None

    @root_validator(pre=True)
    def handle_empty_strings(cls, values):
        # Prevent DataErrors on postgres date fields by checking for ""
        if isinstance(values, dict):
            for field in ['start_date', 'end_date']:
                if values.get(field) == "":
                    values[field] = None
        else:
            for field in ['start_date', 'end_date']:
                if getattr(values, field, None) == "":
                    setattr(values, field, None)
        return values

class SlaAgreementCreate(SlaAgreementBase):
    services: Optional[List[SlaServiceScopeCreate]] = []
    escalations: Optional[List[SlaEscalationBase]] = []
    penalties: Optional[List[SlaPenaltyBase]] = []
    signatories: Optional[List[SlaSignatoryBase]] = []

class SlaAgreementUpdate(SlaAgreementBase):
    project_id: Optional[UUID] = None
    services: Optional[List[SlaServiceScopeCreate]] = []
    escalations: Optional[List[SlaEscalationBase]] = []
    penalties: Optional[List[SlaPenaltyBase]] = []
    signatories: Optional[List[SlaSignatoryBase]] = []

class UserCompact(BaseModel):
    id: UUID
    full_name: str
    class Config:
        orm_mode = True
        from_attributes = True

class SlaAgreementResponse(SlaAgreementBase):
    id: UUID
    created_by_id: UUID
    created_at: datetime
    updated_at: datetime
    
    created_by: Optional[UserCompact] = None
    
    services: List[SlaServiceScopeResponse] = []
    escalations: List[SlaEscalationResponse] = []
    penalties: List[SlaPenaltyResponse] = []
    signatories: List[SlaSignatoryResponse] = []
    
    class Config:
        orm_mode = True
        from_attributes = True
