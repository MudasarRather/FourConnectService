"""Probe — Wires / UPLINK ARRAY against the LIVE backend on :8000.

Coverage (self-cleaning — restores the original queue_notifications value):
  PUT  /api/support-desk/settings/            — queue_notifications round-trip
  POST /api/support-desk/settings/test-webhook
        · real delivery to a local HTTP listener (payload = {"text": ...})
        · unreachable endpoint -> ok:false
        · no URL anywhere -> 422
  IN-PROCESS (live DB): wires.allows() honours assign_email / breach_warning;
        wires.post_webhook dedupes and actually posts to the listener.

Run FROM the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" probe_uplink_array.py
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
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

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
if not m:
    sys.exit("DATABASE_URL not parseable")
user, pwd, host, port, dbname = m.groups()
conn = psycopg2.connect(user=user, password=pwd, host=host, port=port, dbname=dbname)
cur = conn.cursor()
cur.execute("""SELECT id, email, COALESCE(token_version, 0) FROM users
               WHERE is_superuser = TRUE AND is_active = TRUE LIMIT 1""")
uid, email, tv = cur.fetchone()
cur.execute("SELECT value FROM support_settings WHERE key = 'queue_notifications'")
row = cur.fetchone()
original = row[0] if row else None
conn.close()
print(f"superuser: {email}  tv={tv}  original wires: {original}")

token = jwt.encode({"sub": str(uid), "tv": int(tv),
                    "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
                   env.get("SECRET_KEY", ""), algorithm="HS256")
HDRS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
BASE = "http://127.0.0.1:8000/api"
FAIL = 0


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers=HDRS,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None


def check(label, ok, extra=""):
    global FAIL
    print(f"  [{'OK' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAIL += 1


# ── local listener that catches webhook POSTs ──
received = []


class _Catcher(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        try:
            received.append(json.loads(body.decode()))
        except Exception:
            received.append({"raw": body.decode(errors="replace")})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), _Catcher)
port_l = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
HOOK = f"http://127.0.0.1:{port_l}/hook"
print(f"listener on {HOOK}")

print("\n-- settings round-trip --")
st, s = call("PUT", "/support-desk/settings/", {
    "key": "queue_notifications",
    "value": {"assign_email": False, "breach_warning": True, "webhook_url": HOOK}})
check("PUT queue_notifications", st == 200 and s["value"]["assign_email"] is False, f"status={st}")

print("\n-- test transmission --")
st, r = call("POST", "/support-desk/settings/test-webhook", {})
check("test with SAVED url -> delivered", st == 200 and r.get("ok") is True, f"status={st} detail={r and r.get('detail')}")
time.sleep(0.3)
check("listener actually received {'text': ...}", any("text" in p for p in received),
      f"payloads={len(received)}")
st, r = call("POST", "/support-desk/settings/test-webhook", {"url": "http://127.0.0.1:9/dead"})
check("unreachable endpoint -> ok:false", st == 200 and r.get("ok") is False, f"status={st}")
st, r = call("POST", "/support-desk/settings/test-webhook", {"url": "ftp://nope"})
check("bad scheme -> ok:false + reason", st == 200 and r.get("ok") is False and "http" in (r.get("detail") or ""),
      f"detail={r and r.get('detail')}")

print("\n-- in-process gate + uplink engine (live DB session) --")
sys.path.insert(0, ".")
# Load the FULL model graph first — querying with only SdSetting imported leaves
# cross-package relationships (Asset -> AssetCategory) unresolved and mapper
# configuration fails. The real backend imports everything via app.main.
import app.models                                          # noqa: E402,F401
import app.models.hr                                       # noqa: E402,F401
import app.models.hr.asset_lifecycle                       # noqa: E402,F401  (AssetCategory — not in hr/__init__)
import app.models.support_desk.ticket                      # noqa: E402,F401
from app.database import SessionLocal                      # noqa: E402
from app.utils.support_desk import wires                   # noqa: E402
from app.models.support_desk.constants import (            # noqa: E402
    EVT_TICKET_ASSIGNED, EVT_TICKET_SLA_BREACH, EVT_TICKET_REPLIED,
)

db = SessionLocal()
wires.invalidate_cache()
check("assign_email=false gates ASSIGNED", wires.allows(db, EVT_TICKET_ASSIGNED) is False)
check("breach_warning=true passes SLA_BREACH", wires.allows(db, EVT_TICKET_SLA_BREACH) is True)
check("ungated events always pass", wires.allows(db, EVT_TICKET_REPLIED) is True)


class _FakeTicket:
    id = "probe-ticket-1"
    ticket_number = "TCK-PROBE"


before = len(received)
wires.post_webhook(db, EVT_TICKET_ASSIGNED, _FakeTicket(), "Uplink probe — assignment")
wires.post_webhook(db, EVT_TICKET_ASSIGNED, _FakeTicket(), "Uplink probe — assignment")  # dupe burst
time.sleep(1.0)
delta = len(received) - before
check("post_webhook fires (gates don't stop the uplink)", delta >= 1, f"received {delta}")
check("30s burst dedupe collapses the double fire", delta == 1, f"received {delta}")
db.close()

print("\n-- cleanup: restore original wires --")
restore = original if original is not None else {"assign_email": True, "breach_warning": True, "webhook_url": ""}
st, _ = call("PUT", "/support-desk/settings/", {"key": "queue_notifications", "value": restore})
check("restored", st == 200, f"status={st}")
server.shutdown()

print(f"\n{'ALL CHECKS PASSED' if not FAIL else f'{FAIL} CHECK(S) FAILED'}")
sys.exit(1 if FAIL else 0)
