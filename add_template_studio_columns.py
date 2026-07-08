"""Template Studio Support-Desk columns — idempotent ALTER TABLE on the LIVE DB.

Adds the fields that power the admin "Template Studio" (lifecycle status, usage
analytics, SLA/assignee defaults, card identity, ordering, versioning-lite) plus
the template_id provenance stamp on support_tickets. Mirrors
add_ticket_workbench_columns.py: reads DATABASE_URL straight from .env
(cwd-independent), `ADD COLUMN IF NOT EXISTS` so it's safe to re-run. Keep all
prints ASCII (the Windows cp1252 console crashes on unicode BEFORE the SQL runs).

    & "C:/Users/91700/AppData/Local/Programs/Python/Python314/python.exe" C:/Projects/FourConnectService/add_template_studio_columns.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def database_url() -> str:
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*DATABASE_URL\s*=\s*(.+)\s*$", line)
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    return "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"


TEMPLATE_COLUMNS = [
    ("status", "VARCHAR(16) NOT NULL DEFAULT 'active'"),   # draft|active|archived (is_active mirrors)
    ("usage_count", "INTEGER NOT NULL DEFAULT 0"),          # apply-flow analytics
    ("last_used_at", "TIMESTAMPTZ"),
    ("last_used_by_id", "UUID"),                            # bare UUID (ORM declares the FK)
    ("default_sla_package_id", "UUID"),
    ("default_assignee_id", "UUID"),
    ("icon", "VARCHAR(40)"),                                # lucide key OR emoji
    ("accent", "VARCHAR(20)"),                              # hex card identity
    ("pinned", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("sort_order", "INTEGER NOT NULL DEFAULT 0"),
    ("version", "INTEGER NOT NULL DEFAULT 1"),
    ("revisions", "JSONB NOT NULL DEFAULT '[]'::jsonb"),    # <=10 prior-content snapshots
    ("visibility", "VARCHAR(16) NOT NULL DEFAULT 'global'"),  # global|team|personal (agent Template Desk)
]

TICKET_COLUMNS = [
    ("template_id", "UUID"),                                # provenance: born from a template
]


def main():
    url = database_url()
    safe = re.sub(r":[^:@/]+@", ":***@", url)
    print(f"[template-studio] connecting -> {safe}")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    added = []
    for name, ddl in TEMPLATE_COLUMNS:
        cur.execute(f'ALTER TABLE support_ticket_templates ADD COLUMN IF NOT EXISTS {name} {ddl};')
        added.append(f"support_ticket_templates.{name}")
    for name, ddl in TICKET_COLUMNS:
        cur.execute(f'ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS {name} {ddl};')
        added.append(f"support_tickets.{name}")
    cur.execute('CREATE INDEX IF NOT EXISTS ix_support_ticket_templates_status ON support_ticket_templates (status);')
    cur.execute('CREATE INDEX IF NOT EXISTS ix_support_ticket_templates_visibility ON support_ticket_templates (visibility);')
    cur.execute('CREATE INDEX IF NOT EXISTS ix_support_tickets_template_id ON support_tickets (template_id);')
    # Backfill: templates deactivated before status existed become archived.
    cur.execute("UPDATE support_ticket_templates SET status = 'archived' WHERE is_active = FALSE AND status = 'active';")
    print(f"[template-studio] backfilled {cur.rowcount} inactive template(s) -> archived")
    print(f"[template-studio] ensured {len(added)} columns: {', '.join(added)}")
    # Defensive verify: every expected column must now exist.
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'support_ticket_templates'"
    )
    have = {r[0] for r in cur.fetchall()}
    missing = [c for c, _ in TEMPLATE_COLUMNS if c not in have]
    if missing:
        print(f"[template-studio] WARNING missing after run: {', '.join(missing)}")
        sys.exit(1)
    cur.close()
    conn.close()
    print("[template-studio] DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[template-studio] FAILED: {e}")
        sys.exit(1)
