from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, EmailStr, UUID4, field_validator
import re


class UserBase(BaseModel):
    """Base user schema"""
    email: EmailStr
    full_name: str
    phone: Optional[str] = None
    country_code: Optional[str] = None
    employee_code: Optional[str] = None
    address: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    organisation: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    gender: Optional[str] = None
    avatar_url: Optional[str] = None


class UserCreate(UserBase):
    """Schema for creating a new user"""
    password: str
    
    @field_validator('password')
    @classmethod
    def validate_password_strength(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[0-9]', v):
            raise ValueError('Password must contain at least one number')
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', v):
            raise ValueError('Password must contain at least one special character')
        return v


class UserUpdate(BaseModel):
    """Schema for updating a user"""
    full_name: Optional[str] = None
    phone: Optional[str] = None
    country_code: Optional[str] = None
    employee_code: Optional[str] = None
    address: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    organisation: Optional[str] = None
    bio: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    gender: Optional[str] = None
    avatar_url: Optional[str] = None


class UserSelfUpdate(BaseModel):
    """Self-service update — explicit whitelist of fields a user can edit on themselves.

    Deliberately excludes: is_superuser, is_active, is_activated, activation_code,
    email, employee_code, hashed_password, organisation. Any unknown fields in
    the request body are silently ignored (extra='ignore'), so a malicious
    client cannot escalate by posting {"is_superuser": true}.
    """
    model_config = ConfigDict(extra='ignore')

    full_name: Optional[str] = None
    phone: Optional[str] = None
    country_code: Optional[str] = None
    address: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    bio: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    gender: Optional[str] = None
    avatar_url: Optional[str] = None

    @field_validator('full_name')
    @classmethod
    def validate_full_name(cls, v):
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError('Full name cannot be empty')
        return stripped


class UserLogin(BaseModel):
    """Schema for user login"""
    email: EmailStr
    password: str


class PasswordChange(BaseModel):
    """Schema for changing password"""
    old_password: str
    new_password: str
    
    @field_validator('new_password')
    @classmethod
    def validate_password_strength(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[0-9]', v):
            raise ValueError('Password must contain at least one number')
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', v):
            raise ValueError('Password must contain at least one special character')
        return v


class UserResponse(UserBase):
    """Schema for user response"""
    id: UUID4
    bio: Optional[str] = None
    is_active: bool
    is_superuser: bool
    is_activated: bool = False
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class UserAdminResponse(UserResponse):
    """Schema for admin view of users with activation code"""
    activation_code: Optional[str] = None


class Token(BaseModel):
    """Schema for JWT token response"""
    access_token: str
    token_type: str = "bearer"
    is_activated: bool = False
    is_superuser: bool = False
    user: Optional[UserResponse] = None


class TokenData(BaseModel):
    """Schema for token data"""
    user_id: Optional[str] = None


class ActivationCodeRequest(BaseModel):
    """Schema for activation code verification"""
    activation_code: str
