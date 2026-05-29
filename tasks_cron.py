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

        print("Success: All transitions processed.")
    except Exception as e:
        print(f"Error during task transitions: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    run_cron()
