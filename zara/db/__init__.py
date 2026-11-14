"""Persistent storage for Zara (SQLite locally, TiDB/MySQL in production)."""

from __future__ import annotations

from .config import DatabaseConfig, ssl_ca_material
from .core import Database, MySQLDatabase, Result, SQLiteDatabase, create_database
from .crypto import EncryptionCodec
from .errors import (
    BackupError,
    EncryptionError,
    EncryptionKeyMissingError,
    InsufficientBalanceError,
    MigrationError,
    RecordNotFoundError,
    StorageConfigurationError,
    StorageError,
    StorageUnavailableError,
    StorageWriteError,
)
from .row import Row

__all__ = [
    "BackupError",
    "Database",
    "DatabaseConfig",
    "EncryptionCodec",
    "EncryptionError",
    "EncryptionKeyMissingError",
    "InsufficientBalanceError",
    "MigrationError",
    "MySQLDatabase",
    "RecordNotFoundError",
    "Result",
    "Row",
    "SQLiteDatabase",
    "StorageConfigurationError",
    "StorageError",
    "StorageUnavailableError",
    "StorageWriteError",
    "create_database",
    "ssl_ca_material",
]
