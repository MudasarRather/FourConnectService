"""HR Training & Development — Certification schemas."""
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.certification import CertificationStatus


# ───────────────────────────── Certification catalog ─────────────────────────────

class CertificationCreate(BaseModel):
    name: str
    code: Optional[str] = None
    issuing_authority: Optional[str] = None
    category: Optional[str] = None
    validity_months: Optional[int] = None
    description: Optional[str] = None
    skill_id: Optional[UUID] = None


class CertificationUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    issuing_authority: Optional[str] = None
    category: Optional[str] = None
    validity_months: Optional[int] = None
    description: Optional[str] = None
    skill_id: Optional[UUID] = None
    is_active: Optional[bool] = None


class CertificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: Optional[str] = None
    issuing_authority: Optional[str] = None
    category: Optional[str] = None
    validity_months: Optional[int] = None
    description: Optional[str] = None
    skill_id: Optional[UUID] = None
    skill_name: Optional[str] = None
    is_active: bool
    held_count: Optional[int] = None
    created_at: datetime


# ───────────────────────────── Employee certifications ─────────────────────────────

class EmployeeCertificationCreate(BaseModel):
    employee_id: UUID
    certification_id: Optional[UUID] = None
    name: str
    issuing_authority: Optional[str] = None
    certificate_number: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    certificate_url: Optional[str] = None
    drive_document_id: Optional[UUID] = None
    renewal_training_program_id: Optional[UUID] = None
    notes: Optional[str] = None


class EmployeeCertificationUpdate(BaseModel):
    name: Optional[str] = None
    issuing_authority: Optional[str] = None
    certificate_number: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: Optional[CertificationStatus] = None
    certificate_url: Optional[str] = None
    drive_document_id: Optional[UUID] = None
    renewal_training_program_id: Optional[UUID] = None
    notes: Optional[str] = None


class EmployeeCertificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    designation_name: Optional[str] = None
    certification_id: Optional[UUID] = None
    category: Optional[str] = None
    name: str
    issuing_authority: Optional[str] = None
    certificate_number: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: CertificationStatus
    days_to_expiry: Optional[int] = None
    certificate_url: Optional[str] = None
    drive_document_id: Optional[UUID] = None
    source_assignment_id: Optional[UUID] = None
    renewal_training_program_id: Optional[UUID] = None
    renewal_program_name: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
