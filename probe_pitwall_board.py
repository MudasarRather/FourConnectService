"""Probe: does the live API serve the Pit Wall board telemetry?
Reads .env itself (cwd-proof), mints a superuser JWT (with tv), hits
/queues/tier/1/board and /serve-next gates. Self-contained, read-only
except one temporary agent-status flip that it restores."""
import re, sys, json
from pathlib import Path

import psycopg2, psycopg2.extras
import requests
from jose import jwt

ROOT = Path(__file__).resolve().parent
env = (ROOT / ".env").read_text(encoding="utf-8")
def _env(k, d=None):
    m = re.search(rf"^{k}=(.*)$", env, re.M)
    return m.group(1).strip() if m else d

db_url = _env("DATABASE_URL", "")
secret = _env("SECRET_KEY", "your-secret-key-here-change-this-in-production")
m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", db_url)
if not m:
    sys.exit("no DATABASE_URL in .env")
user, pw, host, port, dbname = m.groups()
psycopg2.extras.register_uuid()
conn = psycopg2.connect(user=user, password=pw, host=host, port=port, dbname=dbname)
cur = conn.cursor()
cur.execute("SELECT id, token_version FROM users WHERE is_superuser = true AND is_active = true LIMIT 1")
row = cur.fetchone()
if not row:
    sys.exit("no superuser found")
uid, tv = str(row[0]), row[1] or 0
token = jwt.encode({"sub": uid, "tv": tv}, secret, algorithm="HS256")
H = {"Authorization": f"Bearer {token}"}
BASE = "http://127.0.0.1:8000/api/support-desk"

r = requests.get(f"{BASE}/queues/tier/1/board", headers=H, timeout=15)
print("board:", r.status_code)
if r.status_code != 200:
    print(r.text[:400]); sys.exit(1)
d = r.json()
stats = d.get("stats", {})
new_keys = ["my_status", "next_breach_at", "burn_rate_hr", "drain_eta_mins",
            "resolved_today", "my_resolved_today", "health"]
missing = [k for k in new_keys if k not in stats]
print("stats keys ok" if not missing else f"MISSING stats keys: {missing} -> STALE SERVER")
qs = d.get("queues", [])
if qs:
    qk = ["my_active", "max_agent_load", "skill_match"]
    qmiss = [k for k in qk if k not in qs[0]]
    print("queue keys ok" if not qmiss else f"MISSING queue keys: {qmiss} -> STALE SERVER")
else:
    print("no tier-1 queues visible (stats.no_queues =", stats.get("no_queues"), ")")

# availability gate: flip me away, expect 409 from serve-next, then restore
prev = requests.get(f"{BASE}/agent-status", headers=H, timeout=15).json().get("me") or {}
prev_status = prev.get("status") or "online"
requests.put(f"{BASE}/me/status", headers=H, json={"status": "away"}, timeout=15)
r2 = requests.post(f"{BASE}/queues/tier/1/serve-next", headers=H, timeout=15)
print("serve-next while away:", r2.status_code, "(expect 409)" , r2.json().get("detail") if r2.status_code == 409 else r2.text[:200])
requests.put(f"{BASE}/me/status", headers=H, json={"status": prev_status}, timeout=15)
print("restored status:", prev_status)
conn.close()
