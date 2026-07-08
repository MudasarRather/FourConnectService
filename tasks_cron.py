import os
import sys

# Prime platform cache BEFORE importing app/* — SQLAlchemy's import path can hit
# platform.uname() → WMI, which intermittently hangs on this box. Mirrors
# C:/tmp/run_server.py so the scheduled run never wedges on a stuck Winmgmt.
import platform
try:
    _ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
    _ur.__dict__["processor"] = "Intel"
    platform._uname_cache = _ur
    platform._Processor.get = staticmethod(lambda: "Intel")
except Exception:
    pass

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

        # 6. Travel lifecycle: auto-start tours on their departure date and
        #    auto-complete them after their return date (also runs in-process hourly).
        from app.utils.hr.travel.scheduler import run_travel_auto_transitions
        tv = run_travel_auto_transitions(db)
        print(f"- Travel auto-transitions: {tv}")

        # 7. Leave upkeep: monthly accrual (idempotent, quota-capped) + FY-rollover
        #    carry-forward on 01-Apr. Safe to run daily.
        from app.utils.hr.leave_maintenance import run_leave_maintenance
        lv = run_leave_maintenance(db)
        print(f"- Leave maintenance: {lv}")

        # 8. Notification scans: time-based HR events (birthday, work anniversary,
        #    probation ending, contract expiry, asset return due, missing attendance).
        #    Each routes through the NotificationRule matrix and is day-idempotent.
        from app.utils.hr.notification_scans import run_notification_scans
        ns = run_notification_scans(db)
        print(f"- Notification scans: {ns}")

        # 9. Support Desk: auto-close RESOLVED tickets whose reopen window has elapsed
        #    (resolved → closed after SUPPORT_RESOLVED_AUTOCLOSE_DAYS). Idempotent.
        from app.routers.support_desk._common import (
            auto_close_due_tickets, warn_cold_pending, auto_close_cold_pending,
        )
        sc = auto_close_due_tickets(db)
        print(f"- Support auto-close: {sc} ticket(s) closed.")

        # 10. Support Desk: pending-customer silence sweep — nudge requesters approaching the
        #     auto-close window, then auto-resolve tickets that stayed silent past it. Idempotent.
        pw = warn_cold_pending(db)
        pc = auto_close_cold_pending(db)
        print(f"- Support pending-customer sweep: {pw} nudged, {pc} auto-resolved.")

        # 11. Support Desk: pending-vendor OLA sweep — flag + auto-escalate hand-offs that
        #     blew past their expected-return date (idempotent; fires once per hand-off).
        from app.utils.support_desk.vendor import sweep_vendor_overdue
        vs = sweep_vendor_overdue(db)
        db.commit()
        print(f"- Support pending-vendor sweep: {vs} overdue hand-off(s) flagged/escalated.")

        # 12. Support Desk: on-hold sweeps — auto-resume holds whose hold_until release date
        #     has passed (SLA un-freezes, ticket returns to held_from_status), then nudge
        #     stale holds (no release date, unreviewed past STALE_HOLD_DAYS) for a hold
        #     review. Both idempotent / day-throttled.
        from app.routers.support_desk._common import auto_resume_expired_holds, remind_stale_holds
        ha = auto_resume_expired_holds(db)
        hs = remind_stale_holds(db)
        print(f"- Support on-hold sweep: {ha} auto-resumed, {hs} review nudge(s).")

        # 13. Support Desk: war-room update-cadence sweep — nudge owners whose promised
        #     stakeholder status update (next_update_due_at) has lapsed. Day-throttled per
        #     ticket; the Critical board list-load also runs it opportunistically.
        from app.routers.support_desk._common import sweep_update_overdue
        uo = sweep_update_overdue(db)
        print(f"- Support update-cadence sweep: {uo} overdue-update nudge(s).")

        # 14. Support Desk: SLA breach-flag sweep — flip the stored breach flags for idle
        #     tickets whose deadline silently passed (flags are otherwise write-path-only),
        #     stamping sla_*_breached_at + timeline activity + owner ping. MUST run BEFORE
        #     the escalation sweep below, which consumes these flags — this ordering lets a
        #     freshly-detected breach auto-escalate in the same cron run.
        from app.utils.support_desk.breach import sweep_sla_breach_flags
        bf = sweep_sla_breach_flags(db)
        if bf:
            db.commit()
        print(f"- Support breach-flag sweep: {bf} breach flag(s)/stamp(s) flipped.")

        # 15. Support Desk: escalation sweeps — auto-escalate SLA-resolution-breached,
        #     owned, actively-worked tickets EXACTLY ONCE (stamped via auto_escalated_at),
        #     then nudge owners whose escalation response clock lapsed unacknowledged.
        #     Both idempotent / day-throttled; the Escalated desk list-load also runs them.
        from app.utils.support_desk.escalation import (
            sweep_sla_breach_escalation, sweep_escalation_response_overdue)
        ae = sweep_sla_breach_escalation(db)
        if ae:
            db.commit()
        eo = sweep_escalation_response_overdue(db)
        print(f"- Support escalation sweep: {ae} auto-escalated, {eo} response-overdue nudge(s).")

        # 16. Support Desk: retention sweep — auto-archive CLOSED records older than
        #     SUPPORT_CLOSED_AUTOARCHIVE_DAYS into deep storage (auto_retention; legal-hold
        #     exempt). Runs AFTER the auto-close sweep so a record can close and age out in
        #     the correct order. Purge stays MANUAL (superuser) — never a cron action.
        from app.routers.support_desk._common import auto_archive_old_closed
        aa = auto_archive_old_closed(db)
        print(f"- Support retention sweep: {aa} closed record(s) auto-archived.")

        print("Success: All transitions processed.")
    except Exception as e:
        print(f"Error during task transitions: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    run_cron()
