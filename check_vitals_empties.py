"""One-off check: WHY are tier_flow and breach_horizon empty on the Queues Overview?
Reads .env itself; read-only."""
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import psycopg2
import psycopg2.extras

psycopg2.extras.register_uuid()

env = {}
with open(".env", encoding="utf-8") as f:
    for line in f:
        m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.+?)\s*$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", env.get("DATABASE_URL", ""))
conn = psycopg2.connect(user=m.group(1), password=m.group(2), host=m.group(3),
                        port=m.group(4), dbname=m.group(5))
cur = conn.cursor()

print("== tier_moved activities (escalation flow source) ==")
cur.execute("""SELECT COUNT(*), MIN(created_at), MAX(created_at)
               FROM support_ticket_activities WHERE action = 'tier_moved'""")
n, lo, hi = cur.fetchone()
print(f"  all-time: {n}  first={lo}  last={hi}")
cur.execute("""SELECT COUNT(*) FROM support_ticket_activities
               WHERE action = 'tier_moved' AND created_at >= NOW() - INTERVAL '7 days'""")
print(f"  last 7d : {cur.fetchone()[0]}")
cur.execute("""SELECT COUNT(*) FROM support_ticket_activities
               WHERE action = 'tier_moved' AND created_at >= NOW() - INTERVAL '30 days'""")
print(f"  last 30d: {cur.fetchone()[0]}")

print("\n== open (non-terminal) tickets: SLA clock state (breach-horizon source) ==")
cur.execute("""
    SELECT
      COUNT(*)                                                                AS open_all,
      COUNT(*) FILTER (WHERE sla_paused_since IS NOT NULL)                    AS paused,
      COUNT(*) FILTER (WHERE COALESCE(sla_response_breached,FALSE)
                          OR COALESCE(sla_resolution_breached,FALSE))         AS breached,
      COUNT(*) FILTER (WHERE first_responded_at IS NULL
                         AND NOT COALESCE(sla_response_breached,FALSE)
                         AND response_due_at IS NOT NULL
                         AND response_due_at > NOW())                         AS rsp_pending_future,
      COUNT(*) FILTER (WHERE resolved_at IS NULL
                         AND NOT COALESCE(sla_resolution_breached,FALSE)
                         AND resolution_due_at IS NOT NULL
                         AND resolution_due_at > NOW())                       AS res_pending_future,
      COUNT(*) FILTER (WHERE response_due_at IS NULL
                         AND resolution_due_at IS NULL)                       AS no_sla_targets
    FROM support_tickets
    WHERE is_deleted = FALSE AND merged_into_id IS NULL
      AND status NOT IN ('resolved','closed','archived','cancelled')
      AND queue_id IS NOT NULL""")
row = cur.fetchone()
cols = ("open_all", "paused", "breached", "rsp_pending_future",
        "res_pending_future", "no_sla_targets")
for c, v in zip(cols, row):
    print(f"  {c:22s} {v}")

print("\n== sample of open tickets' due dates ==")
cur.execute("""SELECT ticket_number, status, response_due_at, resolution_due_at,
                      first_responded_at IS NOT NULL AS responded,
                      COALESCE(sla_response_breached,FALSE) AS rb,
                      COALESCE(sla_resolution_breached,FALSE) AS xb
               FROM support_tickets
               WHERE is_deleted = FALSE AND merged_into_id IS NULL
                 AND status NOT IN ('resolved','closed','archived','cancelled')
                 AND queue_id IS NOT NULL
               ORDER BY created_at DESC LIMIT 8""")
for r in cur.fetchall():
    print("  ", r)
conn.close()
