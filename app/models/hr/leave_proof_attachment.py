"""HR Leave Proof Attachment — supporting documents uploaded by employees in
response to an HR proof request.

When HR flags `LeaveRequest.proof_requested=True`, the employee may upload one
or more files. Each file becomes a row here. Files live on disk under
`storage/leave-proofs/<uuid>.<ext>` (mounted at `/storage/leave-proofs/`).

Soft-delete pattern mirrors the rest of the HR module: `is_deleted=True` hides
the row from the proof list without losing the audit trail.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Integer, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class LeaveProofAttachment(Base):
    """One supporting-document upload for a LeaveRequest."""
    __tablename__ = "hr_leave_proof_attachments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    leave_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hr_leave_requests.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # Relative URL the frontend uses to download the file. Stored as
    # `/storage/leave-proofs/<uuid>.<ext>` so it lines up with the FastAPI
    # StaticFiles mount in main.py.
    file_url = Column(String(500), nullable=False)
    # Absolute path on the server filesystem — used by housekeeping/cleanup,
    # never returned to the client.
    file_path = Column(String(500), nullable=False)

    original_filename = Column(String(255), nullable=False)
    file_size = Column(Integer, nullable=False)
    mime_type = Column(String(100), nullable=True)

    uploaded_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    leave_request = relationship("LeaveRequest", foreign_keys=[leave_request_id])

    __table_args__ = (
        Index("ix_hr_leave_proof_req_active", "leave_request_id", "is_deleted"),
    )
