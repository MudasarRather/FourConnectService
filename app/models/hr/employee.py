import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Numeric, Integer, Sequence, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class EmploymentType(str, enum.Enum):
    FULL_TIME = "FULL_TIME"
    CONTRACT = "CONTRACT"
    INTERN = "INTERN"
    CONSULTANT = "CONSULTANT"
    PART_TIME = "PART_TIME"


class EmployeeCategory(str, enum.Enum):
    PERMANENT = "PERMANENT"
    PROBATIONARY = "PROBATIONARY"
    CONTRACT = "CONTRACT"
    TRAINEE = "TRAINEE"


class MaritalStatus(str, enum.Enum):
    SINGLE = "SINGLE"
    MARRIED = "MARRIED"
    DIVORCED = "DIVORCED"
    WIDOWED = "WIDOWED"
    OTHER = "OTHER"


class TaxRegime(str, enum.Enum):
    OLD = "OLD"
    NEW = "NEW"


class LifecycleState(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ON_PROBATION = "ON_PROBATION"
    ON_NOTICE = "ON_NOTICE"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"
    EXITED = "EXITED"
    ARCHIVED = "ARCHIVED"


# Auto-generates the EMP#### sequence (e.g. EMP0001).
employee_id_seq = Sequence("hr_employee_id_seq", start=1, increment=1)


class Employee(Base):
    """HR Employee record. 1-1 with User via user_id (auth identity stays on User)."""
    __tablename__ = "hr_employees"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False, index=True)
    employee_id = Column(String(20), unique=True, nullable=False, index=True)  # e.g. "EMP0042"
    employee_code = Column(String(50), nullable=True, index=True)  # mirror of User.employee_code for fast queries

    # ──────────── Identity ────────────
    gender = Column(String(20), nullable=True)
    dob = Column(Date, nullable=True)
    marital_status = Column(Enum(MaritalStatus, name="hr_marital_status"), nullable=True)
    blood_group = Column(String(10), nullable=True)
    nationality = Column(String(80), nullable=True)
    religion = Column(String(80), nullable=True)

    # ──────────── Statutory IDs ────────────
    aadhaar_last_4 = Column(String(4), nullable=True)  # only last 4, never full Aadhaar
    pan = Column(String(10), nullable=True)
    passport_number = Column(String(30), nullable=True)
    passport_expiry = Column(Date, nullable=True)
    driving_license = Column(String(30), nullable=True)
    uan = Column(String(20), nullable=True)
    pf_number = Column(String(30), nullable=True)
    esic_number = Column(String(30), nullable=True)
    tax_regime = Column(Enum(TaxRegime, name="hr_tax_regime"), nullable=True, default=TaxRegime.NEW)

    # ──────────── Contact ────────────
    mobile = Column(String(30), nullable=True)
    emergency_contact_name = Column(String(120), nullable=True)
    emergency_contact_phone = Column(String(30), nullable=True)
    emergency_contact_relation = Column(String(60), nullable=True)
    permanent_address = Column(String, nullable=True)
    current_address = Column(String, nullable=True)
    current_same_as_permanent = Column(Boolean, default=False, nullable=False)

    # ──────────── Employment ────────────
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True, index=True)
    designation_id = Column(UUID(as_uuid=True), ForeignKey("hr_designations.id"), nullable=True, index=True)
    employment_type = Column(Enum(EmploymentType, name="hr_employment_type"), nullable=True)
    employee_category = Column(Enum(EmployeeCategory, name="hr_employee_category"), nullable=True)
    joining_date = Column(Date, nullable=True, index=True)
    confirmation_date = Column(Date, nullable=True)
    probation_months = Column(Integer, nullable=True, default=6)
    reporting_manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    hr_manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    shift_id = Column(UUID(as_uuid=True), nullable=True)  # FK wired in Phase 2 (Shifts)
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id"), nullable=True)
    pay_level = Column(String(20), nullable=True)
    work_location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id"), nullable=True)
    work_location_text = Column(String(160), nullable=True)  # Free-text override (preferred over FK in 1.0.1+)
    notice_period_days = Column(Integer, nullable=True, default=30)

    # ──────────── Bank & Salary ────────────
    bank_name = Column(String(120), nullable=True)
    account_number = Column(String(40), nullable=True)  # TODO Phase 1.2: encrypt at rest with pgcrypto
    ifsc = Column(String(20), nullable=True)
    salary_structure_id = Column(UUID(as_uuid=True), nullable=True)  # wired Phase 3 (Payroll)
    monthly_ctc = Column(Numeric(12, 2), nullable=True)
    annual_ctc = Column(Numeric(14, 2), nullable=True)

    # ──────────── Lifecycle ────────────
    lifecycle_state = Column(
        Enum(LifecycleState, name="hr_lifecycle_state"),
        nullable=False,
        default=LifecycleState.ACTIVE,
        index=True,
    )
    suspension_reason = Column(String, nullable=True)
    suspension_date = Column(Date, nullable=True)
    notice_period_start_date = Column(Date, nullable=True)
    last_working_date = Column(Date, nullable=True)
    exit_date = Column(Date, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # ──────────── Audit ────────────
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # ──────────── Relationships ────────────
    user = relationship("User", foreign_keys=[user_id])
    department = relationship("Department", back_populates="employees", foreign_keys=[department_id])
    designation = relationship("Designation", back_populates="employees")
    grade = relationship("Grade", back_populates="employees")
    work_location = relationship("WorkLocation", back_populates="employees")
    reporting_manager = relationship("User", foreign_keys=[reporting_manager_id])
    hr_manager = relationship("User", foreign_keys=[hr_manager_id])
    history = relationship(
        "EmployeeHistory",
        back_populates="employee",
        cascade="all, delete-orphan",
        order_by="EmployeeHistory.created_at.desc()",
    )

    __table_args__ = (
        Index("ix_hr_employees_lifecycle_active", "lifecycle_state", "is_deleted"),
        Index("ix_hr_employees_dept_active", "department_id", "is_deleted"),
    )

    def __repr__(self):
        return f"<Employee {self.employee_id}>"
