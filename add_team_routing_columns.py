"""Support Desk — add request-type routing + service-profile columns to support_teams.

Makes a Team a first-class routing target: it declares which request TYPES (and
categories) it handles, whether it auto-assigns matched tickets, the method + RR
cursor, per-member roles, and a service profile (business hours + default SLA /
priority). create_all never ALTERs existing tables, so these are added here.
Idempotent (ADD COLUMN IF NOT EXISTS) — safe to re-run.

    & "C:/Users/91700/AppData/Local/Programs/Python/Python314/python.exe" C:/Projects/FourConnectService/add_team_routing_columns.py
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


DDL = [
    "ALTER TABLE support_teams ADD COLUMN IF NOT EXISTS member_roles JSONB NOT NULL DEFAULT '{}'::jsonb;",
    "ALTER TABLE support_teams ADD COLUMN IF NOT EXISTS request_types JSONB NOT NULL DEFAULT '[]'::jsonb;",
    "ALTER TABLE support_teams ADD COLUMN IF NOT EXISTS category_ids JSONB NOT NULL DEFAULT '[]'::jsonb;",
    "ALTER TABLE support_teams ADD COLUMN IF NOT EXISTS auto_assign BOOLEAN NOT NULL DEFAULT FALSE;",
    "ALTER TABLE support_teams ADD COLUMN IF NOT EXISTS assignment_method VARCHAR(20) NOT NULL DEFAULT 'round_robin';",
    "ALTER TABLE support_teams ADD COLUMN IF NOT EXISTS rr_last_user_id UUID;",
    "ALTER TABLE support_teams ADD COLUMN IF NOT EXISTS business_hours JSONB NOT NULL DEFAULT '{}'::jsonb;",
    "ALTER TABLE support_teams ADD COLUMN IF NOT EXISTS default_sla_package_id UUID;",
    "ALTER TABLE support_teams ADD COLUMN IF NOT EXISTS default_priority VARCHAR(20);",
]


def main():
    url = database_url()
    print(f"[team-routing] connecting -> {re.sub(r':[^:@/]+@', ':***@', url)}")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    for stmt in DDL:
        cur.execute(stmt)
    print("[team-routing] ensured columns on support_teams: member_roles, request_types, "
          "category_ids, auto_assign, assignment_method, rr_last_user_id, business_hours, "
          "default_sla_package_id, default_priority")
    cur.close()
    conn.close()
    print("[team-routing] DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[team-routing] FAILED: {e}")
        sys.exit(1)
