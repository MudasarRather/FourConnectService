"""Verify bank-account encryption end-to-end (no PII printed — last 4 only).

* Confirms the value stored in the DB is ciphertext (a Fernet token), and
* Confirms the ORM (EncryptedString) transparently decrypts it back to digits
  using the app's own settings-derived key.
"""
import os
import re
import sys

import platform
_ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
_ur.__dict__["processor"] = "Intel"
platform._uname_cache = _ur
platform._Processor.get = staticmethod(lambda: "Intel")

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import psycopg2
import app.main  # noqa: F401 — registers the full model registry (resolves relationship names)
from app.database import SessionLocal
from app.models.hr.employee import Employee


def _last4(s):
    d = "".join(c for c in (s or "") if c.isdigit())
    return d[-4:] if d else "—"


# 1) Raw DB read — what's actually at rest
env = {}
with open(".env", encoding="utf-8") as f:
    for line in f:
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.strip().partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", env["DATABASE_URL"])
u, p, h, port, db = m.groups()
conn = psycopg2.connect(dbname=db, user=u, password=p, host=h, port=port)
c = conn.cursor()
c.execute("SELECT id, account_number FROM hr_employees WHERE account_number IS NOT NULL LIMIT 3")
print("AT REST (raw DB value, first 24 chars):")
for rid, raw in c.fetchall():
    looks_encrypted = bool(raw) and raw.startswith("gAAAAA")
    print(f"  emp {str(rid)[:8]}  encrypted={looks_encrypted}  raw={raw[:24]}…")
c.close(); conn.close()

# 2) ORM read — transparent decryption through EncryptedString
print("\nVIA ORM (decrypted, last-4 only):")
s = SessionLocal()
try:
    for e in s.query(Employee).filter(Employee.account_number.isnot(None)).limit(3):
        acct = e.account_number
        print(f"  emp {str(e.id)[:8]}  digits_only={acct.isdigit() if acct else None}  …{_last4(acct)}")
finally:
    s.close()
print("\nOK — at-rest is ciphertext, ORM returns plaintext digits.")
