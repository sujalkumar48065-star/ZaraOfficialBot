"""Application-level encryption for sensitive columns and exports.

The Fernet key lives in ``ZARA_ENCRYPTION_KEY`` (Render environment secret),
never in the database or in the repository.  Values are stored as
``enc:v1:<fernet-token>`` so existing plaintext rows keep working and can be
re-encrypted in place with ``python -m zara.db.cli reencrypt``.

Usernames need case-insensitive lookup, so a keyed blind index
(``username_hash``) is stored alongside the ciphertext.  The HMAC key is
derived from the Fernet key with a fixed context, so losing the encryption key
also makes the blind index unusable - which is the intended behaviour.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .errors import EncryptionError, EncryptionKeyMissingError, StorageConfigurationError

PREFIX = "enc:v1:"
_BLIND_CONTEXT = b"zara-blind-index:v1:"


class EncryptionCodec:
    """Encrypt/decrypt values and build deterministic username indexes."""

    def __init__(self, key: str | None):
        key = (key or "").strip()
        self._fernet: Fernet | None = None
        self._blind_key: bytes | None = None
        self._key_raw: bytes | None = None
        if not key:
            return
        try:
            raw = base64.urlsafe_b64decode(key.encode("ascii"))
            self._fernet = Fernet(key.encode("ascii"))
        except Exception as exc:  # noqa: BLE001 - cryptography raises several types
            raise StorageConfigurationError(
                "ZARA_ENCRYPTION_KEY is not a valid Fernet key. "
                "Generate one with: python -m zara.db.cli init-key"
            ) from exc
        self._blind_key = hashlib.sha256(raw + _BLIND_CONTEXT).digest()
        self._key_raw = raw

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, value: Any) -> Any:
        """Encrypt a string value; leave None and non-strings alone."""
        if value is None or not isinstance(value, str):
            return value
        if value.startswith(PREFIX):
            return value
        if not self._fernet:
            return value
        token = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return PREFIX + token

    def decrypt(self, value: Any) -> Any:
        """Decrypt a value if it carries the marker; otherwise return it."""
        if isinstance(value, (bytes, bytearray)):
            try:
                value = bytes(value).decode("utf-8")
            except UnicodeDecodeError:
                return bytes(value)
        if not isinstance(value, str) or not value.startswith(PREFIX):
            return value
        if not self._fernet:
            raise EncryptionKeyMissingError(
                "Encrypted data was found in the database but ZARA_ENCRYPTION_KEY "
                "is not set. Restore the original key before starting the bot."
            )
        token = value[len(PREFIX):]
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise EncryptionError(
                "Could not decrypt a stored value. The encryption key does not "
                "match this database."
            ) from exc

    def is_ciphertext(self, value: Any) -> bool:
        return isinstance(value, str) and value.startswith(PREFIX)

    def blind_index(self, value: Any) -> str | None:
        """Keyed, case-insensitive index used for username lookups."""
        if value is None or not self._blind_key:
            return None
        normalized = str(value).strip().lstrip("@").casefold()
        if not normalized:
            return None
        return hmac.new(
            self._blind_key,
            normalized.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def export_key_fingerprint(self) -> str | None:
        """A non-secret fingerprint that can be compared between environments."""
        if not self._fernet:
            return None
        return hashlib.sha256(self._key_raw or b"").hexdigest()[:16]


def key_from_environment(environ: dict[str, str] | None = None) -> str | None:
    source = os.environ if environ is None else environ
    return (source.get("ZARA_ENCRYPTION_KEY") or "").strip() or None
