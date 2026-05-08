from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.allowed_employee import AllowedEmployee
from app.schemas.employee import EmployeeCreate, EmployeeResponse
from app.utils.dependencies import get_current_user
from app.models.user import User

router = APIRouter(prefix="/admin/employees", tags=["Admin Employees"])

@router.post("/", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
def add_allowed_employee(
    employee_data: EmployeeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Add a new employee to the whitelist (Superadmin only)
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    # Check if exists
    existing = db.query(AllowedEmployee).filter(
        AllowedEmployee.employee_code == employee_data.employee_code
    ).first()
    
    if existing:
        raise HTTPException(status_code=400, detail="Employee code already exists")
        
    new_employee = AllowedEmployee(
        employee_code=employee_data.employee_code,
        phone=employee_data.phone
    )
    
    db.add(new_employee)
    db.commit()
    db.refresh(new_employee)
    return new_employee

@router.get("/", response_model=List[EmployeeResponse])
def list_allowed_employees(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all whitelisted employees (Superadmin only)
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    return db.query(AllowedEmployee).order_by(AllowedEmployee.created_at.desc()).all()
