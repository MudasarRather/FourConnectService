"""Application-level field encryption for PII at rest.

Bank account numbers (and any other column wired to :class:`EncryptedString`)
are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before they touch the
database and transparently decrypted on read — so every ORM read site keeps
seeing plaintext with zero call-site changes.

Why app-level (Fernet) and not pgcrypto
---------------------------------------
This codebase reads ``account_number`` through the ORM (``emp.account_number``,
``slip.account_number``) in many places. A SQLAlchemy ``TypeDecorator`` encrypts
on write / decrypts on read *transparently everywhere* with no call-site edits
and needs no ``CREATE EXTENSION`` privilege on the (remote) database. pgcrypto
would force every read into raw ``pgp_sym_decrypt`` SQL and a ``bytea`` column.

Key management
--------------
The symmetric key is *derived* from a secret passphrase, so operators manage one
secret:

    FIELD_ENCRYPTION_KEY  (preferred, set in .env) — any passphrase string
    SECRET_KEY            (fallback)               — the existing JWT secret

The passphrase is hashed with SHA-256 and urlsafe-base64 encoded into the 32-byte
key Fernet requires. **If this passphrase changes, previously encrypted values
become unreadable** — treat it like a database password.

Backward / forward compatibility
---------------------------------
:meth:`EncryptedString.process_result_value` returns the stored value verbatim
when it is *not* a valid Fernet token. This lets the encrypted column type be
deployed *before* the one-off data migration runs: legacy plaintext rows still
read correctly, and any subsequent write re-stores them encrypted. The migration
script (`encrypt_bank_accounts.py`) backfills existing rows.
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, Text


def derive_fernet(passphrase: str) -> Fernet:
    """Deterministically derive a Fernet instance from an arbitrary passphrase.

    Pure (no settings / DB access) so the migration script can reuse the exact
    same key derivation without importing the app's settings machinery.
    """
    digest = hashlib.sha256((passphrase or "").encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    # Imported lazily so importing this module never pulls settings at import time.
    from app.config import get_settings

    s = get_settings()
    passphrase = getattr(s, "FIELD_ENCRYPTION_KEY", None) or s.SECRET_KEY
    return derive_fernet(passphrase)


def encrypt_value(value):
    """Encrypt ``value`` to a Fernet token string. ``None`` passes through."""
    if value is None:
        return None
    return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")


def decrypt_value(value):
    """Decrypt a stored token. If ``value`` is not our ciphertext (legacy
    plaintext written before the encryption migration), return it verbatim."""
    if value is None:
        return None
    try:
        return _fernet().decrypt(str(value).encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return value


class EncryptedString(TypeDecorator):
    """Transparently encrypts/decrypts a text column with Fernet.

    Stored as ``Text`` because a Fernet token is ~100-160 chars even for short
    plaintext — never size this column tightly (the source columns were widened
    from ``VARCHAR(40)`` to ``text`` by the encryption migration).
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)
