"""Fernet symmetric encryption for refresh tokens at rest.

Why Fernet: it's the simplest authenticated symmetric scheme that
doesn't require us to think about IVs / nonces / padding. A single
key encrypts AND decrypts; the ciphertext includes a timestamp + HMAC
so any tampering / wrong key fails loudly.

Why we encrypt refresh tokens:
- A refresh token is a long-lived bearer credential. Anyone with the
  token + our client secret can mint access tokens for that user's
  calendar until they revoke.
- Storing them plaintext in the DB means a SQL-injection or read-only
  DB compromise hands an attacker calendar-read access for every
  connected user.
- Encrypting at rest means an attacker also needs the encryption key
  (an env var, separate trust domain).

`CALENDAR_TOKEN_ENCRYPTION_KEY` is a Fernet key — base64-encoded 32
random bytes. Generate via:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


_FERNET_INSTANCE: Optional[Fernet] = None


def _fernet() -> Fernet:
    global _FERNET_INSTANCE
    if _FERNET_INSTANCE is not None:
        return _FERNET_INSTANCE
    key = os.environ.get("CALENDAR_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "CALENDAR_TOKEN_ENCRYPTION_KEY env var is missing. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and set it before starting the app."
        )
    _FERNET_INSTANCE = Fernet(key.encode("ascii"))
    return _FERNET_INSTANCE


def encrypt_token(plaintext: str) -> str:
    """Encrypt a refresh token (or any small secret string).
    Output is URL-safe base64 — fine for VARCHAR columns."""
    if plaintext is None:
        return None  # type: ignore[return-value]
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str) -> Optional[str]:
    """Decrypt. Returns None if ciphertext is None/empty or invalid —
    callers treat None as 'connection unusable, mark as revoked'."""
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def reset_for_tests() -> None:
    """pytest helper — wipes the cached Fernet so a per-test env var
    swap takes effect."""
    global _FERNET_INSTANCE
    _FERNET_INSTANCE = None
