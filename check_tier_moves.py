import json
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
m = re.search(r"postgresql://(.*?):(.*?)@(.*?):(\d+)/(.*)", env["DATABASE_URL"])
conn = psycopg2.connect(user=m.group(1), password=m.group(2), host=m.group(3),
                        port=m.group(4), dbname=m.group(5))
cur = conn.cursor()
cur.execute("""SELECT a.detail, a.created_at, t.queue_id, t.status
               FROM support_ticket_activities a
               JOIN support_tickets t ON t.id = a.ticket_id
               WHERE a.action = 'tier_moved' ORDER BY a.created_at""")
for d, ca, qid, st in cur.fetchall():
    print(json.dumps(d), "| now-in-queue:", qid, "| status:", st, "|", ca)
print()
cur.execute("SELECT id, name, tier, is_deleted FROM support_queues ORDER BY name")
for r in cur.fetchall():
    print(" queue:", r)
conn.close()
