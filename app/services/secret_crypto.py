"""Encrypt integration secrets at rest (Fernet, key derived from SECRET_KEY)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "" or value.startswith(_PREFIX):
        return value
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith(_PREFIX):
        # Legacy plaintext — still usable until next save re-encrypts
        return value
    token = value[len(_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None
