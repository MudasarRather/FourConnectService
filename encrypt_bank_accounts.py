"""One-off, idempotent migration: encrypt employee & payslip bank account
numbers at rest (Fernet, application-level). Safe to re-run.

What it does
------------
1. Widens ``hr_employees.account_number`` and ``hr_payslips.account_number``
   from ``VARCHAR(40)`` to ``text`` (a Fernet token won't fit 40 chars).
2. Encrypts every value that is NOT already a valid Fernet token. Re-running is
   a no-op (already-encrypted rows are detected and skipped).

Safety
------
* Reads ``.env`` directly for both ``DATABASE_URL`` and the encryption passphrase
  so it always targets the SAME database the backend uses, regardless of cwd
  (see CLAUDE.md — pydantic-settings resolves .env relative to cwd; we don't
  rely on that here).
* Re-uses ``app.utils.crypto.derive_fernet`` so the key matches the running app
  exactly — no drift.
* Verifies a decrypt round-trip for every row BEFORE writing, and runs in a
  single transaction: any mismatch/error rolls everything back. No partial or
  lossy state is committed.

Run (backend should be stopped to avoid ALTER TABLE lock contention):
  & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" \\
      C:\\Projects\\FourConnectService\\encrypt_bank_accounts.py
"""
import os
import re
import sys

# Prime platform cache before any SQLAlchemy import path runs (the local WMI
# service is wedged on this box — see C:/tmp/run_server.py for the same trick).
import platform
_ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
_ur.__dict__["processor"] = "Intel"
platform._uname_cache = _ur
platform._Processor.get = staticmethod(lambda: "Intel")

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.utils.crypto import derive_fernet  # noqa: E402
from cryptography.fernet import InvalidToken  # noqa: E402

TABLES = ("hr_employees", "hr_payslips")
_DEFAULT_DB = "postgresql://postgres:acer2gb@127.0.0.1:5432/fourreck_db"
_DEFAULT_SECRET = "your-secret-key-here-change-this-in-production"


def _read_env() -> dict:
    env = {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    env = _read_env()
    db_url = env.get("DATABASE_URL") or _DEFAULT_DB
    passphrase = env.get("FIELD_ENCRYPTION_KEY") or env.get("SECRET_KEY") or _DEFAULT_SECRET
    fernet = derive_fernet(passphrase)

    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", db_url)
    if not m:
        print(f"ERROR: could not parse DATABASE_URL: {db_url!r}")
        sys.exit(1)
    user, pwd, host, port, dbname = m.groups()
    key_src = "FIELD_ENCRYPTION_KEY" if env.get("FIELD_ENCRYPTION_KEY") else "SECRET_KEY"
    print(f"DB        : {host}:{port}/{dbname} (user={user})")
    print(f"Key source: {key_src}\n")

    conn = psycopg2.connect(dbname=dbname, user=user, password=pwd, host=host, port=port)
    conn.autocommit = False
    cur = conn.cursor()

    def is_encrypted(v) -> bool:
        if not v:
            return True
        try:
            fernet.decrypt(v.encode("utf-8"))
            return True
        except (InvalidToken, ValueError, TypeError):
            return False

    grand_total = 0
    try:
        for table in TABLES:
            cur.execute(
                "SELECT data_type, character_maximum_length FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = 'account_number'",
                (table,),
            )
            meta = cur.fetchone()
            if not meta:
                print(f"  [{table}] no account_number column — skipping")
                continue
            dtype, maxlen = meta
            if dtype != "text":
                shown = f"{dtype}({maxlen})" if maxlen else dtype
                print(f"  [{table}] ALTER account_number {shown} -> text")
                cur.execute(f"ALTER TABLE {table} ALTER COLUMN account_number TYPE text")

            cur.execute(
                f"SELECT id, account_number FROM {table} "
                f"WHERE account_number IS NOT NULL AND account_number <> ''"
            )
            rows = cur.fetchall()
            enc = skipped = 0
            for rid, val in rows:
                if is_encrypted(val):
                    skipped += 1
                    continue
                token = fernet.encrypt(val.encode("utf-8")).decode("ascii")
                # Verify the round-trip BEFORE committing — guarantees no data loss.
                assert fernet.decrypt(token.encode("utf-8")).decode("utf-8") == val, \
                    f"round-trip mismatch on {table} id={rid}"
                cur.execute(
                    f"UPDATE {table} SET account_number = %s WHERE id = %s", (token, rid)
                )
                enc += 1
            print(f"  [{table}] rows={len(rows)}  encrypted={enc}  already-encrypted={skipped}")
            grand_total += enc

        conn.commit()
        print(f"\nCommitted. Newly encrypted: {grand_total}. (Re-running this script is safe.)")
    except Exception as e:
        conn.rollback()
        print(f"\nROLLED BACK — no changes committed. Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
