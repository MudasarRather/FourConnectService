import uuid
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.database import Base

class AllowedEmployee(Base):
    """Model for whitelisting employees who can sign up"""
    
    __tablename__ = "allowed_employees"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_code = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, nullable=False) # Phone number for verification
    is_registered = Column(Boolean, default=False, nullable=False) # To track if they have signed up
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<AllowedEmployee {self.employee_code}>"
