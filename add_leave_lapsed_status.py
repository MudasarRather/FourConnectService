"""Add the LAPSED value to the hr_leave_status Postgres enum.

Idempotent (ADD VALUE IF NOT EXISTS). Must run with AUTOCOMMIT — Postgres forbids
ALTER TYPE ... ADD VALUE inside a transaction block. Run from the backend root so
.env resolves to the live DB:

    python add_leave_lapsed_status.py
"""
import platform
_ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
_ur.__dict__["processor"] = "Intel"
platform._uname_cache = _ur
platform._Processor.get = staticmethod(lambda: "Intel")

import os
import sys

BACKEND = r"C:\Projects\FourConnectService"
os.chdir(BACKEND)
sys.path.insert(0, BACKEND)

from sqlalchemy import text
from app.database import engine

with engine.connect() as conn:
    conn = conn.execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(text("ALTER TYPE hr_leave_status ADD VALUE IF NOT EXISTS 'LAPSED'"))
    rows = conn.execute(text(
        "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid "
        "WHERE t.typname = 'hr_leave_status' ORDER BY e.enumsortorder"
    )).fetchall()
    print("hr_leave_status values:", [r[0] for r in rows])
