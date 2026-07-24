"""Probe — Incident Timeline upgrade (catalog / filters / cursor / pins / pulse /
stream / exports) against the LIVE backend on :8000.

Self-fixturing: creates marked probe tickets, exercises the new surfaces with a
superuser + a same-team non-lead agent + an out-of-team agent, then soft-deletes
the fixtures. Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_incident_timeline_upgrade.py
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
import urllib.request
import urllib.error
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from jose import jwt

sys.stdout.reconfigure(encoding="utf-8")
psycopg2.extras.register_uuid()

env = {}
with open(".env", encoding="utf-8") as f:
    for line in f:
        m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.+?)\s*$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")

m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", env.get("DATABASE_URL", ""))
user, pwd, host, port, dbname = m.groups()
conn = psycopg2.connect(user=user, password=pwd, host=host, port=port, dbname=dbname)
conn.autocommit = True
cur = conn.cursor()

MARK = "TLPROBE-" + datetime.now(timezone.utc).strftime("%H%M%S")
BASE = "http://127.0.0.1:8000/api"
FAIL = 0


def mint(uid, tv):
    return jwt.encode({"sub": str(uid), "tv": int(tv or 0),
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=45)},
                      env.get("SECRET_KEY", ""), algorithm="HS256")


def call(method, path, tok, body=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method,
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            if raw:
                return r.status, data, {k.lower(): v for k, v in r.headers.items()}
            return r.status, (json.loads(data.decode()) if data else None)
    except urllib.error.HTTPError as e:
        data = e.read()
        if raw:
            return e.code, data, {k.lower(): v for k, v in e.headers.items()}
        try:
            return e.code, json.loads(data.decode())
        except Exception:
            return e.code, None


def check(label, ok, extra=""):
    global FAIL
    print(f"  [{'OK' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAIL += 1


# ── setup: personas ──
cur.execute("""SELECT id, email, COALESCE(token_version,0) FROM users
               WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1""")
su_id, su_email, su_tv = cur.fetchone()
su = mint(su_id, su_tv)
print(f"superuser: {su_email}")

cur.execute("""SELECT id, name, lead_user_id, member_roles FROM support_teams
               WHERE lead_user_id IS NOT NULL""")
team = None
for tid, tname, lead_id, roles in cur.fetchall():
    members = [k for k in (roles or {}).keys() if str(k) != str(lead_id)]
    if members:
        team = (tid, tname, lead_id, members)
        break
if not team:
    print("no team with a lead + a second member — abort")
    sys.exit(1)
team_id, team_name, lead_id, others = team
mate_id = None
for cand in others:
    cur.execute("""SELECT id, COALESCE(token_version,0) FROM users
                   WHERE id = %s AND is_active = TRUE AND is_support_agent = TRUE
                         AND is_superuser = FALSE""", (cand,))
    row = cur.fetchone()
    if row:
        mate_id, mate_tv = row
        break
if not mate_id:
    print("no non-lead agent on the team — abort")
    sys.exit(1)
mate = mint(mate_id, mate_tv)
cur.execute("""SELECT u.id, COALESCE(u.token_version,0), u.email FROM users u
               WHERE u.is_active = TRUE AND u.is_support_agent = TRUE
                     AND u.is_superuser = FALSE AND u.id != %s
                     AND NOT EXISTS (SELECT 1 FROM support_teams t
                                     WHERE t.member_roles ? u.id::text)""", (mate_id,))
row = cur.fetchone()
outsider = mint(row[0], row[1]) if row else None
print(f"team: {team_name} lead={lead_id} mate={mate_id} outsider={'yes' if outsider else 'NO (seal checks skipped)'}")

made = []


def forge(subject, ttype="incident", priority="high", assignee=None):
    s, j = call("POST", "/support-desk/tickets/", su, {
        "subject": f"{MARK} {subject}", "description": "timeline probe — safe to ignore",
        "priority": priority, "ticket_type": ttype, "source": "internal"})
    if not (j and j.get("id")):
        print(f"  setup create failed ({s}: {j}) — abort")
        sys.exit(1)
    made.append(j["id"])
    cur.execute("UPDATE support_tickets SET team_id=%s, assigned_agent_id=%s, queue_id=NULL WHERE id=%s",
                (team_id, assignee or lead_id, j["id"]))
    return j["id"]


tk = forge("payment relay flapping")

print("\n-- 1 · DDL --")
cur.execute("""SELECT column_name FROM information_schema.columns
               WHERE table_name='support_ticket_activities'
                 AND column_name IN ('is_milestone','pinned_by_id','pinned_at')""")
cols = {r[0] for r in cur.fetchall()}
check("milestone columns exist", cols == {"is_milestone", "pinned_by_id", "pinned_at"}, str(cols))
cur.execute("""SELECT indexname FROM pg_indexes WHERE tablename='support_ticket_activities'
               AND indexname IN ('ix_support_ticket_activities_action','ix_sta_milestone_at')""")
idx = {r[0] for r in cur.fetchall()}
check("action btree + milestone partial index", len(idx) == 2, str(idx))

print("\n-- 2 · back-compat + enrichment --")
s, j = call("GET", "/support-desk/incidents/timeline", su)
check("no-param call 200 with days shape", s == 200 and all(k in (j or {}) for k in ("total", "page", "limit", "days")))
ev0 = next((e for d in (j or {}).get("days", []) for e in d["events"]), None)
check("events carry id/category/label/is_milestone/actor_user_id/team_id",
      bool(ev0) and all(k in ev0 for k in ("id", "category", "label", "is_milestone", "actor_user_id", "team_id")))
check("response carries cursor", "cursor" in (j or {}))

print("\n-- 3 · kinds validation --")
s, j = call("GET", "/support-desk/incidents/timeline?kinds=created,resolved", su)
acts = {e["action"] for d in (j or {}).get("days", []) for e in d["events"]}
check("kinds=created,resolved 200 + subset", s == 200 and acts <= {"created", "resolved"}, str(acts)[:80])
s, _ = call("GET", "/support-desk/incidents/timeline?kinds=bogus_kind", su)
check("kinds=bogus 422", s == 422)
s, _ = call("GET", "/support-desk/incidents/timeline?kinds=created,bogus", su)
check("kinds mixed 422", s == 422)
LEGACY = ("created,acknowledged,major_incident,escalated,decision_logged,status_changed,"
          "resolved,reopened,pir_created,pir_submitted,pir_approved,pir_published,de_escalated")
s, _ = call("GET", f"/support-desk/incidents/timeline?kinds={LEGACY}", su)
check("all 13 legacy frontend kinds 200", s == 200)

print("\n-- 4 · catalog --")
s, j = call("GET", "/support-desk/incidents/timeline/catalog", su)
names = {a["action"] for a in (j or {}).get("actions", [])}
check("catalog 200 + legacy kinds registered", s == 200 and set(LEGACY.split(",")) <= names,
      f"{len(names)} actions")
check("6 categories + milestone_cap", set((j or {}).get("categories", [])) ==
      {"lifecycle", "command", "comms", "sla", "governance", "system"} and (j or {}).get("milestone_cap"))

print("\n-- 5 · fixture verbs --")
s, j = call("POST", f"/support-desk/tickets/{tk}/decision", su,
            {"kind": "mitigation", "decision": f"{MARK} failover to standby relay"})
check("decision logged", s in (200, 201), str(j)[:90] if s not in (200, 201) else "")
s, j = call("POST", f"/support-desk/tickets/{tk}/tasks", su, {"title": f"{MARK} check relay logs"})
check("task added", s in (200, 201), str(j)[:90] if s not in (200, 201) else "")
s, j = call("POST", f"/support-desk/tickets/{tk}/comments", su,
            {"body": f"{MARK} internal probe note", "is_internal": True})
check("internal comment", s in (200, 201), str(j)[:90] if s not in (200, 201) else "")
s, j = call("POST", f"/support-desk/tickets/{tk}/worklogs", su, {"minutes": 25, "note": f"{MARK} diag"})
check("worklog", s in (200, 201), str(j)[:90] if s not in (200, 201) else "")

print("\n-- 6 · new filters --")
s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}&actor=human", su)
human = [e for d in (j or {}).get("days", []) for e in d["events"]]
check("actor=human → no System rows", s == 200 and all(e["actor_user_id"] for e in human), f"{len(human)} events")
s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}&actor=system", su)
sysev = [e for d in (j or {}).get("days", []) for e in d["events"]]
check("actor=system → only System rows", s == 200 and all(not e["actor_user_id"] for e in sysev))
s, _ = call("GET", "/support-desk/incidents/timeline?actor=alien", su)
check("actor=alien 422", s == 422)
s, j = call("GET", f"/support-desk/incidents/timeline?actor_id={su_id}&q={MARK}", su)
mine = [e for d in (j or {}).get("days", []) for e in d["events"]]
check("actor_id isolates one person", s == 200 and mine and
      all(str(e["actor_user_id"]) == str(su_id) for e in mine), f"{len(mine)} events")
s, j = call("GET", "/support-desk/incidents/timeline?mi_only=1", su)
mievs = [e for d in (j or {}).get("days", []) for e in d["events"]]
check("mi_only → all SEV1", s == 200 and all(e["sev"] == 1 for e in mievs), f"{len(mievs)} events")
s, _ = call("GET", "/support-desk/incidents/timeline?exposure=bogus", su)
check("exposure=bogus 422", s == 422)
s, _ = call("GET", "/support-desk/incidents/timeline?exposure=security", su)
check("exposure=security 200", s == 200)
s, j = call("GET", f"/support-desk/incidents/timeline?team_id={team_id}&q={MARK}", su)
tevs = [e for d in (j or {}).get("days", []) for e in d["events"]]
check("team_id filter scopes to team", s == 200 and tevs and
      all(str(e["team_id"]) == str(team_id) for e in tevs))

print("\n-- 7 · since cursor --")
s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}", su)
cur0 = (j or {}).get("cursor")
check("cursor minted", bool(cur0), str(cur0)[:40])
call("POST", f"/support-desk/tickets/{tk}/decision", su,
     {"kind": "mitigation", "decision": f"{MARK} second decision for cursor test"})
s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}&since={quote(cur0)}", su)
fresh = [e for d in (j or {}).get("days", []) for e in d["events"]]
cur1 = (j or {}).get("cursor")
check("since returns only newer events", s == 200 and len(fresh) >= 1 and
      all(e["at"] > cur0.split("~")[0] for e in fresh), f"{len(fresh)} new")
s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}&since={quote(cur1)}", su)
check("replay with fresh cursor → empty + cursor echoed",
      s == 200 and (j or {}).get("total") == 0 and (j or {}).get("cursor") == cur1)
s, _ = call("GET", "/support-desk/incidents/timeline?since=garbage", su)
check("since=garbage 422", s == 422)

print("\n-- 8 · milestone pins --")
s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}&kinds=decision_logged", su)
dec_ids = [e["id"] for d in (j or {}).get("days", []) for e in d["events"]]
s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}&kinds=task_added", su)
task_ids = [e["id"] for d in (j or {}).get("days", []) for e in d["events"]]
s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}&kinds=created", su)
created_ids = [e["id"] for d in (j or {}).get("days", []) for e in d["events"]]
check("fixture activities located", bool(dec_ids) and bool(task_ids) and bool(created_ids),
      f"dec={len(dec_ids)} task={len(task_ids)} created={len(created_ids)}")
pin_id = dec_ids[0] if dec_ids else None
s, j = call("POST", f"/support-desk/incidents/activities/{pin_id}/pin", su)
check("pin decision → 200 + is_milestone", s == 200 and (j or {}).get("is_milestone") is True,
      str(j)[:90] if s != 200 else "")
s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}&milestones=1", su)
stones = [e for d in (j or {}).get("days", []) for e in d["events"]]
check("milestones=1 shows the pin", s == 200 and len(stones) == 1 and str(stones[0]["id"]) == str(pin_id))
s, _ = call("POST", f"/support-desk/incidents/activities/{pin_id}/pin", su)
check("re-pin 409", s == 409)
s, j = call("POST", f"/support-desk/incidents/activities/{task_ids[0]}/pin", su)
check("ineligible action 422", s == 422, str((j or {}).get('detail'))[:70])
s, _ = call("POST", f"/support-desk/incidents/activities/{created_ids[0]}/pin", mate)
check("same-team non-lead pin 403", s == 403)
if outsider:
    s, _ = call("POST", f"/support-desk/incidents/activities/{created_ids[0]}/pin", outsider)
    check("out-of-team pin 404 (not 403)", s == 404)
    s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}", outsider)
    check("outsider feed sealed to zero", s == 200 and (j or {}).get("total") == 0)
s, j = call("DELETE", f"/support-desk/incidents/activities/{pin_id}/pin", su)
check("unpin 200", s == 200 and (j or {}).get("is_milestone") is False)
s, _ = call("DELETE", f"/support-desk/incidents/activities/{pin_id}/pin", su)
check("re-unpin 409", s == 409)

print("\n-- 9 · milestone cap --")
for i in range(12):
    call("POST", f"/support-desk/tickets/{tk}/status-update", su,
         {"body": f"{MARK} cadence update {i}"})
s, j = call("GET", f"/support-desk/incidents/timeline?q={MARK}&kinds=status_update&limit=200", su)
upd_ids = [e["id"] for d in (j or {}).get("days", []) for e in d["events"]]
pins_ok = 0
for aid in upd_ids[:12]:
    s, _ = call("POST", f"/support-desk/incidents/activities/{aid}/pin", su)
    if s == 200:
        pins_ok += 1
# the ticket now sits at the cap — the 13th pin (the earlier decision) must 409
s, j = call("POST", f"/support-desk/incidents/activities/{pin_id}/pin", su)
check("cap enforced at 12 pins/ticket", pins_ok == 12 and s == 409,
      f"pinned={pins_ok} overflow={s}")

print("\n-- 10 · pulse --")
frm = "2020-01-01T00:00:00Z"
s, p = call("GET", f"/support-desk/incidents/timeline/pulse?from={frm}", su)
s2, t2 = call("GET", f"/support-desk/incidents/timeline?from={frm}&limit=1", su)
check("pulse 200", s == 200)
check("pulse total ⇔ timeline total", p and t2 and p.get("total_events") == t2.get("total"),
      f"pulse={p and p.get('total_events')} feed={t2 and t2.get('total')}")
check("density sums to total", p and sum(d["count"] for d in p.get("density", [])) == p.get("total_events"))
check("category keys ⊆ taxonomy", p and set(p.get("by_category", {}).keys()) <=
      {"lifecycle", "command", "comms", "sla", "governance", "system"})
check("pulse carries flow/mtta/top_actors/by_team",
      p and all(k in p for k in ("flow", "mtta_minutes", "top_actors", "by_team")))

print("\n-- 11 · dossier stream --")
s, j = call("GET", f"/support-desk/incidents/{tk}/stream", su)
c = (j or {}).get("counts", {})
check("stream 200 + all four kinds counted", s == 200 and c.get("activity", 0) >= 3 and
      c.get("comment", 0) >= 1 and c.get("worklog", 0) >= 1 and c.get("task", 0) >= 1, str(c))
its = (j or {}).get("items", [])
check("items sorted desc", all(its[i]["at"] >= its[i + 1]["at"] for i in range(len(its) - 1)))
check("ticket header row-shaped", all(k in (j or {}).get("ticket", {}) for k in
      ("acknowledged_at", "is_major_incident", "priority", "war_room_url", "mi_proposed_at", "team_id")))
s, j = call("GET", f"/support-desk/incidents/{tk}/stream?types=comment", su)
only = (j or {}).get("items", [])
check("types=comment facet + internal flag", s == 200 and only and
      all(i["kind"] == "comment" for i in only) and any(i["is_internal"] for i in only))
s, _ = call("GET", f"/support-desk/incidents/{tk}/stream?types=alien", su)
check("types=alien 422", s == 422)
nk = forge("plain service request — not an incident", ttype="service_request")
s, _ = call("GET", f"/support-desk/incidents/{nk}/stream", su)
check("non-incident 422", s == 422)
if outsider:
    s, _ = call("GET", f"/support-desk/incidents/{tk}/stream", outsider)
    check("outsider stream 404", s == 404)

print("\n-- 12 · exports --")
s, data, hdrs = call("GET", f"/support-desk/incidents/timeline/export.json?q={MARK}", su, raw=True)
try:
    jj = json.loads(data.decode())
except Exception:
    jj = None
check("export.json 200 + valid + capped flag", s == 200 and jj is not None and "events" in jj and
      len(jj["events"]) <= 2000 and "capped" in jj and
      "attachment" in (hdrs.get("content-disposition") or ""),
      f"s={s} keys={list((jj or {}).keys())[:5]} cd={(hdrs.get('content-disposition') or '')[:40]} "
      f"body={data.decode(errors='replace')[:120] if s != 200 else ''}")
s, data, hdrs = call("GET", f"/support-desk/incidents/timeline/export.csv?q={MARK}", su, raw=True)
head = data.decode().splitlines()[0] if s == 200 and data else ""
check("export.csv 200 + additive trailer columns", s == 200 and
      head.endswith("label,category,milestone"), head[-60:])
s, data, hdrs = call("GET", f"/support-desk/incidents/timeline/export.pdf?q={MARK}", su, raw=True)
check("export.pdf %PDF or 503 GTK guard", (s == 200 and data[:4] == b"%PDF") or s == 503,
      f"status={s}")

print("\n-- cleanup --")
for tid in made:
    cur.execute("UPDATE support_tickets SET is_deleted = TRUE WHERE id = %s", (tid,))
print(f"soft-deleted {len(made)} probe ticket(s)")

print(f"\n{'ALL GREEN' if FAIL == 0 else f'{FAIL} FAILURE(S)'}")
sys.exit(1 if FAIL else 0)
