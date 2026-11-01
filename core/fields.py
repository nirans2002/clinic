"""Field-level encryption (Fernet / AES-128-CBC + HMAC-SHA256) for confidential clinical text.

Stored as  enc1:<token>.  Supports key rotation: put the NEW key first in
FIELD_ENCRYPTION_KEYS and keep the old ones after it, then run
`python manage.py rotate_encryption` to re-encrypt every row with the new key.

NOTE: encrypted columns cannot be searched with SQL (icontains etc.) – by design.
"""
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models

PREFIX = "enc1:"


@lru_cache(maxsize=1)
def _fernet():
    keys = [Fernet(k.encode() if isinstance(k, str) else k) for k in settings.FIELD_ENCRYPTION_KEYS]
    return MultiFernet(keys)


def encrypt(text):
    return PREFIX + _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(value):
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:  # wrong key / tampered data: never leak ciphertext to the UI
        return "[unreadable – check encryption keys]"


class EncryptedTextField(models.TextField):
    description = "Text encrypted at rest"

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        value = str(value)
        if value.startswith(PREFIX):
            return value
        return encrypt(value)

    def from_db_value(self, value, expression, connection):
        return decrypt(value) if value else value
