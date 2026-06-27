"""Verify the account-provisioning password-reset fix against the LIVE API.

  A) set-credentials with a BLANK password -> 400 (the silent no-op is closed);
     old password must still verify (proves A changed nothing).
  B) set-credentials with a real NEW password -> 200; the old password
     (@Er5ty2w) must STOP verifying, the new one must work, token_version bumps.

Leaves Razeya's password set to NEW_PWD (printed below) — the old @Er5ty2w is
intentionally killed (that was the whole point). Admin should re-reset to their
own choice via the now-fixed UI.
"""
import json, re, urllib.error, urllib.request
import psycopg2
from jose import jwt
from passlib.context import CryptContext

OLD_PWD = "@Er5ty2w"
NEW_PWD = "Reset#Fc2026"
BASE = "http://127.0.0.1:8000/api"
ctx = CryptContext(schemes=["argon2"], deprecated="auto")

env = open(".env", encoding="utf-8").read()
g = lambda k: re.search(rf"^\s*{k}\s*=\s*(.+?)\s*$", env, re.MULTILINE).group(1).strip()
url = g("DATABASE_URL").replace("postgresql+psycopg2://", "postgresql://")
secret, alg = g("SECRET_KEY"), g("ALGORITHM")

conn = psycopg2.connect(url); cur = conn.cursor()
cur.execute("SELECT id, token_version FROM users WHERE is_superuser = true ORDER BY created_at LIMIT 1;")
admin_id, admin_tv = cur.fetchone()
cur.execute("""SELECT ap.id FROM hr_account_provisioning ap
               JOIN hr_employees e ON e.id = ap.employee_id
               JOIN users u ON u.id = e.user_id
               WHERE u.email = 'razeya@fourreck.com' AND ap.account_type::text ILIKE '%ERP%';""")
r = cur.fetchone()
cur.close()
if not r:
    print("No ERP provisioning row for Razeya — cannot test set-credentials."); conn.close(); raise SystemExit
ap_id = r[0]
tok = jwt.encode({"sub": str(admin_id), "tv": admin_tv}, secret, algorithm=alg)

def set_creds(body):
    req = urllib.request.Request(f"{BASE}/hr/account-provisioning/{ap_id}/set-credentials",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp: return resp.status, ""
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:160]

def hash_tv():
    cu = conn.cursor(); cu.execute("SELECT hashed_password, token_version FROM users WHERE email='razeya@fourreck.com';")
    v = cu.fetchone(); cu.close(); return v

h0, tv0 = hash_tv()
print(f"before: token_version={tv0} | @Er5ty2w verifies={ctx.verify(OLD_PWD, h0)}")

# A) blank password — must be rejected now
sa, body = set_creds({"password": None, "auto_generate": False, "activate": True})
hA, tvA = hash_tv()
print(f"A) blank reset -> http {sa} {body!r} | hash unchanged={hA==h0} | old still works={ctx.verify(OLD_PWD, hA)}")

# B) real new password — must take effect
sb, _ = set_creds({"password": NEW_PWD, "auto_generate": False, "activate": True})
hB, tvB = hash_tv()
print(f"B) real reset  -> http {sb} | old @Er5ty2w works={ctx.verify(OLD_PWD, hB)} | new {NEW_PWD!r} works={ctx.verify(NEW_PWD, hB)} | token_version {tv0}->{tvB}")
conn.close()

ok = sa == 400 and hA == h0 and sb == 200 and (not ctx.verify(OLD_PWD, hB)) and ctx.verify(NEW_PWD, hB) and tvB > tv0
print("RESULT:", "PASS" if ok else "FAIL")
