"""Add disbursement columns to hr_travel_advances (additive, idempotent).

  • disbursement_method  hr_travel_settlement_method  NOT NULL DEFAULT 'PAYROLL'
  • disbursement_reference  VARCHAR(120)  NULL

The enum type hr_travel_settlement_method already exists (owned by hr_travel_settlements),
so the method column just references it. Reads .env directly (cwd-independent) per the
repo's ad-hoc-script convention, and checks information_schema before adding.

Run from C:\\Projects\\FourConnectService:
    python add_advance_disbursement.py
"""
import os
import re
import sys

import psycopg2


def _database_url() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(here, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*DATABASE_URL\s*=\s*(.+)\s*$", line)
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    return "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"


def _column_exists(cur, table, column) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return cur.fetchone() is not None


def main() -> int:
    url = _database_url()
    safe = re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)
    print(f"Connecting to {safe}")
    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        table = "hr_travel_advances"

        # the enum type must already exist (created by hr_travel_settlements)
        cur.execute("SELECT 1 FROM pg_type WHERE typname = 'hr_travel_settlement_method'")
        if cur.fetchone() is None:
            print("ERROR: enum type 'hr_travel_settlement_method' not found. Start the backend "
                  "once (create_all) so the settlement table + enum exist, then re-run.")
            return 1

        if _column_exists(cur, table, "disbursement_method"):
            print("[=] disbursement_method already present - skipping")
        else:
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN disbursement_method "
                "hr_travel_settlement_method NOT NULL DEFAULT 'PAYROLL'"
            )
            print("[+] added disbursement_method (NOT NULL DEFAULT 'PAYROLL')")

        if _column_exists(cur, table, "disbursement_reference"):
            print("[=] disbursement_reference already present - skipping")
        else:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN disbursement_reference VARCHAR(120)")
            print("[+] added disbursement_reference VARCHAR(120)")

        conn.commit()
        print("Done — committed.")
        return 0
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print(f"ERROR (rolled back): {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
