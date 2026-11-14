"""Pluggable database layer for Zara.

Runtime handlers keep speaking SQLite-flavoured SQL.  :class:`SQLiteDatabase`
runs it directly; :class:`MySQLDatabase` translates it for TiDB/MySQL and adds
pooling, pre-ping, recycling, retries for reads and explicit transactions.

Both backends return :class:`zara.db.row.Row`, so the rest of the bot does not
need to know which database is active.
"""

from __future__ import annotations

import base64
import logging
import random
import re
import sqlite3
import ssl
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .config import DatabaseConfig, ssl_ca_material
from .crypto import EncryptionCodec
from .errors import (
    EncryptionError,
    EncryptionKeyMissingError,
    RecordNotFoundError,
    StorageConfigurationError,
    StorageError,
    StorageUnavailableError,
    StorageWriteError,
)
from .row import Row
from .schema import (
    COLUMN_MIGRATIONS,
    MIGRATIONS,
    MYSQL_DDL,
    MYSQL_INDEXES,
    REQUIRED_COLUMNS,
    SCHEMA_VERSION,
    SENSITIVE_COLUMNS,
    SQLITE_DDL,
    SQLITE_INDEXES,
    TABLE_ORDER,
)
from .translate import is_read_sql, to_mysql, to_sqlite

log = logging.getLogger(__name__)

_INSERT_RE = re.compile(
    r"(?is)^\s*(?:INSERT(?:\s+OR\s+\w+)?|REPLACE)\s+INTO\s+"
    r"([`\"\[]?[A-Za-z_][A-Za-z0-9_]*[`\"\]]?)\s*\(([^)]*)\)"
)
_UPDATE_RE = re.compile(
    r"(?is)^\s*UPDATE\s+([`\"\[]?[A-Za-z_][A-Za-z0-9_]*[`\"\]]?)\s+SET\s+(.*?)(?:\s+WHERE\s+.*)?$"
)

_TRANSIENT_MYSQL_CODES = {1040, 1105, 1205, 1213, 2006, 2013, 2055}
_SQLITE_RETRY_CODES = {"database is locked", "database table is locked"}


class Result:
    """Materialized result compatible with the sqlite3 cursor API we use."""

    __slots__ = ("_rows", "_position", "rowcount", "lastrowid", "columns")

    def __init__(
        self,
        rows: Sequence[Row],
        rowcount: int = -1,
        lastrowid: int | None = None,
        columns: tuple[str, ...] = (),
    ):
        self._rows = list(rows)
        self._position = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.columns = columns

    def fetchone(self) -> Row | None:
        if self._position >= len(self._rows):
            return None
        row = self._rows[self._position]
        self._position += 1
        return row

    def fetchall(self) -> list[Row]:
        rows = self._rows[self._position :]
        self._position = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[Row]:
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def __len__(self) -> int:
        return len(self._rows)


def _strip_identifier(value: str) -> str:
    return value.strip().strip("`\"[]").lower()


def _split_top_level(value: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in value:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _encrypt_params(sql: str, params: Sequence[Any], codec: EncryptionCodec) -> tuple[Any, ...]:
    """Encrypt parameters bound to declared sensitive columns."""
    if not codec.enabled or not params:
        return tuple(params)

    insert = _INSERT_RE.match(sql)
    if insert:
        table = _strip_identifier(insert.group(1))
        columns = [
            _strip_identifier(column)
            for column in insert.group(2).split(",")
            if column.strip()
        ]
        sensitive = set(SENSITIVE_COLUMNS.get(table, ()))
        if not sensitive:
            return tuple(params)
        if len(columns) != len(params):
            raise StorageConfigurationError(
                f"Encrypted multi-row INSERT for {table!r} is not supported; "
                "use one execute() per row."
            )
        values = list(params)
        for index, column in enumerate(columns):
            if column in sensitive:
                values[index] = codec.encrypt(values[index])
        return tuple(values)

    update = _UPDATE_RE.match(sql)
    if update:
        table = _strip_identifier(update.group(1))
        sensitive = set(SENSITIVE_COLUMNS.get(table, ()))
        if not sensitive:
            return tuple(params)
        values = list(params)
        parameter_index = 0
        for assignment in _split_top_level(update.group(2)):
            left, separator, right = assignment.partition("=")
            if not separator:
                continue
            placeholders = right.count("?")
            if placeholders == 1:
                column = _strip_identifier(left)
                if column in sensitive and parameter_index < len(values):
                    values[parameter_index] = codec.encrypt(values[parameter_index])
            parameter_index += placeholders
        return tuple(values)

    return tuple(params)


def _decrypt_rows(columns: tuple[str, ...], raw_rows: Iterable[Sequence[Any]], codec: EncryptionCodec) -> list[Row]:
    rows: list[Row] = []
    for raw in raw_rows:
        values = tuple(codec.decrypt(value) for value in raw)
        rows.append(Row(columns, values))
    return rows


class Database:
    """Common database interface."""

    kind = "base"

    def __init__(self, config: DatabaseConfig, codec: EncryptionCodec | None = None):
        self.config = config
        self.codec = codec or EncryptionCodec(config.encryption_key)
        self._lock = threading.RLock()
        self._local = threading.local()

    # ------------------------------------------------------------------ setup
    def initialize(self) -> None:
        with self._lock:
            self._create_schema()
            self._ensure_columns()
            self._ensure_indexes()
            self._record_migrations()
            self.verify_schema()

    def _create_schema(self) -> None:
        raise NotImplementedError

    def _ensure_columns(self) -> None:
        raise NotImplementedError

    def _ensure_indexes(self) -> None:
        raise NotImplementedError

    def _record_migrations(self) -> None:
        now = int(time.time())
        self.set_meta("schema_version", str(SCHEMA_VERSION))
        self.set_meta("storage_format", "zara-v1")
        self.set_meta("encryption", "enabled" if self.codec.enabled else "disabled")
        for version, _description in MIGRATIONS:
            self.execute(
                "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(?,?)",
                (version, now),
            )
        self.commit()

    # -------------------------------------------------------------- properties
    @property
    def encryption_enabled(self) -> bool:
        return self.codec.enabled

    def username_index(self, username: Any) -> str | None:
        return self.codec.blind_index(username)

    def export_key_fingerprint(self) -> str | None:
        return self.codec.export_key_fingerprint()

    # ----------------------------------------------------------------- execution
    def _prepare(self, sql: str, params: Sequence[Any]) -> tuple[str, tuple[Any, ...]]:
        values = tuple(params) if params is not None else ()
        values = _encrypt_params(sql, values, self.codec)
        if self.config.is_mysql:
            return to_mysql(sql), values
        return to_sqlite(sql), values

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Result:
        if params is None:
            params = ()
        if not isinstance(params, (tuple, list)):
            params = (params,)
        translated, values = self._prepare(sql, params)
        return self._execute(translated, values, is_read_sql(sql))

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> Result:
        prepared: list[tuple[Any, ...]] = []
        translated = ""
        for row in rows:
            values = tuple(row) if row is not None else ()
            translated, encrypted = self._prepare(sql, values)
            prepared.append(encrypted)
        return self._execute_many(translated, prepared, is_read_sql(sql))

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)
        self.commit()

    def _execute(self, sql: str, params: Sequence[Any], read: bool) -> Result:
        raise NotImplementedError

    def _execute_many(self, sql: str, params: Sequence[Sequence[Any]], read: bool) -> Result:
        raise NotImplementedError

    # ------------------------------------------------------------------ transactions
    @property
    def _active_connection(self) -> Any | None:
        return getattr(self._local, "connection", None)

    @property
    def in_transaction(self) -> bool:
        return self._active_connection is not None

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        with self._lock:
            active = self._active_connection
            if active is not None:
                depth = int(getattr(self._local, "depth", 0))
                savepoint = f"zara_sp_{depth}"
                self._savepoint(active, savepoint)
                self._local.depth = depth + 1
                try:
                    yield self
                except BaseException:
                    self._rollback_to_savepoint(active, savepoint)
                    raise
                else:
                    self._release_savepoint(active, savepoint)
                finally:
                    self._local.depth = depth
                return

            connection = self._acquire_transaction_connection()
            self._local.connection = connection
            self._local.depth = 0
            try:
                self._begin(connection)
                yield self
            except BaseException:
                self._rollback_transaction(connection)
                raise
            else:
                self._commit_transaction(connection)
            finally:
                self._local.connection = None
                self._local.depth = 0
                self._release_transaction_connection(connection)

    def commit(self) -> None:
        if self._active_connection is not None:
            # The outer transaction() context owns the commit.
            return
        self._commit_idle()

    def rollback(self) -> None:
        if self._active_connection is not None:
            return
        self._rollback_idle()

    def _acquire_transaction_connection(self) -> Any:
        raise NotImplementedError

    def _release_transaction_connection(self, connection: Any) -> None:
        raise NotImplementedError

    def _begin(self, connection: Any) -> None:
        raise NotImplementedError

    def _commit_transaction(self, connection: Any) -> None:
        raise NotImplementedError

    def _rollback_transaction(self, connection: Any) -> None:
        raise NotImplementedError

    def _savepoint(self, connection: Any, name: str) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(f"SAVEPOINT {name}")
        finally:
            cursor.close()

    def _release_savepoint(self, connection: Any, name: str) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(f"RELEASE SAVEPOINT {name}")
        finally:
            cursor.close()

    def _rollback_to_savepoint(self, connection: Any, name: str) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(f"ROLLBACK TO SAVEPOINT {name}")
            cursor.execute(f"RELEASE SAVEPOINT {name}")
        finally:
            cursor.close()

    def _commit_idle(self) -> None:
        pass

    def _rollback_idle(self) -> None:
        pass

    # ------------------------------------------------------------------ schema
    def table_names(self) -> set[str]:
        raise NotImplementedError

    def column_names(self, table: str) -> set[str]:
        raise NotImplementedError

    def verify_schema(self) -> None:
        existing_tables = self.table_names()
        missing_tables = sorted(set(TABLE_ORDER) - existing_tables)
        if missing_tables:
            raise StorageConfigurationError(
                "Database schema is incomplete; missing tables: " + ", ".join(missing_tables)
            )
        missing_columns: list[str] = []
        for table, required in REQUIRED_COLUMNS.items():
            present = self.column_names(table)
            for column in required:
                if column not in present:
                    missing_columns.append(f"{table}.{column}")
        if missing_columns:
            raise StorageConfigurationError(
                "Database schema is incomplete; missing columns: " + ", ".join(missing_columns)
            )

    def get_meta(self, key: str) -> str | None:
        row = self.execute(
            "SELECT meta_value FROM storage_meta WHERE meta_key=?",
            (key,),
        ).fetchone()
        return str(row["meta_value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO storage_meta(meta_key,meta_value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value,"
            "updated_at=excluded.updated_at",
            (key, value, int(time.time())),
        )
        self.commit()

    # ------------------------------------------------------------------ health
    def health_check(self) -> dict[str, Any]:
        started = time.perf_counter()
        row = self.execute("SELECT 1 AS ok").fetchone()
        if not row or int(row["ok"]) != 1:
            raise StorageUnavailableError("Database health probe returned no result")
        self.verify_schema()
        self._check_encryption()
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "backend": self.kind,
            "target": self.config.safe_label(),
            "latency_ms": latency_ms,
            "schema_version": self.get_meta("schema_version"),
            "encryption": self.codec.enabled,
            "tables": len(self.table_names()),
        }

    def _check_encryption(self) -> None:
        if self.codec.enabled:
            # Validate a small sample; Row decryption would already have raised
            # for the probe below if the key did not match.
            for table, columns in SENSITIVE_COLUMNS.items():
                column_list = ", ".join(columns)
                self.execute(f"SELECT {column_list} FROM {table} LIMIT 5").fetchall()
            return
        for table, columns in SENSITIVE_COLUMNS.items():
            for column in columns:
                row = self.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE {column} LIKE ?",
                    ("enc:v1:%",),
                ).fetchone()
                if row and int(row["n"]) > 0:
                    raise EncryptionKeyMissingError(
                        "Encrypted values exist in the database but "
                        "ZARA_ENCRYPTION_KEY is not configured."
                    )

    # ------------------------------------------------------------------ misc
    def close(self) -> None:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.config.safe_label()}>"


class SQLiteDatabase(Database):
    kind = "sqlite"

    def __init__(self, config: DatabaseConfig, codec: EncryptionCodec | None = None):
        super().__init__(config, codec)
        self.path = Path(config.sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,
            timeout=30.0,
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._connection.execute("PRAGMA foreign_keys=ON")

    def _create_schema(self) -> None:
        with self._lock:
            for statement in SQLITE_DDL:
                self._connection.executescript(statement)
            self._connection.commit()

    def _ensure_columns(self) -> None:
        for table, column, sqlite_type, _mysql_type in COLUMN_MIGRATIONS:
            if column not in self.column_names(table):
                self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sqlite_type}")
        self._connection.commit()

    def _ensure_indexes(self) -> None:
        for statement in SQLITE_INDEXES:
            self._connection.execute(statement)
        self._connection.commit()

    def _execute(self, sql: str, params: Sequence[Any], read: bool) -> Result:
        with self._lock:
            active = self._active_connection is not None
            attempts = self.config.retries + 1 if (read or not active) else 1
            last_error: Exception | None = None
            for attempt in range(attempts):
                cursor = None
                try:
                    cursor = self._connection.cursor()
                    cursor.execute(sql, params)
                    columns = tuple(item[0] for item in cursor.description) if cursor.description else ()
                    raw_rows = cursor.fetchall() if columns else []
                    rows = _decrypt_rows(columns, raw_rows, self.codec)
                    result = Result(rows, cursor.rowcount, cursor.lastrowid, columns)
                    cursor.close()
                    if not active:
                        self._connection.commit()
                    return result
                except sqlite3.OperationalError as exc:
                    if cursor is not None:
                        cursor.close()
                    if not active:
                        self._connection.rollback()
                    last_error = exc
                    message = str(exc).lower()
                    if (
                        any(code in message for code in _SQLITE_RETRY_CODES)
                        and attempt + 1 < attempts
                    ):
                        time.sleep(self.config.retry_base_delay * (2**attempt) + random.random() * 0.05)
                        continue
                    raise StorageUnavailableError(f"SQLite unavailable: {exc}") from exc
                except sqlite3.IntegrityError as exc:
                    if cursor is not None:
                        cursor.close()
                    if not active:
                        self._connection.rollback()
                    raise StorageWriteError(f"SQLite rejected the write: {exc}") from exc
                except sqlite3.Error as exc:
                    if cursor is not None:
                        cursor.close()
                    if not active:
                        self._connection.rollback()
                    raise StorageError(f"SQLite error: {exc}") from exc
            raise StorageUnavailableError(f"SQLite unavailable: {last_error}")

    def _execute_many(self, sql: str, params: Sequence[Sequence[Any]], read: bool) -> Result:
        with self._lock:
            active = self._active_connection is not None
            cursor = self._connection.cursor()
            try:
                cursor.executemany(sql, params)
                result = Result([], cursor.rowcount, cursor.lastrowid, ())
                if not active:
                    self._connection.commit()
                return result
            except sqlite3.Error as exc:
                if not active:
                    self._connection.rollback()
                raise StorageError(f"SQLite error: {exc}") from exc
            finally:
                cursor.close()

    def _acquire_transaction_connection(self) -> Any:
        return self._connection

    def _release_transaction_connection(self, connection: Any) -> None:
        return None

    def _begin(self, connection: Any) -> None:
        connection.execute("BEGIN IMMEDIATE")

    def _commit_transaction(self, connection: Any) -> None:
        connection.commit()

    def _rollback_transaction(self, connection: Any) -> None:
        connection.rollback()

    def _commit_idle(self) -> None:
        with self._lock:
            self._connection.commit()

    def _rollback_idle(self) -> None:
        with self._lock:
            self._connection.rollback()

    def table_names(self) -> set[str]:
        rows = self.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {str(row["name"]) for row in rows}

    def column_names(self, table: str) -> set[str]:
        rows = self.execute("SELECT name FROM pragma_table_info(?)", (table,)).fetchall()
        return {str(row["name"]) for row in rows}

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class MySQLDatabase(Database):
    kind = "mysql"

    def __init__(self, config: DatabaseConfig, codec: EncryptionCodec | None = None):
        super().__init__(config, codec)
        try:
            import pymysql  # noqa: F401
            from sqlalchemy import create_engine
        except ImportError as exc:  # pragma: no cover - depends on deployment
            raise StorageConfigurationError(
                "MySQL/TiDB support requires PyMySQL and SQLAlchemy. "
                "Install them with: pip install -r requirements.txt"
            ) from exc
        self._engine = create_engine(
            "mysql+pymysql://",
            creator=self._connect,
            pool_pre_ping=True,
            pool_recycle=config.pool_recycle,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_timeout=config.pool_timeout,
            future=True,
        )

    def _connect(self) -> Any:
        import pymysql
        from pymysql.constants import CLIENT

        ssl_context = None
        if self.config.ssl_mode != "disabled":
            ca = ssl_ca_material(self.config.ssl_ca)
            if ca:
                if ca.startswith("-----BEGIN"):
                    ssl_context = ssl.create_default_context(cadata=ca)
                else:
                    ssl_context = ssl.create_default_context(cafile=ca)
            else:
                ssl_context = ssl.create_default_context()
            if self.config.ssl_mode == "skip-verify":
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=self.config.connect_timeout,
            read_timeout=self.config.read_timeout,
            write_timeout=self.config.write_timeout,
            client_flag=CLIENT.FOUND_ROWS,
            ssl=ssl_context,
        )

    def _acquire_connection(self) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.config.retries + 1):
            try:
                return self._engine.raw_connection()
            except Exception as exc:  # noqa: BLE001 - SQLAlchemy wraps DB errors
                last_error = exc
                if attempt + 1 < self.config.retries + 1:
                    time.sleep(self.config.retry_base_delay * (2**attempt) + random.random() * 0.1)
        raise StorageUnavailableError(f"Could not connect to MySQL/TiDB: {last_error}") from last_error

    def _create_schema(self) -> None:
        for statement in MYSQL_DDL:
            self._run_ddl(statement)

    def _ensure_columns(self) -> None:
        for table, column, _sqlite_type, mysql_type in COLUMN_MIGRATIONS:
            if column not in self.column_names(table):
                self._run_ddl(f"ALTER TABLE {table} ADD COLUMN {column} {mysql_type}")

    def _ensure_indexes(self) -> None:
        for index_name, table, column in MYSQL_INDEXES:
            row = self.execute(
                "SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND INDEX_NAME=?",
                (self.config.database, table, index_name),
            ).fetchone()
            if not row or int(row["n"]) == 0:
                self._run_ddl(f"CREATE INDEX {index_name} ON {table}({column})")

    def _run_ddl(self, sql: str) -> None:
        with self._lock:
            connection = self._acquire_connection()
            cursor = None
            try:
                cursor = connection.cursor()
                cursor.execute(sql)
                connection.commit()
            finally:
                if cursor is not None:
                    cursor.close()
                connection.close()

    def _execute(self, sql: str, params: Sequence[Any], read: bool) -> Result:
        import pymysql

        with self._lock:
            active = self._active_connection is not None
            attempts = self.config.retries + 1 if (read and not active) else 1
            last_error: Exception | None = None
            for attempt in range(attempts):
                connection = active or self._acquire_connection()
                cursor = None
                try:
                    cursor = connection.cursor()
                    cursor.execute(sql, params)
                    columns = tuple(item[0] for item in cursor.description) if cursor.description else ()
                    raw_rows = cursor.fetchall() if columns else []
                    rows = _decrypt_rows(columns, raw_rows, self.codec)
                    result = Result(rows, cursor.rowcount, cursor.lastrowid, columns)
                    cursor.close()
                    if not active:
                        connection.commit()
                    return result
                except pymysql.err.OperationalError as exc:
                    if cursor is not None:
                        cursor.close()
                    if not active:
                        try:
                            connection.rollback()
                        except Exception:  # noqa: BLE001
                            pass
                    last_error = exc
                    code = exc.args[0] if exc.args else None
                    if (
                        not active
                        and read
                        and code in _TRANSIENT_MYSQL_CODES
                        and attempt + 1 < attempts
                    ):
                        time.sleep(self.config.retry_base_delay * (2**attempt) + random.random() * 0.1)
                        continue
                    raise StorageUnavailableError(
                        f"MySQL/TiDB unavailable (error {code}): {exc}"
                    ) from exc
                except pymysql.err.IntegrityError as exc:
                    if cursor is not None:
                        cursor.close()
                    if not active:
                        try:
                            connection.rollback()
                        except Exception:  # noqa: BLE001
                            pass
                    raise StorageWriteError(f"MySQL/TiDB rejected the write: {exc}") from exc
                except pymysql.err.MySQLError as exc:
                    if cursor is not None:
                        cursor.close()
                    if not active:
                        try:
                            connection.rollback()
                        except Exception:  # noqa: BLE001
                            pass
                    raise StorageError(f"MySQL/TiDB error: {exc}") from exc
                finally:
                    if not active:
                        connection.close()
            raise StorageUnavailableError(f"MySQL/TiDB unavailable: {last_error}")

    def _execute_many(self, sql: str, params: Sequence[Sequence[Any]], read: bool) -> Result:
        import pymysql

        with self._lock:
            active = self._active_connection is not None
            connection = active or self._acquire_connection()
            cursor = None
            try:
                cursor = connection.cursor()
                cursor.executemany(sql, params)
                result = Result([], cursor.rowcount, cursor.lastrowid, ())
                if not active:
                    connection.commit()
                return result
            except pymysql.err.MySQLError as exc:
                if not active:
                    try:
                        connection.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                raise StorageError(f"MySQL/TiDB error: {exc}") from exc
            finally:
                if cursor is not None:
                    cursor.close()
                if not active:
                    connection.close()

    def _acquire_transaction_connection(self) -> Any:
        return self._acquire_connection()

    def _release_transaction_connection(self, connection: Any) -> None:
        connection.close()

    def _begin(self, connection: Any) -> None:
        connection.begin()

    def _commit_transaction(self, connection: Any) -> None:
        connection.commit()

    def _rollback_transaction(self, connection: Any) -> None:
        connection.rollback()

    def table_names(self) -> set[str]:
        rows = self.execute(
            "SELECT TABLE_NAME AS name FROM information_schema.TABLES WHERE TABLE_SCHEMA=?",
            (self.config.database,),
        ).fetchall()
        return {str(row["name"]) for row in rows}

    def column_names(self, table: str) -> set[str]:
        rows = self.execute(
            "SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=? AND TABLE_NAME=?",
            (self.config.database, table),
        ).fetchall()
        return {str(row["name"]) for row in rows}

    def close(self) -> None:
        self._engine.dispose()


def create_database(
    config: DatabaseConfig | None = None,
    *,
    initialize: bool = True,
    explicit_sqlite_path: Path | None = None,
) -> Database:
    resolved = config or DatabaseConfig.from_env(explicit_sqlite_path=explicit_sqlite_path)
    database: Database
    if resolved.is_sqlite:
        database = SQLiteDatabase(resolved)
    elif resolved.is_mysql:
        database = MySQLDatabase(resolved)
    else:  # pragma: no cover - config validation prevents this
        raise StorageConfigurationError(f"Unsupported database backend: {resolved.backend}")
    if initialize:
        database.initialize()
    return database
