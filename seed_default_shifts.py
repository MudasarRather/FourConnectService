"""Seed the default General shift + assign it to any employee lacking an
EmployeeShiftAssignment.

Idempotent — safe to re-run. Always invoked from the backend root so
get_settings() resolves .env correctly.

Usage:
    cd C:\\Projects\\FourConnectService
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" seed_default_shifts.py
"""
from datetime import date, time

from app.database import SessionLocal, engine, Base
# Ensure all HR models are loaded so create_all() and FK resolution work
from app.models import hr  # noqa: F401
from app.models.hr.employee import Employee
from app.models.hr.shift import Shift, EmployeeShiftAssignment, ShiftType


DEFAULT_CODE = "GEN-09-18"


def main() -> None:
    # First-run safety: ensure tables exist (mirrors main.py startup).
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        shift = db.query(Shift).filter(Shift.code == DEFAULT_CODE).first()
        created_shift = False
        if not shift:
            shift = Shift(
                code=DEFAULT_CODE,
                name="General Shift",
                shift_type=ShiftType.GENERAL,
                start_time=time(9, 0),
                end_time=time(18, 0),
                break_minutes=60,
                grace_minutes=10,
                weekly_off_days=[5, 6],
                half_day_hours=4.0,
                full_day_hours=8.0,
                description="Default 9:00 – 18:00 general shift with 60 min break and 10 min grace.",
            )
            db.add(shift)
            db.flush()
            created_shift = True

        employees = (
            db.query(Employee)
            .outerjoin(EmployeeShiftAssignment, EmployeeShiftAssignment.employee_id == Employee.id)
            .filter(Employee.is_deleted == False)  # noqa: E712
            .filter(EmployeeShiftAssignment.id.is_(None))
            .all()
        )
        assigned = 0
        for emp in employees:
            eff_from = emp.joining_date or date.today()
            db.add(EmployeeShiftAssignment(
                employee_id=emp.id,
                shift_id=shift.id,
                effective_from=eff_from,
                is_default=True,
                notes="Seeded by seed_default_shifts.py",
            ))
            if not emp.shift_id:
                emp.shift_id = shift.id
            assigned += 1

        db.commit()
        print(f"created_shift={created_shift}  default_shift_code={DEFAULT_CODE}  newly_assigned={assigned}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
