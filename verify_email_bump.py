"""Controlled live test: does changing an employee's email via PATCH /hr/employees
/{pk} bump the linked user's token_version? Changes Razeya's email to a temp value,
checks token_version, then RESTORES the original email. Net: email unchanged,
token_version bumped (which is the intended force-logout signal).
"""
import json
import re
import urllib.error
import urllib.request

import psycopg2
from jose import jwt

EMP_PK = "0c391540-2272-4662-b17a-897518334302"  # Razeya (from the profile URL)
BASE = "http://127.0.0.1:8000/api"

env = open(".env", encoding="utf-8").read()
g = lambda k: re.search(rf"^\s*{k}\s*=\s*(.+?)\s*$", env, re.MULTILINE).group(1).strip()
url = g("DATABASE_URL").replace("postgresql+psycopg2://", "postgresql://")
secret, alg = g("SECRET_KEY"), g("ALGORITHM")

conn = psycopg2.connect(url)
cur = conn.cursor()
cur.execute("SELECT id, token_version FROM users WHERE is_superuser = true ORDER BY created_at LIMIT 1;")
admin_id, admin_tv = cur.fetchone()
cur.execute("SELECT u.id, u.email, u.token_version FROM hr_employees e JOIN users u ON u.id = e.user_id WHERE e.id = %s;", (EMP_PK,))
ru_id, orig_email, tv_before = cur.fetchone()
cur.close()

tok = jwt.encode({"sub": str(admin_id), "tv": admin_tv}, secret, algorithm=alg)

def patch_email(email):
    req = urllib.request.Request(
        f"{BASE}/hr/employees/{EMP_PK}", data=json.dumps({"email": email}).encode(),
        method="PATCH", headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"{e.code}: {e.read().decode()[:200]}"

def tv_of(uid):
    cu = conn.cursor(); cu.execute("SELECT token_version, email FROM users WHERE id = %s;", (uid,))
    v = cu.fetchone(); cu.close(); return v

print(f"Razeya: {orig_email}  token_version(before)={tv_before}")
st1 = patch_email("razeya.tvtest@fourreck.com")
tv_mid, email_mid = tv_of(ru_id)
print(f"PATCH email->temp : http {st1} | token_version={tv_mid}, email={email_mid}")
st2 = patch_email(orig_email)
tv_after, email_after = tv_of(ru_id)
print(f"PATCH email->orig : http {st2} | token_version={tv_after}, email={email_after}")
conn.close()
print("BUMP-ON-EMAIL-CHANGE WORKS:", tv_mid == tv_before + 1 and tv_after == tv_mid + 1)
