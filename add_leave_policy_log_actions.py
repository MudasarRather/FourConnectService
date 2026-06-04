"""Idempotent live migration — add LEAVE_POLICY_* values to the Postgres
``hr_attendance_log_action`` enum so the leave-policy lifecycle (create /
edit / soft-delete) can be written to the shared audit log.

Mirrors the live-migration pattern used by add_leave_module_tables.py.
ALTER TYPE ... ADD VALUE cannot run inside a transaction block, so we use an
autocommit connection. Reads DATABASE_URL directly from .env (do NOT rely on
get_settings() cwd resolution — see CLAUDE.md env note).
"""
import re
import psycopg2

NEW_VALUES = [
    "LEAVE_POLICY_CREATED",
    "LEAVE_POLICY_UPDATED",
    "LEAVE_POLICY_DELETED",
]


def main():
    env = open(".env").read()
    m = re.search(r"DATABASE_URL=postgresql://(.*?):(.*?)@(.*?):(\d+)/(\S+)", env)
    if not m:
        raise SystemExit("Could not parse DATABASE_URL from .env")
    user, pwd, host, port, db = m.groups()
    conn = psycopg2.connect(dbname=db, user=user, password=pwd, host=host, port=port)
    conn.autocommit = True  # ALTER TYPE ADD VALUE cannot run in a tx block
    cur = conn.cursor()
    cur.execute(
        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid "
        "WHERE t.typname='hr_attendance_log_action'"
    )
    existing = {r[0] for r in cur.fetchall()}
    for val in NEW_VALUES:
        if val in existing:
            print(f"  skip (already present): {val}")
            continue
        cur.execute(
            f"ALTER TYPE hr_attendance_log_action ADD VALUE IF NOT EXISTS '{val}'"
        )
        print(f"  added: {val}")
    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
