"""HR Settings — Numbering Series.

Configurable auto-ID formats per module (Employee, Recruitment, …). A series is
OPT-IN: until an admin creates an active row for a module, ID generation keeps
using the existing PG-sequence / MAX+1 logic unchanged (``next_number`` returns
None → caller falls back). When a series exists, ``next_number`` formats
``prefix + {YYYY}/{MM} tokens + zero-padded counter + suffix`` and advances the
counter inside the caller's transaction (atomic with the row it numbers).

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base

# Modules whose generators consult next_number() (wired in Phase C).
NUMBERING_MODULES = [
    {"module": "EMPLOYEE", "label": "Employee ID", "sample_prefix": "EMP"},
    {"module": "RECRUITMENT_REQUISITION", "label": "Requisition No.", "sample_prefix": "REQ"},
    {"module": "RECRUITMENT_POSITION", "label": "Position Code", "sample_prefix": "POS"},
    {"module": "RECRUITMENT_CANDIDATE", "label": "Candidate Code", "sample_prefix": "CAN"},
    {"module": "RECRUITMENT_APPLICATION", "label": "Application Code", "sample_prefix": "APP"},
    {"module": "RECRUITMENT_INTERVIEW", "label": "Interview Code", "sample_prefix": "INT"},
    {"module": "RECRUITMENT_OFFER", "label": "Offer Code", "sample_prefix": "OFR"},
]


class NumberingSeries(Base):
    __tablename__ = "hr_numbering_series"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    module = Column(String(40), nullable=False, unique=True, index=True)
    prefix = Column(String(20), nullable=False, default="")
    suffix = Column(String(20), nullable=True)
    # Separator inserted between prefix/tokens/counter, e.g. "-" → EMP-2026-0001
    separator = Column(String(4), nullable=False, default="")
    padding = Column(Integer, nullable=False, default=4)
    current_number = Column(Integer, nullable=False, default=0)
    include_year = Column(Boolean, nullable=False, default=False)   # insert {YYYY}
    include_month = Column(Boolean, nullable=False, default=False)  # insert {MM}
    financial_year_reset = Column(Boolean, nullable=False, default=False)
    last_reset_fy = Column(String(7), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("module", name="uq_hr_numbering_module"),
    )

    def __repr__(self):
        return f"<NumberingSeries {self.module} {self.prefix}#>"
