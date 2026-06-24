"""HR Exit Management — generated letters (Experience / Relieving).

Ties a generated letter to the case AND the unified Employee Documents hub: the
PDF lives in ``DriveDocument``; an ``EmployeeDocument`` (source=GENERATED) mirror
row surfaces it in the existing docs hub; this ``ExitDocument`` is the exit-local
pointer + the QR verification anchor (``verification_code``).

Reuses ``DocTemplateType.EXPERIENCE_LETTER`` / ``RELIEVING_LETTER`` — no new enum.
New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Enum, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.exit_type import ExitDocStatus
from app.models.hr.employee_document import DocTemplateType


class ExitDocument(Base):
    __tablename__ = "hr_exit_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    exit_case_id = Column(UUID(as_uuid=True), ForeignKey("hr_exit_cases.id", ondelete="CASCADE"),
                          nullable=False, index=True)

    doc_type = Column(Enum(DocTemplateType, name="hr_doc_template_type", create_type=False), nullable=False)
    status = Column(Enum(ExitDocStatus, name="hr_exit_doc_status"), nullable=False,
                    default=ExitDocStatus.NOT_GENERATED, index=True)

    template_id = Column(UUID(as_uuid=True), ForeignKey("hr_employee_document_templates.id", ondelete="SET NULL"), nullable=True)
    drive_document_id = Column(UUID(as_uuid=True), ForeignKey("drive_documents.id", ondelete="SET NULL"), nullable=True)
    employee_document_id = Column(UUID(as_uuid=True), ForeignKey("hr_employee_documents.id", ondelete="SET NULL"), nullable=True)

    verification_code = Column(String(40), nullable=True, unique=True, index=True)   # QR anchor
    issued_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    issued_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(Text, nullable=True)

    content_snapshot = Column(JSONB, nullable=False, default=dict)   # resolved placeholders at generation

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    exit_case = relationship("ExitCase", back_populates="documents")

    __table_args__ = (
        Index("ix_hr_exit_doc_case_type", "exit_case_id", "doc_type"),
    )

    def __repr__(self):
        return f"<ExitDocument {self.doc_type} {self.status}>"
