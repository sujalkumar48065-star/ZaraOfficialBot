"""Storage-specific exceptions.

Every exception raised by the storage layer derives from :class:`StorageError`
so handlers can catch one type without knowing which backend is in use.
"""

from __future__ import annotations


class StorageError(RuntimeError):
    """Base class for all storage failures."""


class StorageConfigurationError(StorageError):
    """The storage environment is missing or invalid."""


class StorageUnavailableError(StorageError):
    """The database could not be reached or the connection was lost."""


class StorageWriteError(StorageError):
    """A write did not reach the database."""


class RecordNotFoundError(StorageWriteError):
    """A write was rejected because the target row does not exist."""


class InsufficientBalanceError(StorageWriteError):
    """A debit was rejected because the balance was too low."""


class EncryptionError(StorageError):
    """Data could not be encrypted or decrypted."""


class EncryptionKeyMissingError(EncryptionError):
    """Encrypted data exists but ZARA_ENCRYPTION_KEY is not configured."""


class BackupError(StorageError):
    """A backup, export, restore or import failed validation."""


class MigrationError(StorageError):
    """A schema or data migration failed."""
