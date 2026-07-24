"""Non-disruptive in-process probe of GET /incidents/command-dashboard.
Calls the handler directly against the live DB (no HTTP, no server restart) to
confirm the new _dashboard_extras / _admin_block helpers run and shape is sane.
Run FROM C:\\Projects\\FourConnectService so .env resolves to the remote DB."""
import sys, traceback
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def main():
    import app.main  # noqa: F401 — loads ALL models/routers so SQLAlchemy mappers fully configure
    from app.database import SessionLocal
    from app.models.user import User
    from app.routers.support_desk.incidents import command_dashboard

    db = SessionLocal()
    try:
        su = db.query(User).filter(User.is_superuser == True).first()  # noqa: E712
        agent = (db.query(User).filter(User.is_superuser == False)  # noqa: E712
                 .filter(User.is_active == True).first())  # noqa: E712
        if not su:
            print("NO superuser found — cannot probe admin block"); return 2

        # ── superuser path (whole desk + admin block) ──
        r = command_dashboard(db=db, admin=su)
        a = r.agent
        print("── SUPERUSER ──")
        print("  agent.active_total   :", a.active_total)
        print("  agent.by_sev         :", a.by_sev)
        print("  agent.critical.exposure:", getattr(a.critical, "exposure", None))
        print("  extras.aging_ladder  :", len(r.extras.aging_ladder), "buckets")
        print("  extras.escalation    :", r.extras.escalation)
        print("  extras.war_rooms     :", r.extras.war_rooms)
        print("  extras.quality       :", r.extras.quality)
        print("  extras.next_breach   :", r.extras.next_breach)
        print("  extras.tasks_live    :", r.extras.tasks_live)
        print("  is_superuser         :", r.is_superuser)
        assert r.admin is not None, "admin block missing for superuser"
        print("  admin.leaderboard    :", len(r.admin.leaderboard), "rows")
        print("  admin.per_team       :", len(r.admin.per_team), "teams")
        print("  admin.rca            :", r.admin.rca)
        print("  admin.pir            :", r.admin.pir)
        print("  admin.recurring      :", len(r.admin.recurring), "clusters")
        print("  admin.escalation_heatmap:", len(r.admin.escalation_heatmap), "cells")
        print("  admin.busy_hours     :", len(r.admin.busy_hours), "cells")

        # ── agent path (team-sealed, no admin block) ──
        if agent:
            r2 = command_dashboard(db=db, admin=agent)
            print("── AGENT (team-sealed) ──")
            print("  agent.active_total   :", r2.agent.active_total)
            print("  extras.aging_ladder  :", len(r2.extras.aging_ladder))
            print("  admin block          :", "present (BUG!)" if r2.admin else "None (correct)")
        else:
            print("── AGENT ── (no non-superuser user to test the sealed path)")

        print("\nPROBE OK")
        return 0
    finally:
        db.close()

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc(); sys.exit(1)
