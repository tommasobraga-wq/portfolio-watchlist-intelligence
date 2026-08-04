"""Symmetric encryption for the watchlist.

The watchlist names which securities are actually held, so it is not kept in cleartext in
the repository. Encrypting the file rather than merely excluding it means the *history* is
protected too — the realistic accident here is not a breach but the repository being made
public one day, at which point every past commit is exposed at once.

Fernet (AES-128-CBC + HMAC-SHA256) from `cryptography`: a standard authenticated recipe, so
a tampered or truncated file is rejected rather than silently misread. No external binary,
so local runs and CI behave identically.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

KEY_VAR = "PWI_WATCHLIST_KEY"


class VaultError(RuntimeError):
    pass


class KeyMissing(VaultError):
    """Raised at startup, not at read time, so a misconfigured job fails immediately."""


def generate_key() -> str:
    """A fresh key, to be stored in `.env` locally and as a CI secret."""
    return Fernet.generate_key().decode()


def key_from_env(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    key = (source.get(KEY_VAR) or "").strip().strip("\"'").strip()
    if not key:
        raise KeyMissing(
            f"{KEY_VAR} is not set. Generate one with "
            f"`uv run scripts/watchlist_tool.py generate-key`."
        )
    return key


def encrypt(plaintext: bytes, key: str) -> bytes:
    return _cipher(key).encrypt(plaintext)


def decrypt(ciphertext: bytes, key: str) -> bytes:
    try:
        return _cipher(key).decrypt(ciphertext)
    except InvalidToken as exc:
        # Authenticated encryption means this is either the wrong key or a corrupted file;
        # both are fatal and must never degrade into "empty watchlist".
        raise VaultError(
            "Could not decrypt the watchlist: wrong key, or the file has been altered."
        ) from exc


def _cipher(key: str) -> Fernet:
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise VaultError(f"{KEY_VAR} is not a valid Fernet key") from exc
