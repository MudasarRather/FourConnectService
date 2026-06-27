"""Ad-hoc migration for the self-service Asset Type catalog.

1. Widen ``hr_asset.asset_type`` and ``hr_asset_categories.default_asset_type``
   from the PG enum ``hr_asset_type`` to varchar(40) so custom type codes can be
   stored (the SQLAlchemy models were switched to String).
2. Create ``hr_asset_type_defs`` (idempotent) — the manageable type catalog.
3. Seed the 13 built-in types (``is_system=True``), ON CONFLICT DO NOTHING.

Idempotent + guarded; safe to re-run. Reads DATABASE_URL from .env (see CLAUDE.md).
Run from the backend root:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" migrate_asset_types.py
"""
import os
import re
import sys
import uuid

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
FALLBACK = "postgresql://postgres:postgres@127.0.0.1:5432/fourreck_db"

# code, label, lucide-icon-name, sort_order
BUILTINS = [
    ("LAPTOP", "Laptop", "Laptop", 0),
    ("DESKTOP", "Desktop", "HardDrive", 1),
    ("MONITOR", "Monitor", "Monitor", 2),
    ("MOBILE", "Mobile", "Smartphone", 3),
    ("SIM", "SIM", "CreditCard", 4),
    ("RFID_CARD", "RFID Card", "CreditCard", 5),
    ("ID_CARD", "ID Card", "CreditCard", 6),
    ("HEADSET", "Headset", "Headphones", 7),
    ("KEYBOARD", "Keyboard", "Keyboard", 8),
    ("MOUSE", "Mouse", "Mouse", 9),
    ("VEHICLE", "Vehicle", "Car", 10),
    ("KEYS", "Keys", "KeyRound", 11),
    ("OTHER", "Other", "Package", 12),
]

ALTERS = [
    ("hr_assets", "asset_type"),
    ("hr_asset_categories", "default_asset_type"),
]


def database_url() -> str:
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL") and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return FALLBACK


def main() -> int:
    url = database_url()
    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", url)
    if not m:
        print(f"[FAIL] Could not parse DATABASE_URL: {url!r}")
        return 1
    user, pwd, host, port, db = m.groups()
    db = db.split("?")[0]
    print(f"[..] Connecting to {host}:{port}/{db} as {user}")
    conn = psycopg2.connect(host=host, port=port, user=user, password=pwd, dbname=db)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # 1. widen the enum columns → varchar (guarded by current data_type)
            for table, col in ALTERS:
                cur.execute(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name=%s AND column_name=%s", (table, col),
                )
                row = cur.fetchone()
                if not row:
                    print(f"[skip] {table}.{col} not found")
                    continue
                if row[0] == "character varying":
                    print(f"[ok]   {table}.{col} already varchar")
                    continue
                cur.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {col} TYPE varchar(40) "
                    f"USING {col}::text"
                )
                print(f"[done] {table}.{col} {row[0]} -> varchar(40)")

            # 2. create the catalog table (idempotent)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS hr_asset_type_defs (
                    id uuid PRIMARY KEY,
                    code varchar(40) NOT NULL UNIQUE,
                    label varchar(80) NOT NULL,
                    icon varchar(40),
                    sort_order integer NOT NULL DEFAULT 0,
                    is_system boolean NOT NULL DEFAULT false,
                    is_active boolean NOT NULL DEFAULT true,
                    is_deleted boolean NOT NULL DEFAULT false,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    created_by_id uuid REFERENCES users(id)
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS ix_hr_asset_type_defs_is_deleted "
                "ON hr_asset_type_defs (is_deleted)"
            )
            print("[ok]   hr_asset_type_defs table ready")

            # 3. seed built-ins
            seeded = 0
            for code, label, icon, order in BUILTINS:
                cur.execute(
                    "INSERT INTO hr_asset_type_defs (id, code, label, icon, sort_order, is_system, is_active) "
                    "VALUES (%s, %s, %s, %s, %s, true, true) ON CONFLICT (code) DO NOTHING",
                    (str(uuid.uuid4()), code, label, icon, order),
                )
                seeded += cur.rowcount
            cur.execute("SELECT count(*) FROM hr_asset_type_defs")
            total = cur.fetchone()[0]
            print(f"[done] seeded {seeded} new built-in type(s); {total} total in catalog")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
