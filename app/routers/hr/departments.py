from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.department import Department
from app.models.hr.employee import Employee
from app.schemas.hr.department import DepartmentCreate, DepartmentUpdate, DepartmentResponse
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/departments", tags=["HR — Departments"])


def _track_actor(db: Session, actor_id):
    """Stash actor id on the session so audit listeners can pick it up."""
    db.info["audit_actor_id"] = str(actor_id)


@router.get("/", response_model=List[DepartmentResponse])
def list_departments(
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(Department)
    if not include_deleted:
        q = q.filter(Department.is_deleted == False)  # noqa: E712
    return q.order_by(Department.name).all()


@router.post("/", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    if db.query(Department).filter(Department.code == payload.code).first():
        raise HTTPException(400, "Department code already exists")
    dept = Department(**payload.model_dump(exclude_unset=True))
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept


@router.get("/{dept_id}", response_model=DepartmentResponse)
def get_department(
    dept_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    dept = db.query(Department).filter(Department.id == dept_id).first()
    if not dept:
        raise HTTPException(404, "Department not found")
    return dept


@router.patch("/{dept_id}", response_model=DepartmentResponse)
def update_department(
    dept_id: UUID,
    payload: DepartmentUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    dept = db.query(Department).filter(Department.id == dept_id).first()
    if not dept:
        raise HTTPException(404, "Department not found")
    update = payload.model_dump(exclude_unset=True)
    if "code" in update and update["code"] != dept.code:
        if db.query(Department).filter(Department.code == update["code"], Department.id != dept_id).first():
            raise HTTPException(400, "Department code already exists")
    for k, v in update.items():
        setattr(dept, k, v)
    db.commit()
    db.refresh(dept)
    return dept


@router.delete("/{dept_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(
    dept_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    dept = db.query(Department).filter(Department.id == dept_id).first()
    if not dept:
        raise HTTPException(404, "Department not found")
    # Block delete if active employees exist
    active = db.query(Employee).filter(
        Employee.department_id == dept_id,
        Employee.is_deleted == False,  # noqa: E712
    ).count()
    if active > 0:
        raise HTTPException(409, f"Cannot delete department with {active} active employees")
    dept.is_deleted = True
    db.commit()
    return None
