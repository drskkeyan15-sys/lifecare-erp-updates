"""
auth_utils.py
LifeCare Pharmacy ERP - password hashing.

Fixes a real, verified vulnerability: login.py used to do
"SELECT role FROM users WHERE username=? AND password=?" - both the
stored value and the comparison were plain text, so anyone who opened
pharmacy.db in any SQLite browser could read every user's password
outright.

Deliberately stdlib-only (hashlib.pbkdf2_hmac, no bcrypt/passlib) - this
app is packaged with PyInstaller onto pharmacists' own Windows PCs, and
every extra dependency is one more thing that can silently fail to
bundle into the frozen exe (see LifeCareERP.spec's whole hiddenimports
saga). PBKDF2-HMAC-SHA256 at a modern iteration count is a legitimate,
still-recommended choice (OWASP's 2023+ guidance) for exactly this
situation, not a shortcut.

Stored format: "pbkdf2$sha256$<iterations>$<salt_hex>$<hash_hex>" - a
single self-describing string, so a future iteration-count bump doesn't
break verification of passwords hashed under the old count.
"""

import hashlib
import hmac
import os
import binascii

ALGO = "sha256"
ITERATIONS = 260_000  # OWASP-recommended floor for PBKDF2-HMAC-SHA256 as of 2023+


def hash_password(plain_password):
    """Returns a new salted hash string, ready to store in users.password."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(ALGO, plain_password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2${ALGO}${ITERATIONS}${binascii.hexlify(salt).decode()}${binascii.hexlify(dk).decode()}"


def is_hashed(stored_value):
    """True if `stored_value` is already in our hashed format - used by
    the one-time startup migration (database.py) to tell an
    already-migrated row apart from a still-plaintext legacy one."""
    return bool(stored_value) and stored_value.startswith("pbkdf2$")


def verify_password(plain_password, stored_value):
    """
    Checks `plain_password` against whatever's stored. Handles both a
    proper hash (the normal case) AND a still-plaintext legacy value
    (only possible if the startup migration in database.py hasn't run
    yet for some reason) - so login never breaks mid-migration, it just
    keeps working exactly as before until that row gets hashed.
    """
    if not stored_value:
        return False

    if not is_hashed(stored_value):
        return plain_password == stored_value

    try:
        _, algo, iterations, salt_hex, hash_hex = stored_value.split("$")
        salt = binascii.unhexlify(salt_hex)
        expected = binascii.unhexlify(hash_hex)
        dk = hashlib.pbkdf2_hmac(algo, plain_password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(dk, expected)
    except Exception:
        # Malformed stored value (shouldn't happen outside manual DB
        # tampering) - fail closed, never treat a parse error as a match.
        return False
