"""Mint superuser + support-agent JWTs for the breached-desk headless smoke.
Writes c:/tmp/sd_brc_tokens.json. Run from the backend root so .env resolves."""
import json
import os
import sys

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

db = SessionLocal()
su = db.execute(text("SELECT id, token_version FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1")).fetchone()
ag = db.execute(text("SELECT id, token_version FROM users WHERE is_superuser = FALSE AND is_support_agent = TRUE AND is_active = TRUE LIMIT 1")).fetchone()
out = {
    "admin": create_access_token({"sub": str(su[0]), "tv": su[1] or 1}) if su else None,
    "agent": create_access_token({"sub": str(ag[0]), "tv": ag[1] or 1}) if ag else None,
}
with open("c:/tmp/sd_brc_tokens.json", "w") as fh:
    json.dump(out, fh)
db.close()
print("[OK] tokens written: admin=%s agent=%s" % (bool(out["admin"]), bool(out["agent"])))
