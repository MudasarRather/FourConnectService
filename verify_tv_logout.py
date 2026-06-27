"""Verify the missing-`tv`-as-version-1 fix (read-only; mints tokens locally).

  Razeya (token_version bumped to 3): no-`tv` token  -> 401 (booted)  + matching tv=3 -> 200
  A default-version user (token_version=1): no-`tv` token -> 200 (NOT mass-logged-out)
"""
import json, re, urllib.error, urllib.request
import psycopg2
from jose import jwt

API = "http://127.0.0.1:8000/api/auth/me"
env = open(".env", encoding="utf-8").read()
g = lambda k: re.search(rf"^\s*{k}\s*=\s*(.+?)\s*$", env, re.MULTILINE).group(1).strip()
url = g("DATABASE_URL").replace("postgresql+psycopg2://", "postgresql://")
secret, alg = g("SECRET_KEY"), g("ALGORITHM")

conn = psycopg2.connect(url); cur = conn.cursor()
cur.execute("SELECT id, full_name, token_version FROM users WHERE email = 'razeya@fourreck.com';")
rz = cur.fetchone()
cur.execute("SELECT id, full_name, token_version FROM users WHERE is_active = true AND token_version = 1 AND email <> 'razeya@fourreck.com' ORDER BY created_at LIMIT 1;")
df = cur.fetchone()
cur.close(); conn.close()

def me(claims):
    tok = jwt.encode(claims, secret, algorithm=alg)
    req = urllib.request.Request(API, headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return r.status
    except urllib.error.HTTPError as e: return e.code

print(f"Razeya token_version={rz[2]}")
r_legacy = me({"sub": str(rz[0])})              # no tv  -> treated as v1 -> mismatch (3)
r_match  = me({"sub": str(rz[0]), "tv": rz[2]})  # tv=3   -> match
print(f"  Razeya no-tv (pre-feature token)  -> {r_legacy}  (expect 401 = booted)")
print(f"  Razeya tv={rz[2]} (fresh login)        -> {r_match}  (expect 200)")

print(f"Default user {df[1]} token_version={df[2]}")
d_legacy = me({"sub": str(df[0])})              # no tv -> v1 == v1 -> stays
print(f"  no-tv (pre-feature token)         -> {d_legacy}  (expect 200 = NOT logged out)")

ok = r_legacy == 401 and r_match == 200 and d_legacy == 200
print("RESULT:", "PASS" if ok else "FAIL")
