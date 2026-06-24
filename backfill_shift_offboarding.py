"""One-off, idempotent backfill: apply shift-offboarding cleanup to employees
who already gave notice / exited BEFORE the offboarding hook existed.

For every employee in a leaving/separated state (ON_NOTICE, EXITED, ARCHIVED,
INACTIVE) with a recorded last_working_date, caps/removes any shift assignment
that runs past that date — exactly what the live hook now does on transition.

Safe to re-run: already-capped assignments are no-ops.

Run from the backend root:
  & "<python>" C:\\Projects\\FourConnectService\\backfill_shift_offboarding.py
"""
import os
import sys

import platform
_ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
_ur.__dict__["processor"] = "Intel"
platform._uname_cache = _ur
platform._Processor.get = staticmethod(lambda: "Intel")

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import app.main  # noqa: F401,E402 — registers the full model registry
from app.database import SessionLocal  # noqa: E402
from app.models.hr.employee import Employee  # noqa: E402
from app.utils.hr.lifecycle_guard import LEAVING_OR_GONE  # noqa: E402
from app.utils.hr.shift_offboarding import close_shift_assignments_on_separation  # noqa: E402


def main():
    db = SessionLocal()
    try:
        emps = (
            db.query(Employee)
            .filter(
                Employee.lifecycle_state.in_(LEAVING_OR_GONE),
                Employee.last_working_date.isnot(None),
            )
            .all()
        )
        print(f"Leaving/separated employees with a last working day: {len(emps)}")
        tot_capped = tot_removed = touched = 0
        for e in emps:
            r = close_shift_assignments_on_separation(db, e, None)
            if r["capped"] or r["removed"]:
                touched += 1
                tot_capped += r["capped"]
                tot_removed += r["removed"]
                label = getattr(e, "employee_id", None) or str(e.id)[:8]
                print(f"  {label} ({e.lifecycle_state.value if hasattr(e.lifecycle_state,'value') else e.lifecycle_state}, "
                      f"LWD {e.last_working_date}): capped={r['capped']} removed={r['removed']}")
        db.commit()
        print(f"\nCommitted. Employees touched: {touched}, capped={tot_capped}, removed={tot_removed}.")
    except Exception as exc:
        db.rollback()
        print(f"ROLLED BACK — {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
