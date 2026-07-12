"""Probe: Pit Wall board with a REAL tier-1 lane — creates a temp queue (superuser),
reads the board (expects new stats + queue keys), then deletes the queue. Self-cleaning."""
import re, sys
from pathlib import Path

import psycopg2, psycopg2.extras
import requests
from jose import jwt

ROOT = Path(__file__).resolve().parent
env = (ROOT / ".env").read_text(encoding="utf-8")
def _env(k, d=None):
    m = re.search(rf"^{k}=(.*)$", env, re.M)
    return m.group(1).strip() if m else d

m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", _env("DATABASE_URL", ""))
user, pw, host, port, dbname = m.groups()
psycopg2.extras.register_uuid()
conn = psycopg2.connect(user=user, password=pw, host=host, port=port, dbname=dbname)
cur = conn.cursor()
cur.execute("SELECT id, token_version FROM users WHERE is_superuser = true AND is_active = true LIMIT 1")
uid, tv = cur.fetchone()
token = jwt.encode({"sub": str(uid), "tv": tv or 0}, _env("SECRET_KEY"), algorithm="HS256")
H = {"Authorization": f"Bearer {token}"}
BASE = "http://127.0.0.1:8000/api/support-desk"

qid = None
try:
    r = requests.post(f"{BASE}/queues/", headers=H, timeout=15, json={
        "name": "PROBE Pit Lane", "code": "PROBE_PIT", "tier": 1,
        "queue_priority": 50, "max_agent_load": 2, "color": "#f5b942"})
    print("create queue:", r.status_code)
    if r.status_code not in (200, 201):
        print(r.text[:300]); sys.exit(1)
    qid = r.json().get("id")

    r = requests.get(f"{BASE}/queues/tier/1/board", headers=H, timeout=15)
    d = r.json(); stats = d.get("stats", {}); qs = d.get("queues", [])
    print("board:", r.status_code, "| queues:", len(qs))
    for k in ["my_status", "next_breach_at", "burn_rate_hr", "drain_eta_mins",
              "resolved_today", "my_resolved_today", "health"]:
        print(f"  stats.{k} =", stats.get(k, "<MISSING>"))
    if qs:
        probe_q = next((x for x in qs if x.get("id") == str(qid)), qs[0])
        for k in ["my_active", "max_agent_load", "skill_match", "skills"]:
            print(f"  queue.{k} =", probe_q.get(k, "<MISSING>"))
    items = d.get("items", [])
    print("  items[0].viewing =", (items[0].get("viewing", "<MISSING>") if items else "(no items to check)"))

    # empty lane: serve-next should say drained (status gate passed while online)
    r = requests.post(f"{BASE}/queues/tier/1/serve-next", headers=H, timeout=15)
    print("serve-next (empty lane):", r.status_code, r.json().get("reason"))
finally:
    if qid:
        r = requests.delete(f"{BASE}/queues/{qid}", headers=H, timeout=15)
        print("cleanup delete:", r.status_code)
conn.close()
