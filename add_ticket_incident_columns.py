"""Add the Support Desk Incident-Management columns to the live DB.

Run standalone:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_ticket_incident_columns.py

Adds the MI command roster (incident_commander_id / comms_lead_id / ops_lead_id),
impact detail (affected_services JSONB, incident_started_at / incident_detected_at,
compliance_impact / security_impact / public_impact), parent/child linking
(parent_incident_id) + the commander/parent indexes for the Incident Management
module ("Fault Grid" agent desk / "Command Funnel" admin desk).
The PIR table (support_incident_reports) auto-creates via create_all — only these
ticket-column ALTERs need a script. Reads DATABASE_URL straight from .env (relative
to this file) so it always hits the SAME database the backend uses —
`pydantic-settings` resolves .env relative to CWD, which silently falls back to the
local DB when invoked from elsewhere (see CLAUDE.md "the live DB is REMOTE").
Idempotent: ADD COLUMN IF NOT EXISTS.
"""
import os
import re
import sys

from sqlalchemy import create_engine

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.utils.support_desk.migrate import ensure_ticket_incident_columns


def _database_url() -> str:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*DATABASE_URL\s*=\s*(.+)\s*$", line)
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    from app.config import get_settings
    return get_settings().DATABASE_URL


if __name__ == "__main__":
    url = _database_url()
    safe = re.sub(r"//([^:]+):[^@]+@", r"//\1:***@", url)
    print(f"Connecting to: {safe}")
    engine = create_engine(url, future=True)
    applied = ensure_ticket_incident_columns(engine)
    print(f"Applied {len(applied)} statement(s):")
    for stmt in applied:
        print(f"  - {stmt}")
    print("Done.")
