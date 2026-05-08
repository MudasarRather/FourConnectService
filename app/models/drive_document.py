import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Integer, BigInteger, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.database import Base


class DriveDocument(Base):
    """Enterprise Document Drive - File/Document model"""
    
    __tablename__ = "drive_documents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # Core metadata
    title = Column(String(500), nullable=False, index=True)
    description = Column(Text, nullable=True)
    file_name = Column(String(500), nullable=False)
    file_url = Column(String(1000), nullable=False)
    file_type = Column(String(50), nullable=True)  # pdf, jpg, xlsx, docx, etc.
    file_size = Column(BigInteger, default=0)  # bytes
    mime_type = Column(String(200), nullable=True)
    
    # Classification
    category = Column(String(100), nullable=True, index=True)  # Finance, Compliance, HR, Legal, etc.
    tags = Column(JSONB, default=list)  # ["tax", "2024", "gst"]
    
    # Lifecycle
    status = Column(String(50), default="Active", index=True)  # Active, Under Review, Approved, Archived, Expired, Deleted
    is_favorite = Column(Boolean, default=False)
    is_locked = Column(Boolean, default=False)
    is_confidential = Column(Boolean, default=False)
    
    # Version control
    version = Column(String(20), default="1.0")
    version_number = Column(Integer, default=1)
    parent_document_id = Column(UUID(as_uuid=True), nullable=True)  # For versioning chain
    
    # Ownership
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    department = Column(String(200), nullable=True)
    
    # Linking
    project_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    project_name = Column(String(300), nullable=True)
    
    # Expiry
    expiry_date = Column(DateTime(timezone=True), nullable=True)
    
    # Sharing
    shared_with = Column(JSONB, default=list)  # [{"user_id": "...", "permission": "view"}]
    access_level = Column(String(50), default="Private")  # Private, Department, Organization, Public
    
    # Audit
    last_accessed_at = Column(DateTime(timezone=True), nullable=True)
    last_accessed_by = Column(UUID(as_uuid=True), nullable=True)
    download_count = Column(Integer, default=0)
    view_count = Column(Integer, default=0)
    
    # Soft delete
    is_deleted = Column(Boolean, default=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    def __repr__(self):
        return f"<DriveDocument {self.title}>"


class DriveActivity(Base):
    """Activity log for Document Drive actions"""
    
    __tablename__ = "drive_activity_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    document_id = Column(UUID(as_uuid=True), ForeignKey("drive_documents.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    user_name = Column(String(200), nullable=True)
    action = Column(String(50), nullable=False)  # uploaded, viewed, downloaded, shared, archived, restored, deleted, updated, locked, unlocked
    details = Column(Text, nullable=True)
    activity_metadata = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    def __repr__(self):
        return f"<DriveActivity {self.action} on {self.document_id}>"


class DriveFolder(Base):
    """Virtual folder structure for organizing documents"""
    
    __tablename__ = "drive_folders"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(300), nullable=False)
    parent_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    color = Column(String(20), default="#f59e0b")
    icon = Column(String(50), default="folder")
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    def __repr__(self):
        return f"<DriveFolder {self.name}>"
