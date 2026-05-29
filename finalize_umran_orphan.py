"""Run the orphan-punch finalizer for Umran so May 28 picks up the implicit OUT
immediately. Also fixes the late_minutes via the timezone-corrected rollup.
"""
import re, sys
from pathlib import Path

# Ensure we load the right .env (the backend always runs from this dir, so cd
# here before importing app modules).
import os
os.chdir(r"C:/Projects/FourConnectService")
sys.path.insert(0, r"C:/Projects/FourConnectService")

from app.database import SessionLocal
from app.models.user import User
from app.models.hr.employee import Employee
from app.utils.hr.attendance_logic import finalize_orphan_open_punches, daily_rollup
from datetime import date, timedelta

db = SessionLocal()
try:
    u = db.query(User).filter(User.email.ilike("%umran%")).first()
    if not u:
        print("No Umran user."); sys.exit(0)
    emp = db.query(Employee).filter(Employee.user_id == u.id, Employee.is_deleted == False).first()
    if not emp:
        print("No employee row for Umran."); sys.exit(0)

    print(f"Umran emp_id = {emp.id}")
    finalized = finalize_orphan_open_punches(db, emp.id, lookback_days=14, actor_id=None)
    db.commit()
    print(f"Finalized {finalized} orphan day(s).")

    # Also re-roll the past 14 days so the timezone-corrected late_minutes
    # / overtime_hours land in existing rows (the original rollup wrote 0).
    today = date.today()
    for n in range(1, 15):
        d = today - timedelta(days=n)
        daily_rollup(db, emp.id, d, actor_id=None)
    db.commit()
    print("Re-rolled prior 14 days for timezone-corrected metrics.")
finally:
    db.close()
