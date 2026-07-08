"""Phase-2 Support-Desk ticket columns — idempotent ALTER TABLE on the LIVE DB.

`Base.metadata.create_all()` creates missing TABLES but never adds COLUMNS to an
existing one, so the Phase-2 fields on `support_tickets` must be added here.
Reads DATABASE_URL straight from .env (cwd-independent — see CLAUDE.md env note),
uses `ADD COLUMN IF NOT EXISTS` so it's safe to re-run.

    & "C:/Users/91700/AppData/Local/Programs/Python/Python314/python.exe" C:/Projects/FourConnectService/add_ticket_phase2_columns.py
"""
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def database_url() -> str:
    # Read .env directly (don't trust get_settings() cwd resolution).
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*DATABASE_URL\s*=\s*(.+)\s*$", line)
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    # fallback (local dev)
    return "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"


COLUMNS = [
    ("escalation_reason", "TEXT"),
    ("reopen_reason", "TEXT"),
    ("hold_reason", "VARCHAR(240)"),
    ("hold_until", "TIMESTAMPTZ"),
    ("held_at", "TIMESTAMPTZ"),
    ("held_from_status", "VARCHAR(30)"),
    ("last_customer_reply_at", "TIMESTAMPTZ"),
    ("reminder_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_reminder_at", "TIMESTAMPTZ"),
    ("vendor_name", "VARCHAR(160)"),
    ("vendor_ticket_ref", "VARCHAR(120)"),
    ("vendor_status", "VARCHAR(60)"),
    ("is_major_incident", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("business_impact", "VARCHAR(20)"),
    ("affected_users", "INTEGER"),
    ("revenue_impact", "VARCHAR(160)"),
    ("war_room_url", "VARCHAR(400)"),
    ("breach_reason", "VARCHAR(240)"),
    ("rca_summary", "TEXT"),
    ("rca_corrective", "TEXT"),
    ("rca_preventive", "TEXT"),
]


def main():
    url = database_url()
    safe = re.sub(r":[^:@/]+@", ":***@", url)
    print(f"[phase2] connecting -> {safe}")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    added = []
    for name, ddl in COLUMNS:
        cur.execute(f'ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS {name} {ddl};')
        added.append(name)
    # is_major_incident gets an index (used by a future scope filter / dashboards).
    cur.execute('CREATE INDEX IF NOT EXISTS ix_support_tickets_major_incident ON support_tickets (is_major_incident);')
    print(f"[phase2] ensured {len(added)} columns: {', '.join(added)}")
    cur.close()
    conn.close()
    print("[phase2] DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[phase2] FAILED: {e}")
        sys.exit(1)
