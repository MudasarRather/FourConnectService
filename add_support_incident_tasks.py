"""Create the Support Desk incident-tasks table on the live DB.

Run standalone:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" add_support_incident_tasks.py

Creates ``support_incident_tasks`` (response playbooks / incident tasks for the
Critical "Fault Grid" / "Command Funnel" desks) via the ORM DDL with checkfirst.
Reads DATABASE_URL straight from .env (relative to this file) so it always hits
the SAME database the backend uses — `pydantic-settings` resolves .env relative
to CWD, which silently falls back to the local DB when invoked from elsewhere
(see CLAUDE.md "the live DB is REMOTE"). Idempotent: CREATE TABLE checkfirst.
"""
import os
import re
import sys

# WMI-hang guard (same as run_server.py) BEFORE any SQLAlchemy import — its
# cyextension import chain calls platform.uname(), which blocks on a wedged WMI.
import platform
try:
    _ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
    _ur.__dict__["processor"] = "Intel"
    platform._uname_cache = _ur
    platform._Processor.get = staticmethod(lambda: "Intel")
except Exception:
    pass

from sqlalchemy import create_engine

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.utils.support_desk.migrate import ensure_incident_tasks_table


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
    applied = ensure_incident_tasks_table(engine)
    print(f"Applied {len(applied)} statement(s):")
    for stmt in applied:
        print(f"  - {stmt}")
    print("Done.")
