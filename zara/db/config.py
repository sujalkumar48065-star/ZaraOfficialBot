"""Environment-driven storage configuration.

Supported variables (all optional; SQLite remains the default):

``ZARA_DB_BACKEND``
    ``sqlite`` or ``mysql``.  Inferred from ``ZARA_DB_URL`` / ``ZARA_DB_HOST``.

``ZARA_DB_URL``
    Full MySQL/TiDB URL, e.g.
    ``mysql://user.root:password@gateway01....tidbcloud.com:4000/zara?ssl=true``.

``ZARA_DB_HOST``, ``ZARA_DB_PORT``, ``ZARA_DB_USER``, ``ZARA_DB_PASSWORD``,
``ZARA_DB_NAME``
    Discrete connection settings used when ``ZARA_DB_URL`` is absent.

``ZARA_DB_SSL``
    ``required`` (default), ``skip-verify`` or ``disabled``.

``ZARA_DB_SSL_CA``
    Path to a CA file, an inline PEM certificate, or ``base64:<data>``.

``ZARA_DB_POOL_SIZE``, ``ZARA_DB_MAX_OVERFLOW``, ``ZARA_DB_POOL_RECYCLE``,
``ZARA_DB_POOL_TIMEOUT``, ``ZARA_DB_CONNECT_TIMEOUT``, ``ZARA_DB_READ_TIMEOUT``,
``ZARA_DB_WRITE_TIMEOUT``, ``ZARA_DB_RETRIES``
    Connection pool and retry tuning.  The default recycle time is 240s, below
    TiDB Cloud's 340s idle endpoint timeout.

``ZARA_ENCRYPTION_KEY``
    Fernet key used for sensitive columns and encrypted exports.

``ZARA_BACKUP_DIR``, ``ZARA_LEGACY_DB_PATH``, ``ZARA_AUTO_MIGRATE_LEGACY``
    Backup location and explicit legacy SQLite migration controls.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ..env import load_env
from .errors import StorageConfigurationError

load_env()

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_SQLITE_PATH = DEFAULT_DATA_DIR / "zara.sqlite3"
DEFAULT_BACKUP_DIR = ROOT / "backups"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise StorageConfigurationError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise StorageConfigurationError(f"{name} must be >= {minimum}, got {value}")
    return value


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise StorageConfigurationError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise StorageConfigurationError(f"{name} must be >= {minimum}, got {value}")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class DatabaseConfig:
    backend: str = "sqlite"
    sqlite_path: Path = field(default_factory=lambda: DEFAULT_SQLITE_PATH)
    host: str = ""
    port: int = 4000
    user: str = ""
    password: str = ""
    database: str = ""
    ssl_mode: str = "required"
    ssl_ca: str = ""
    pool_size: int = 5
    max_overflow: int = 5
    pool_recycle: int = 240
    pool_timeout: float = 30.0
    connect_timeout: int = 10
    read_timeout: int = 30
    write_timeout: int = 30
    retries: int = 3
    retry_base_delay: float = 0.25
    encryption_key: str = ""
    backup_dir: Path = field(default_factory=lambda: DEFAULT_BACKUP_DIR)
    legacy_sqlite_path: Path = field(default_factory=lambda: DEFAULT_SQLITE_PATH)
    auto_migrate_legacy: bool = False

    @property
    def is_mysql(self) -> bool:
        return self.backend == "mysql"

    @property
    def is_sqlite(self) -> bool:
        return self.backend == "sqlite"

    @property
    def label(self) -> str:
        if self.is_sqlite:
            return f"sqlite:{self.sqlite_path}"
        return f"mysql:{self.user}@{self.host}:{self.port}/{self.database}"

    def safe_label(self) -> str:
        return self.label

    def sanitized(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "sqlite_path": str(self.sqlite_path),
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "database": self.database,
            "ssl_mode": self.ssl_mode,
            "pool_size": self.pool_size,
            "max_overflow": self.max_overflow,
            "pool_recycle": self.pool_recycle,
            "encryption": bool(self.encryption_key),
        }

    @classmethod
    def from_env(
        cls,
        explicit_sqlite_path: Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> "DatabaseConfig":
        source = dict(os.environ) if environ is None else dict(environ)

        def env(name: str, default: str = "") -> str:
            return (source.get(name, default) or "").strip()

        def int_env(name: str, default: int, minimum: int = 0) -> int:
            raw = env(name)
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise StorageConfigurationError(f"{name} must be an integer, got {raw!r}") from exc
            if value < minimum:
                raise StorageConfigurationError(f"{name} must be >= {minimum}, got {value}")
            return value

        def float_env(name: str, default: float, minimum: float = 0.0) -> float:
            raw = env(name)
            if not raw:
                return default
            try:
                value = float(raw)
            except ValueError as exc:
                raise StorageConfigurationError(f"{name} must be a number, got {raw!r}") from exc
            if value < minimum:
                raise StorageConfigurationError(f"{name} must be >= {minimum}, got {value}")
            return value

        default_sqlite = Path(env("ZARA_DB_PATH", str(DEFAULT_SQLITE_PATH))).expanduser()
        sqlite_path = Path(explicit_sqlite_path).expanduser() if explicit_sqlite_path else default_sqlite
        legacy_path = Path(env("ZARA_LEGACY_DB_PATH", str(default_sqlite))).expanduser()

        backend = env("ZARA_DB_BACKEND").lower()
        url = env("ZARA_DB_URL")
        host = env("ZARA_DB_HOST")
        port = int_env("ZARA_DB_PORT", 4000, 1)
        user = env("ZARA_DB_USER")
        password = env("ZARA_DB_PASSWORD")
        database = env("ZARA_DB_NAME", "zara")
        ssl_mode = env("ZARA_DB_SSL", "required").lower()

        if url:
            parsed = urlsplit(url)
            scheme = parsed.scheme.lower()
            if scheme.startswith("sqlite"):
                backend = "sqlite"
                url_path = parsed.path.lstrip("/")
                if url_path:
                    sqlite_path = Path(unquote(url_path)).expanduser()
            elif scheme.startswith("mysql") or scheme.startswith("tidb"):
                backend = "mysql"
                host = parsed.hostname or host
                port = parsed.port or port
                user = unquote(parsed.username or user)
                password = unquote(parsed.password or password)
                database = parsed.path.lstrip("/") or database
                query = parsed.query.lower()
                if "ssl" in query and ("false" in query or "disabled" in query):
                    ssl_mode = "disabled"
            else:
                raise StorageConfigurationError(
                    f"Unsupported ZARA_DB_URL scheme {parsed.scheme!r}; use mysql:// or sqlite://"
                )

        if explicit_sqlite_path is not None:
            backend = "sqlite"

        if not backend:
            backend = "mysql" if host else "sqlite"
        if backend not in {"sqlite", "mysql"}:
            raise StorageConfigurationError("ZARA_DB_BACKEND must be 'sqlite' or 'mysql'")

        if backend == "mysql":
            missing = [name for name, value in (("host", host), ("user", user), ("database", database)) if not value]
            if missing:
                raise StorageConfigurationError(
                    "MySQL/TiDB configuration is incomplete; missing: " + ", ".join(missing)
                )
            if ssl_mode not in {"required", "skip-verify", "disabled"}:
                raise StorageConfigurationError(
                    "ZARA_DB_SSL must be required, skip-verify, or disabled"
                )
        else:
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)

        return cls(
            backend=backend,
            sqlite_path=sqlite_path,
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            ssl_mode=ssl_mode,
            ssl_ca=env("ZARA_DB_SSL_CA"),
            pool_size=int_env("ZARA_DB_POOL_SIZE", 5, 1),
            max_overflow=int_env("ZARA_DB_MAX_OVERFLOW", 5, 0),
            pool_recycle=int_env("ZARA_DB_POOL_RECYCLE", 240, 30),
            pool_timeout=float_env("ZARA_DB_POOL_TIMEOUT", 30.0, 1.0),
            connect_timeout=int_env("ZARA_DB_CONNECT_TIMEOUT", 10, 1),
            read_timeout=int_env("ZARA_DB_READ_TIMEOUT", 30, 1),
            write_timeout=int_env("ZARA_DB_WRITE_TIMEOUT", 30, 1),
            retries=int_env("ZARA_DB_RETRIES", 3, 0),
            retry_base_delay=float_env("ZARA_DB_RETRY_DELAY", 0.25, 0.0),
            encryption_key=env("ZARA_ENCRYPTION_KEY"),
            backup_dir=Path(env("ZARA_BACKUP_DIR", str(DEFAULT_BACKUP_DIR))).expanduser(),
            legacy_sqlite_path=legacy_path,
            auto_migrate_legacy=_bool_env("ZARA_AUTO_MIGRATE_LEGACY", False),
        )


def ssl_ca_material(value: str) -> str | None:
    """Return PEM text or a CA file path suitable for ``ssl`` contexts."""
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("base64:"):
        try:
            return base64.b64decode(value[len("base64:") :]).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise StorageConfigurationError("ZARA_DB_SSL_CA is not valid base64") from exc
    path = Path(value).expanduser()
    if path.is_file():
        return str(path)
    if "BEGIN CERTIFICATE" in value:
        return value
    raise StorageConfigurationError(
        "ZARA_DB_SSL_CA must be a file path, inline PEM, or base64:<data>"
    )
