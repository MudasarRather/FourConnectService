"""Read-only check that /auth/me now exposes the employee's work-location timezone.

Finds an employee whose work location has a timezone, mints a token for their
linked user, calls the LIVE /auth/me, and prints the resolved tz + name. Mutates
nothing.
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


def main():
    url = _env("DATABASE_URL").replace("postgresql+psycopg2://", "postgresql://")
    secret, alg = _env("SECRET_KEY"), _env("ALGORITHM", "HS256")
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute(
        "SELECT u.id, u.full_name, u.token_version, w.name, w.timezone "
        "FROM hr_employees e "
        "JOIN users u ON u.id = e.user_id "
        "JOIN hr_work_locations w ON w.id = e.work_location_id "
        "WHERE u.is_active = true AND w.timezone IS NOT NULL "
        "ORDER BY e.created_at DESC LIMIT 1;"
    )
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        print("No located employee found to test."); return
    uid, name, tv, wl_name, wl_tz = row
    print(f"Employee: {name}  | DB work location: {wl_name} ({wl_tz})")

    token = jwt.encode({"sub": str(uid), "tv": tv}, secret, algorithm=alg)
    req = urllib.request.Request(API, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    got_tz = data.get("work_location_timezone")
    got_name = data.get("work_location_name")
    print(f"/auth/me -> work_location_timezone={got_tz!r}  work_location_name={got_name!r}")
    print("RESULT:", "PASS" if got_tz == wl_tz else "FAIL")


if __name__ == "__main__":
    main()
