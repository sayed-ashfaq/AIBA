"""Password hashing and session-token primitives.

Deliberately free of database and request concerns so it can be reasoned about (and tested) on its
own — app.services.auth is what combines these with persistence.
"""

import hashlib
import secrets

from pwdlib import PasswordHash

# Argon2id: memory-hard, so it resists the GPU/ASIC attacks that make plain SHA and even bcrypt
# increasingly cheap to brute-force offline. PasswordHash also records the parameters inside each
# hash, so raising the cost later doesn't invalidate existing passwords.
_password_hash = PasswordHash.recommended()

# a real argon2 hash of a value nobody can supply, used to spend the same CPU time verifying a
# login for an address that doesn't exist as one that does — see verify_password_dummy()
_DUMMY_HASH = _password_hash.hash(secrets.token_urlsafe(32))

# 32 bytes = 256 bits from the OS CSPRNG. Far beyond guessing range, which is what lets the stored
# form be a plain fast hash instead of a KDF.
_TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def verify_password_dummy() -> None:
    """Burn the same time a real verify would, for a login attempt on an unknown email.

    Without this, "no such user" returns in microseconds while a wrong password takes ~100ms, and
    that gap alone tells an attacker which addresses have accounts.
    """
    _password_hash.verify("dummy-password", _DUMMY_HASH)


def generate_session_token() -> str:
    """The raw token. Goes into the user's cookie and is never persisted."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """The stored form. Not salted on purpose: a lookup has to find the row from the token alone,
    and a per-row salt would force a table scan. Safe here only because the input is high-entropy
    random — never do this with anything a human chose."""
    return hashlib.sha256(token.encode()).hexdigest()
