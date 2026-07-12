"""E2E probe — the L2 WORKBENCH (worklogs, watchers, swarm) against the LIVE backend on :8000.

Flow: create a temp L2 lane (no team → no auto-assign, nobody pinged) → create a
probe ticket parked on it → worklog add ×2 (counter sync) → paginated ledger →
delete one entry (counter decrement) → watch (idempotent double-watch) → watchers
list → unwatch → swarm start (double-start 409) → join (idempotent) → end (outcome
→ internal note) → tier-2 board carries the new stats keys + per-item badges → the
no_queues tier-3 shape carries the same keys → CLEAN UP (archive ticket, delete
lane). Never touches serve-next (nothing real can be claimed).

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_l2_workbench.py
"""
import platform
try:
    _ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
    _ur.__dict__["processor"] = "Intel"
    platform._uname_cache = _ur
except Exception:
    pass

import json
import re
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from jose import jwt

psycopg2.extras.register_uuid()

env = {}
with open(".env", encoding="utf-8") as f:
    for line in f:
        m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.+?)\s*$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", env.get("DATABASE_URL", ""))
conn = psycopg2.connect(user=m.group(1), password=m.group(2), host=m.group(3), port=m.group(4), dbname=m.group(5))
cur = conn.cursor()
cur.execute("SELECT id, COALESCE(token_version,0) FROM users WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1")
uid, tv = cur.fetchone()
conn.close()
token = jwt.encode({"sub": str(uid), "tv": int(tv),
                    "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
                   env.get("SECRET_KEY", ""), algorithm="HS256")
HDRS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
BASE = "http://127.0.0.1:8000/api/support-desk"

fails = []

NEW_STATS_KEYS = ["ack_pending", "swarm_active", "watching", "my_logged_today_mins"]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers=HDRS,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        fails.append(name)


cleanup = {"queues": [], "tickets": []}
try:
    # 1. A temp L2 lane, teamless — invisible to auto-assign and other agents' rotations.
    c, q2 = call("POST", "/queues/", {"name": "PROBE L2 Workbench", "tier": 2, "queue_priority": 1})
    check("create temp L2 lane", c == 201, q2 if c != 201 else q2["id"])
    if c != 201:
        sys.exit("cannot continue without the lane")
    cleanup["queues"].append(q2["id"])

    # 2. A probe ticket, then PARK it on the lane + take ownership (create routes through
    #    the rule engine, so the payload queue never sticks — /assign is the sanctioned move).
    c, t = call("POST", "/tickets/", {"subject": "PROBE-L2WB webhook cert loop [probe]",
                                      "description": "Automated L2 workbench probe — safe to ignore.",
                                      "ticket_type": "incident", "priority": "high",
                                      "source": "internal"})
    check("create probe ticket", c in (200, 201), t if c not in (200, 201) else t["ticket_number"])
    cleanup["tickets"].append(t["id"])
    c, t = call("POST", f"/tickets/{t['id']}/assign",
                {"queue_id": q2["id"], "assigned_agent_id": str(uid)})
    check("park on the L2 lane + own it",
          c == 200 and str(t.get("queue_id")) == str(q2["id"])
          and str(t.get("assigned_agent_id")) == str(uid),
          t if c != 200 else "")
    base_minutes = int(t.get("time_spent_minutes") or 0)

    # 3. Worklogs — two entries, counter syncs.
    c, w1 = call("POST", f"/tickets/{t['id']}/worklogs",
                 {"minutes": 25, "note": "probe diagnosis pass", "work_type": "diagnosis"})
    check("worklog #1 created", c == 200 and w1["minutes"] == 25 and w1["work_type"] == "diagnosis",
          w1 if c != 200 else "")
    c, w2 = call("POST", f"/tickets/{t['id']}/worklogs", {"minutes": 20, "work_type": "work"})
    check("worklog #2 created", c == 200, w2 if c != 200 else "")
    c, tk = call("GET", f"/tickets/{t['id']}")
    check("counter synced (+45)", c == 200 and int(tk.get("time_spent_minutes") or 0) == base_minutes + 45,
          tk.get("time_spent_minutes"))

    # 3b. Validation: zero minutes and a bogus work_type must 422.
    c, _ = call("POST", f"/tickets/{t['id']}/worklogs", {"minutes": 0})
    check("worklog minutes=0 → 422", c == 422, c)
    c, _ = call("POST", f"/tickets/{t['id']}/worklogs", {"minutes": 5, "work_type": "napping"})
    check("worklog bogus work_type → 422", c == 422, c)

    # 3c. Ledger + delete (author) decrements the counter.
    c, ledger = call("GET", f"/tickets/{t['id']}/worklogs?page=1&limit=10")
    check("worklog ledger lists 2, sums 45",
          c == 200 and ledger["total"] == 2 and ledger["total_minutes"] == 45, ledger if c != 200 else "")
    c, after = call("DELETE", f"/tickets/{t['id']}/worklogs/{w2['id']}")
    check("worklog delete removes entry", c == 200 and after["total"] == 1 and after["total_minutes"] == 25,
          after if c != 200 else "")
    c, tk = call("GET", f"/tickets/{t['id']}")
    check("counter decremented (-20)", c == 200 and int(tk.get("time_spent_minutes") or 0) == base_minutes + 25,
          tk.get("time_spent_minutes"))
    c, _ = call("DELETE", f"/tickets/{t['id']}/worklogs/{w2['id']}")
    check("double-delete → 404", c == 404, c)

    # 4. Watch — idempotent both ways.
    c, wt = call("POST", f"/tickets/{t['id']}/watch")
    check("watch subscribes", c == 200 and wt["watching"] and wt["total"] == 1, wt if c != 200 else "")
    c, wt = call("POST", f"/tickets/{t['id']}/watch")
    check("double-watch idempotent", c == 200 and wt["total"] == 1, wt)
    c, wl = call("GET", f"/tickets/{t['id']}/watchers")
    check("watchers list shows me", c == 200 and wl["total"] == 1 and wl["watching"], wl if c != 200 else "")
    c, wt = call("DELETE", f"/tickets/{t['id']}/watch")
    check("unwatch", c == 200 and not wt["watching"] and wt["total"] == 0, wt)
    c, wt = call("DELETE", f"/tickets/{t['id']}/watch")
    check("double-unwatch idempotent", c == 200 and wt["total"] == 0, wt)
    # Re-subscribe so the board's 'watching' stat has something to count.
    call("POST", f"/tickets/{t['id']}/watch")

    # 5. Swarm lifecycle.
    c, sw = call("POST", f"/tickets/{t['id']}/swarm", {"note": "probe swarm"})
    check("swarm start", c == 200 and sw["active"] and sw["joined"], sw if c != 200 else "")
    c, _err = call("POST", f"/tickets/{t['id']}/swarm", {})
    check("double-start → 409", c == 409, c)
    c, sw = call("POST", f"/tickets/{t['id']}/swarm/join")
    check("join idempotent (still 1 participant)",
          c == 200 and len(sw["active"]["participants"]) == 1, sw if c != 200 else "")

    # 5b. Board stats while the swarm is live + I watch: keys + per-item badges.
    c, board = call("GET", f"/queues/tier/2/board?queue_id={q2['id']}")
    keys_ok = c == 200 and all(k in board["stats"] for k in NEW_STATS_KEYS)
    check("tier-2 board carries new stats keys", keys_ok,
          [k for k in NEW_STATS_KEYS if c == 200 and k not in board["stats"]] or "")
    if c == 200:
        check("stats: swarm_active ≥ 1", board["stats"]["swarm_active"] >= 1, board["stats"]["swarm_active"])
        check("stats: watching ≥ 1", board["stats"]["watching"] >= 1, board["stats"]["watching"])
        check("stats: my_logged_today_mins ≥ 25", board["stats"]["my_logged_today_mins"] >= 25,
              board["stats"]["my_logged_today_mins"])
        row = next((x for x in board["items"] if str(x["id"]) == str(t["id"])), None)
        check("item badges: swarming + watching", bool(row and row.get("swarming") and row.get("watching")),
              row and {"swarming": row.get("swarming"), "watching": row.get("watching")})

    # 5c. End the swarm with an outcome → internal note + history entry.
    c, sw = call("POST", f"/tickets/{t['id']}/swarm/end", {"outcome": "probe outcome: cert chain replaced"})
    check("swarm end", c == 200 and sw["active"] is None and len(sw["history"]) == 1, sw if c != 200 else "")
    c, _err = call("POST", f"/tickets/{t['id']}/swarm/end", {})
    check("end without active swarm → 409", c == 409, c)
    c, comments = call("GET", f"/tickets/{t['id']}/comments?page=1&limit=50")
    body_hits = json.dumps(comments) if c == 200 else ""
    check("swarm outcome landed as internal note", c == 200 and "Swarm outcome" in body_hits, c)

    # 6. The no_queues early-return (tier 3 has no probe lanes for a fresh superuser? —
    #    it may have real lanes; assert shape only when no_queues is actually flagged,
    #    otherwise assert the keys exist on the populated path too).
    c, b3 = call("GET", "/queues/tier/3/board")
    keys3 = c == 200 and all(k in b3["stats"] for k in NEW_STATS_KEYS)
    check(f"tier-3 board ({'no_queues' if c == 200 and b3['stats'].get('no_queues') else 'populated'}) carries new keys",
          keys3, [k for k in NEW_STATS_KEYS if c == 200 and k not in b3["stats"]] or "")

    # 7. Guards around terminal states.
    c, res = call("POST", f"/tickets/{t['id']}/resolve",
                  {"resolution_code": "solved", "resolution_summary": "probe resolution"})
    check("resolve the probe ticket", c == 200 and res.get("status") == "resolved", res if c != 200 else "")
    c, w3 = call("POST", f"/tickets/{t['id']}/worklogs", {"minutes": 5, "work_type": "comms"})
    check("worklog allowed on RESOLVED", c == 200, w3 if c != 200 else "")
    c, _err = call("POST", f"/tickets/{t['id']}/swarm", {})
    check("swarm on RESOLVED → 409", c == 409, c)
    # Try to close (the closeout quality gate may refuse a fresh resolve — tolerate both).
    call("POST", "/tickets/bulk", {"ids": [t["id"]], "action": "close"})
    c, tk = call("GET", f"/tickets/{t['id']}")
    if c == 200 and tk.get("status") == "closed":
        c, _err = call("POST", f"/tickets/{t['id']}/worklogs", {"minutes": 5})
        check("worklog on CLOSED → 409", c == 409, c)
    else:
        print("SKIP  close gated by closeout quality window — CLOSED guard covered by unit path")

finally:
    for tid in cleanup["tickets"]:
        c, _ = call("DELETE", f"/tickets/{tid}")
        print(f"cleanup ticket {tid}: {c}")
    for qid in cleanup["queues"]:
        c, _ = call("DELETE", f"/queues/{qid}")
        print(f"cleanup queue {qid}: {c}")

print("\n" + ("ALL L2 WORKBENCH CHECKS PASSED ✔" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
