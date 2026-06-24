"""Re-key encrypted bank account numbers from the SECRET_KEY-derived key to a
dedicated FIELD_ENCRYPTION_KEY. Safe to re-run.

Context
-------
``encrypt_bank_accounts.py`` first encrypted the columns using a key derived
from ``SECRET_KEY`` — which on this deployment is still the *public committed
default*. This script switches those rows to a strong, dedicated
``FIELD_ENCRYPTION_KEY`` so the encryption is actually meaningful and is
decoupled from the JWT secret (you can rotate SECRET_KEY without losing data).

What it does
------------
1. Generates a strong ``FIELD_ENCRYPTION_KEY`` if one isn't already in ``.env``
   (and appends it to ``.env``).
2. For every account_number row, decrypts with the OLD key (SECRET_KEY-derived)
   and re-encrypts with the NEW key (FIELD_ENCRYPTION_KEY-derived).
   * Rows already under the new key are detected and skipped (idempotent).
3. Single transaction with a decrypt round-trip assert before commit — any
   failure rolls everything back.

After running: set the SAME FIELD_ENCRYPTION_KEY on DigitalOcean, restart the
local backend, and (separately) untrack .env from git.
"""
import os
import re
import secrets
import sys

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
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_DEFAULT_DB = "postgresql://postgres:acer2gb@127.0.0.1:5432/fourreck_db"
_DEFAULT_SECRET = "your-secret-key-here-change-this-in-production"


def _read_env() -> dict:
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _append_field_key(value: str):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    sep = "" if content.endswith("\n") or content == "" else "\n"
    with open(ENV_PATH, "a", encoding="utf-8") as f:
        f.write(f"{sep}# Dedicated key for PII field encryption (bank accounts). KEEP SECRET. Never commit.\n")
        f.write(f"FIELD_ENCRYPTION_KEY={value}\n")


def main():
    env = _read_env()
    db_url = env.get("DATABASE_URL") or _DEFAULT_DB
    old_secret = env.get("SECRET_KEY") or _DEFAULT_SECRET

    new_key = env.get("FIELD_ENCRYPTION_KEY")
    generated = False
    if not new_key:
        new_key = secrets.token_urlsafe(48)
        generated = True

    old_fernet = derive_fernet(old_secret)
    new_fernet = derive_fernet(new_key)

    m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", db_url)
    if not m:
        print(f"ERROR: could not parse DATABASE_URL: {db_url!r}")
        sys.exit(1)
    user, pwd, host, port, dbname = m.groups()
    print(f"DB           : {host}:{port}/{dbname} (user={user})")
    print(f"Old key      : SECRET_KEY-derived")
    print(f"New key      : {'freshly generated' if generated else 'existing FIELD_ENCRYPTION_KEY'}\n")

    conn = psycopg2.connect(dbname=dbname, user=user, password=pwd, host=host, port=port)
    conn.autocommit = False
    cur = conn.cursor()

    grand = 0
    try:
        for table in TABLES:
            cur.execute(
                f"SELECT id, account_number FROM {table} "
                f"WHERE account_number IS NOT NULL AND account_number <> ''"
            )
            rows = cur.fetchall()
            rekeyed = already = unknown = 0
            for rid, stored in rows:
                b = stored.encode("utf-8")
                # already under the new key?
                try:
                    new_fernet.decrypt(b)
                    already += 1
                    continue
                except (InvalidToken, ValueError, TypeError):
                    pass
                # decrypt with the old key
                try:
                    plain = old_fernet.decrypt(b).decode("utf-8")
                except (InvalidToken, ValueError, TypeError):
                    unknown += 1
                    print(f"    ! {table} id={rid}: not decryptable with old OR new key — left untouched")
                    continue
                token = new_fernet.encrypt(plain.encode("utf-8")).decode("ascii")
                assert new_fernet.decrypt(token.encode("utf-8")).decode("utf-8") == plain, \
                    f"round-trip mismatch on {table} id={rid}"
                cur.execute(f"UPDATE {table} SET account_number = %s WHERE id = %s", (token, rid))
                rekeyed += 1
            print(f"  [{table}] rows={len(rows)}  rekeyed={rekeyed}  already-new={already}  unknown={unknown}")
            grand += rekeyed

        conn.commit()
        print(f"\nCommitted. Re-keyed: {grand}.")
    except Exception as e:
        conn.rollback()
        print(f"\nROLLED BACK — no changes committed. Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

    if generated:
        _append_field_key(new_key)
        print("Appended FIELD_ENCRYPTION_KEY to .env.")

    print("\n" + "=" * 64)
    print("FIELD_ENCRYPTION_KEY (set this EXACT value on DigitalOcean):")
    print(f"  {new_key}")
    print("=" * 64)


if __name__ == "__main__":
    main()
