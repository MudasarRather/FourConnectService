"""Add the Support Desk PIR v2 parity-pack columns to the live DB.

Run standalone:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_support_pir_v2_columns.py

Adds the Post-Incident Review v2 document columns on support_incident_reports
(frozen metrics_snapshot, contributing_factors / went_well / went_wrong retro
registers, participants roster, review_meeting_at (+index) / review_meeting_notes,
revisions trail, distribution receipt) and backfills a stable ``aid`` onto every
existing corrective/preventive action item (deterministic md5-derived 8-hex, so
re-running never rewrites addresses).

Reads DATABASE_URL straight from .env (relative to this file) so it always hits
the SAME database the backend uses — `pydantic-settings` resolves .env relative to
CWD, which silently falls back to the local DB when invoked from elsewhere (see
CLAUDE.md "the live DB is REMOTE"). Idempotent: ADD COLUMN IF NOT EXISTS + guarded
backfills.
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")   # cp1252 console vs unicode in labels

from sqlalchemy import create_engine

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.utils.support_desk.migrate import ensure_pir_v2_columns


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
    applied = ensure_pir_v2_columns(engine)
    print(f"Applied {len(applied)} statement(s):")
    for stmt in applied:
        print(f"  - {stmt}")
    print("Done.")
