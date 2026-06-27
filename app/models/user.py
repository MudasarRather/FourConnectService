import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class User(Base):
    """User model for authentication and user management"""
    
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    employee_code = Column(String, unique=True, nullable=True, index=True)
    phone = Column(String, nullable=True)
    country_code = Column(String, nullable=True)  # Phone country code like US, IN, UK
    address = Column(String, nullable=True)
    job_title = Column(String, nullable=True)
    department = Column(String, nullable=True)
    organisation = Column(String, nullable=True)  # User's organisation
    bio = Column(String, nullable=True)
    location = Column(String, nullable=True)
    country = Column(String, nullable=True)
    state = Column(String, nullable=True)
    city = Column(String, nullable=True)
    gender = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    is_activated = Column(Boolean, default=False, nullable=False)  # Account activation status
    activation_code = Column(String, nullable=True)  # 8-digit activation code
    # Session-invalidation counter. Issued JWTs carry a `tv` claim equal to this
    # value at mint time; get_current_user rejects a token whose `tv` no longer
    # matches. Bumping it (on email change / ERP password reset by an admin)
    # force-logs-out the user's live sessions WITHOUT deactivating the account —
    # they sign back in with the new credentials and get a fresh, matching token.
    token_version = Column(Integer, default=1, server_default="1", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    projects = relationship("Project", back_populates="created_by")
    
    def __repr__(self):
        return f"<User {self.email}>"
