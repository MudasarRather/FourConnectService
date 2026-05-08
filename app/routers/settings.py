
from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.database import get_db
from app.models.user import User
from app.models.system_setting import SystemSetting
from app.utils.dependencies import get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])

def get_superadmin(current_user: User = Depends(get_current_user)):
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required"
        )
    return current_user


@router.get("/currencies")
def get_currencies():
    """Get supported currencies"""
    return [
        {"code": "USD", "symbol": "$", "name": "US Dollar"},
        {"code": "EUR", "symbol": "€", "name": "Euro"},
        {"code": "GBP", "symbol": "£", "name": "British Pound"},
        {"code": "INR", "symbol": "₹", "name": "Indian Rupee"},
        {"code": "AED", "symbol": "AED", "name": "United Arab Emirates Dirham"},
    ]


@router.get("/project-types")
def get_project_types():
    """Get available project types for dropdown"""
    return [
        {"id": "software", "name": "Software Development"},
        {"id": "infrastructure", "name": "Infrastructure"},
        {"id": "consulting", "name": "Consulting"},
        {"id": "research", "name": "Research & Development"},
        {"id": "marketing", "name": "Marketing Campaign"},
        {"id": "operations", "name": "Operations"},
        {"id": "hr", "name": "Human Resources"},
        {"id": "finance", "name": "Finance & Accounting"},
        {"id": "it_support", "name": "IT Support"},
        {"id": "other", "name": "Other"},
    ]


@router.get("/cost-centers")
def get_cost_centers():
    """Get available cost centers for dropdown"""
    return [
        {"id": "cc-tech", "name": "Technology"},
        {"id": "cc-sales", "name": "Sales & Marketing"},
        {"id": "cc-ops", "name": "Operations"},
        {"id": "cc-admin", "name": "Administration"},
        {"id": "cc-hr", "name": "Human Resources"},
        {"id": "cc-fin", "name": "Finance"},
        {"id": "cc-legal", "name": "Legal & Compliance"},
        {"id": "cc-rnd", "name": "Research & Development"},
        {"id": "cc-supply", "name": "Supply Chain"},
        {"id": "cc-customer", "name": "Customer Service"},
        {"id": "cc-general", "name": "General & Administrative"},
    ]


@router.get("/", response_model=List[Dict[str, Any]])
def get_all_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin)
):
    """Get all system settings"""
    settings = db.query(SystemSetting).all()
    return [{"key": s.key, "value": s.value, "description": s.description} for s in settings]

@router.get("/{key}")
def get_setting(
    key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific setting by key"""
    setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")
    return {"key": setting.key, "value": setting.value}

@router.put("/{key}")
def update_setting(
    key: str,
    value: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin)
):
    """Update a system setting"""
    setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not setting:
        setting = SystemSetting(key=key, value=value)
        db.add(setting)
    else:
        setting.value = value
    
    db.commit()
    return {"message": "Setting updated", "key": key, "value": value}
