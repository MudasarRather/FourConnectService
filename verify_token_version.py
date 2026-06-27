"""Read-only verification of the token_version session-invalidation logic.

Mints three JWTs for a real active user and hits the LIVE /auth/me:
  - tv == DB value   -> 200 (valid session)
  - tv != DB value   -> 401 (simulates a token issued before an email/password change)
  - no tv claim       -> 200 (legacy token, intentionally not force-logged-out)

Mutates NOTHING — it only reads the user's current token_version and forges
tokens locally with the app SECRET_KEY. Safe to run against the live DB/server.
"""
import json
import os
import re
import urllib.request

import psycopg2
from jose import jwt

HERE = os.path.dirname(os.path.abspath(__file__))
API = "http://127.0.0.1:8000/api/auth/me"


def _env(key, default=None):
    with open(os.path.join(HERE, ".env"), encoding="utf-8") as fh:
        m = re.search(rf"^\s*{key}\s*=\s*(.+?)\s*$", fh.read(), re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else default


def _me(token):
    req = urllib.request.Request(API, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def main():
    url = _env("DATABASE_URL").replace("postgresql+psycopg2://", "postgresql://")
    secret = _env("SECRET_KEY")
    alg = _env("ALGORITHM", "HS256")

    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute("SELECT id, token_version, email FROM users WHERE is_active = true ORDER BY created_at LIMIT 1;")
    uid, tv, email = cur.fetchone()
    cur.close()
    conn.close()
    print(f"Test user: {email}  (token_version={tv})")

    valid = jwt.encode({"sub": str(uid), "tv": tv}, secret, algorithm=alg)
    stale = jwt.encode({"sub": str(uid), "tv": tv + 1}, secret, algorithm=alg)
    legacy = jwt.encode({"sub": str(uid)}, secret, algorithm=alg)

    results = {
        "matching tv  -> expect 200": _me(valid),
        "stale tv     -> expect 401": _me(stale),
        "no tv (legacy) -> expect 200": _me(legacy),
    }
    print(json.dumps(results, indent=2))
    ok = results["matching tv  -> expect 200"] == 200 and \
        results["stale tv     -> expect 401"] == 401 and \
        results["no tv (legacy) -> expect 200"] == 200
    print("RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
