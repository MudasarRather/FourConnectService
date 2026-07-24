"""Seed ONE live in_review PIR via workflow verbs only (for the admin-desk UI drive).
Prints the ticket + pir ids; pass --cleanup <ticket_id> to archive afterwards."""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import platform
try:
    _ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
    _ur.__dict__["processor"] = "Intel"
    platform._uname_cache = _ur
    platform._Processor.get = staticmethod(lambda: "Intel")
except Exception:
    pass

from sqlalchemy import text
from app.database import SessionLocal
from app.utils.auth import create_access_token

BASE = "http://127.0.0.1:8000/api"


def req(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


db = SessionLocal()
su = db.execute(text("SELECT id, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1")).fetchone()
su_tok = create_access_token({"sub": str(su[0]), "tv": su[1] or 1})

if "--cleanup" in sys.argv:
    tid = sys.argv[sys.argv.index("--cleanup") + 1]
    s, _ = req("DELETE", f"/support-desk/tickets/{tid}?reason=e2e%20cleanup", su_tok)
    print(f"cleanup {tid}: {s}")
    db.close()
    sys.exit(0)

teams = db.execute(text(
    "SELECT id, member_ids FROM support_teams WHERE is_deleted = FALSE AND is_active = TRUE AND lead_user_id IS NOT NULL LIMIT 1"
)).fetchone()
member = str((teams[1] or [None])[0] or su[0])

s, t = req("POST", "/support-desk/tickets/", su_tok, {
    "subject": "e2e-ui: checkout latency spike across two regions",
    "description": "UI drive seed - safe to archive", "priority": "critical",
    "ticket_type": "incident", "source": "internal"})
tid = t["id"]
db.execute(text("UPDATE support_tickets SET team_id = :tm WHERE id = :i"), {"tm": str(teams[0]), "i": tid})
db.commit()
req("POST", f"/support-desk/tickets/{tid}/assign", su_tok, {"assigned_agent_id": member})
req("POST", f"/support-desk/tickets/{tid}/resolve", su_tok, {
    "resolution_code": "solved", "resolution_summary": "Rolled back the routing change; latency nominal.", "close": False})
s, p = req("POST", f"/support-desk/tickets/{tid}/pir", su_tok, {"title": "Checkout latency spike — post-incident review"})
pid = p["id"]
tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=10, minute=0).isoformat()
req("PATCH", f"/support-desk/incidents/pirs/{pid}", su_tok, {
    "executive_summary": "Checkout p99 rose from 900ms to 9.4s for 84 minutes after a routing change halved capacity to the payments edge. Rollback restored service; all mitigations verified in production.",
    "business_impact": "Conversion fell 14 points at peak; 41 support contacts; no contractual breach.",
    "technical_impact": "Edge pool at 100% utilisation; retry amplification pushed p99 to 9.4s.",
    "root_cause": "Routing change promoted from staging halved effective edge capacity; the capacity review step was self-attested and skipped.",
    "root_cause_category": "configuration",
    "five_whys": ["Checkout latency spiked - requests queued past deadline",
                   "Edge capacity halved by the routing change",
                   "Stale staging values promoted to production",
                   "Capacity review step skipped - marked N/A by default",
                   "Change policy allowed self-attestation for config diffs"],
    "contributing_factors": ["stale staging profile", "self-attested change class", "no utilisation alert"],
    "went_well": ["Paging fired inside the detection SLO", "Rollback was rehearsed and clean"],
    "went_wrong": ["No pre-breach utilisation alert", "Status page lagged the internal ack"],
    "participants": [{"name": "Priya Nair", "role": "commander"}, {"name": "Dev Patel", "role": "ops"}],
    "review_meeting_at": tomorrow,
    "review_meeting_notes": "Blameless review on the bridge; ratify both preventive actions.",
    "corrective_actions": [{"action": "Capacity-ceiling audit across payment edge regions", "status": "open"}],
    "preventive_actions": [{"action": "Remove self-attestation for capacity-touching diffs", "status": "open"}],
    "lessons_learned": "Capacity-touching diffs are change-class critical regardless of line count."})
s, sub = req("POST", f"/support-desk/incidents/pirs/{pid}/submit", su_tok)
print(f"TICKET={tid}")
print(f"PIR={pid}")
print(f"STATUS={sub.get('status')}")
db.close()
