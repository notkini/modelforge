"""At-rest encryption for user-stored provider API keys.

The encryption key is derived from SESSION_SECRET via PBKDF2-HMAC-SHA256.
No new environment variable or hardcoded fallback is introduced.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


def _derivation_key() -> bytes:
    settings = get_settings()
    secret = settings['session_secret']
    if not secret or len(secret) < 40:
        raise RuntimeError('SESSION_SECRET must be set to at least 40 characters before storing encrypted settings.')
    return hashlib.pbkdf2_hmac('sha256', secret.encode('utf-8'), b'modelforge-setting-key-v1', 200_000, dklen=32)


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derivation_key()))


def encrypt_api_key(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode('utf-8')).decode('utf-8')


def decrypt_api_key(ciphertext: str) -> str | None:
    try:
        return _fernet().decrypt(ciphertext.encode('utf-8')).decode('utf-8')
    except (InvalidToken, ValueError):
        return None
