import os
import sys

# Add current directory to path so we can import app
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal
from app.routers.tasks import check_update_upcoming_tasks, check_update_expired_tasks
from app.routers.hr.employee_documents import check_document_expiry_alerts

def run_cron():
    db = SessionLocal()
    try:
        print("Running scheduled task transitions...")

        # 1. UPCOMING <-> OPEN transitions
        check_update_upcoming_tasks(db)
        print("- Upcoming/Open status checks completed.")

        # 2. OPEN -> EXPIRED transitions
        check_update_expired_tasks(db)
        print("- Expired status checks completed.")

        # 3. Employee-document expiry: flip EXPIRED + 90/60/30/7-day reminders
        result = check_document_expiry_alerts(db)
        print(f"- Document expiry checks completed: {result}")

        # 4. Attendance finalizer: stamp ABSENT / HALF_DAY / WEEK_OFF etc. for
        #    shifts that have ended (also runs in-process on a 15-min thread).
        from app.utils.hr.attendance_finalizer import finalize_due_attendance
        n = finalize_due_attendance(db)
        print(f"- Attendance finalizer processed {n} record(s) for today.")

        # 5. Training & Development: certification-expiry sweep + compliance
        #    auto-reassign (also runs in-process on a 6-hour thread).
        from app.utils.hr.training.expiry_monitor import run_training_maintenance
        tr = run_training_maintenance(db)
        print(f"- Training maintenance: {tr}")

        print("Success: All transitions processed.")
    except Exception as e:
        print(f"Error during task transitions: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    run_cron()
