"""Backup, export, import, migration and retention operations.

All operations are backend independent.  Exports are logical JSON snapshots;
when an encryption key is configured they are encrypted with the same Fernet
key as the sensitive database columns.  Restores make a pre-restore backup and
verify every restored row before reporting success.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .core import Database, create_database
from .crypto import PREFIX, EncryptionCodec
from .errors import BackupError, MigrationError, StorageError
from .row import Row
from .schema import (
    DATA_TABLES,
    PRIMARY_KEYS,
    SCHEMA_VERSION,
    SENSITIVE_COLUMNS,
    TABLE_ORDER,
)

log = logging.getLogger(__name__)

EXPORT_FORMAT = "zara-storage-export"
EXPORT_VERSION = 1


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _checksum(tables: dict[str, list[dict[str, Any]]]) -> str:
    return hashlib.sha256(_json_bytes(tables)).hexdigest()


def _row_to_dict(row: Row) -> dict[str, Any]:
    return {column: value for column, value in row.items()}


def _table_rows(db: Database, table: str) -> list[dict[str, Any]]:
    rows = db.execute(f"SELECT * FROM {table}").fetchall()
    return [_row_to_dict(row) for row in rows]


def export_payload(db: Database) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in TABLE_ORDER:
        tables[table] = _table_rows(db, table)
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": int(time.time()),
        "tables": tables,
        "row_counts": {table: len(rows) for table, rows in tables.items()},
        "checksum": _checksum(tables),
    }


def encode_export(payload: dict[str, Any], codec: EncryptionCodec, encrypt: bool) -> str:
    tables = payload["tables"]
    header = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "schema_version": payload["schema_version"],
        "created_at": payload["created_at"],
        "checksum": payload["checksum"],
        "encrypted": bool(encrypt),
    }
    if encrypt:
        if not codec.enabled:
            raise BackupError("Encrypted export requested but ZARA_ENCRYPTION_KEY is not set.")
        body = codec.encrypt(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        header["payload"] = body
    else:
        header["tables"] = tables
        header["row_counts"] = payload.get("row_counts", {})
    return json.dumps(header, ensure_ascii=False, sort_keys=True, indent=2)


def decode_export(text: str, codec: EncryptionCodec) -> dict[str, Any]:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BackupError(f"Backup is not valid JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != EXPORT_FORMAT:
        raise BackupError("File is not a Zara storage export.")
    if int(document.get("version", 0)) != EXPORT_VERSION:
        raise BackupError(f"Unsupported export version: {document.get('version')}")
    if document.get("encrypted"):
        if not codec.enabled:
            raise BackupError(
                "This export is encrypted but ZARA_ENCRYPTION_KEY is not configured."
            )
        payload_text = codec.decrypt(document.get("payload"))
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise BackupError(f"Decrypted export is not valid JSON: {exc}") from exc
    else:
        payload = {
            "schema_version": document.get("schema_version", SCHEMA_VERSION),
            "created_at": document.get("created_at", 0),
            "tables": document.get("tables", {}),
            "row_counts": document.get("row_counts", {}),
            "checksum": document.get("checksum", ""),
        }
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise BackupError("Export does not contain a tables object.")
    actual = _checksum(tables)
    expected = str(payload.get("checksum") or document.get("checksum") or "")
    if expected and actual != expected:
        raise BackupError(
            "Backup checksum mismatch; the file is corrupted or was modified."
        )
    payload["checksum"] = actual
    payload["row_counts"] = {
        table: len(rows) for table, rows in tables.items() if isinstance(rows, list)
    }
    return payload


def _atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(temp, flags, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def export_to_file(
    db: Database,
    path: Path,
    *,
    encrypt: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    if path.exists() and not force:
        raise BackupError(f"Refusing to overwrite {path}; pass --force to replace it.")
    payload = export_payload(db)
    text = encode_export(payload, db.codec, encrypt)
    _atomic_write(path, text)
    # Read back and validate before reporting success.
    decoded = decode_export(path.read_text(encoding="utf-8"), db.codec)
    if decoded["checksum"] != payload["checksum"]:
        raise BackupError("Export verification failed after writing the file.")
    return {
        "path": str(path),
        "encrypted": encrypt,
        "tables": len(payload["tables"]),
        "rows": sum(payload["row_counts"].values()),
        "checksum": payload["checksum"],
    }


def backup(
    db: Database,
    backup_dir: Path,
    *,
    encrypt: bool = True,
) -> dict[str, Any]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".json.enc" if encrypt else ".json"
    path = backup_dir / f"zara-backup-{_timestamp()}{suffix}"
    counter = 1
    while path.exists():
        path = backup_dir / f"zara-backup-{_timestamp()}-{counter}{suffix}"
        counter += 1
    result = export_to_file(db, path, encrypt=encrypt, force=False)
    log.info("Storage backup written to %s", path)
    return result


def read_export(path: Path, codec: EncryptionCodec) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BackupError(f"Could not read {path}: {exc}") from exc
    return decode_export(text, codec)


def _upsert_sql(table: str, columns: Sequence[str]) -> str:
    keys = PRIMARY_KEYS[table]
    placeholders = ",".join("?" for _ in columns)
    update_columns = [column for column in columns if column not in keys]
    base = f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})"
    if update_columns:
        assignments = ",".join(f"{column}=excluded.{column}" for column in update_columns)
        conflict = ",".join(keys)
        return f"{base} ON CONFLICT({conflict}) DO UPDATE SET {assignments}"
    conflict = ",".join(keys)
    return f"{base} ON CONFLICT({conflict}) DO NOTHING"


def import_payload(
    db: Database,
    payload: dict[str, Any],
    *,
    replace: bool = False,
) -> dict[str, Any]:
    tables = payload.get("tables") or {}
    missing = [table for table in TABLE_ORDER if table not in tables]
    if missing:
        raise BackupError("Export is missing required tables: " + ", ".join(missing))
    schema_version = int(payload.get("schema_version") or 0)
    if schema_version > SCHEMA_VERSION:
        raise BackupError(
            f"Export schema version {schema_version} is newer than this build "
            f"({SCHEMA_VERSION}); upgrade the bot first."
        )

    # The blind index is keyed, so it must be rebuilt for the target key.
    user_rows = tables.get("users", [])
    for row in user_rows:
        row["username_hash"] = db.username_index(row.get("username"))

    with db.transaction():
        if replace:
            for table in reversed(TABLE_ORDER):
                db.execute(f"DELETE FROM {table}")
        for table in TABLE_ORDER:
            rows = tables.get(table) or []
            for row in rows:
                columns = list(row.keys())
                if not columns:
                    continue
                db.execute(_upsert_sql(table, columns), tuple(row[column] for column in columns))
    db.set_meta("schema_version", str(SCHEMA_VERSION))
    db.set_meta("storage_format", "zara-v1")
    db.set_meta("encryption", "enabled" if db.encryption_enabled else "disabled")

    _verify_imported_rows(db, tables)
    return {
        "tables": len(tables),
        "rows": sum(len(rows) for rows in tables.values()),
        "checksum": _checksum(tables),
        "replaced": replace,
    }


def _verify_imported_rows(db: Database, tables: dict[str, list[dict[str, Any]]]) -> None:
    for table, rows in tables.items():
        if table not in PRIMARY_KEYS:
            continue
        keys = PRIMARY_KEYS[table]
        conditions = " AND ".join(f"{key}=?" for key in keys)
        for expected in rows:
            values = tuple(expected[key] for key in keys)
            actual = db.execute(
                f"SELECT * FROM {table} WHERE {conditions}", values
            ).fetchone()
            if actual is None:
                raise BackupError(f"Restore verification failed: missing {table} row {values}")
            for column, expected_value in expected.items():
                if column == "username_hash" and table == "users":
                    continue
                if table == "storage_meta" and column == "updated_at":
                    # import_payload refreshes metadata timestamps on purpose.
                    continue
                actual_value = actual[column]
                if actual_value != expected_value:
                    raise BackupError(
                        f"Restore verification failed for {table} row {values}, "
                        f"column {column}."
                    )


def restore(
    db: Database,
    path: Path,
    *,
    replace: bool = False,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    payload = read_export(path, db.codec)
    pre_restore = None
    if backup_dir is not None:
        pre_restore = backup(db, backup_dir, encrypt=db.encryption_enabled)
    result = import_payload(db, payload, replace=replace)
    result["pre_restore_backup"] = pre_restore["path"] if pre_restore else None
    result["path"] = str(path)
    return result


# --------------------------------------------------------------------------
# Legacy SQLite migration
# --------------------------------------------------------------------------
def _legacy_tables(source: sqlite3.Connection) -> set[str]:
    rows = source.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _source_payload(source_path: Path) -> tuple[dict[str, Any], dict[str, int]]:
    if not source_path.is_file():
        raise MigrationError(f"Legacy SQLite database not found: {source_path}")
    source = sqlite3.connect(str(source_path))
    source.row_factory = sqlite3.Row
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if str(integrity).lower() != "ok":
            raise MigrationError(f"Legacy SQLite integrity check failed: {integrity}")
        source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        existing = _legacy_tables(source)
        tables: dict[str, list[dict[str, Any]]] = {}
        counts: dict[str, int] = {}
        for table in TABLE_ORDER:
            if table not in existing:
                tables[table] = []
                counts[table] = 0
                continue
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            materialized: list[dict[str, Any]] = []
            for row in rows:
                item = {key: row[key] for key in row.keys()}
                for column, value in item.items():
                    if isinstance(value, str) and value.startswith(PREFIX):
                        raise MigrationError(
                            f"Legacy database column {table}.{column} is already "
                            "encrypted; export it with the original key instead."
                        )
                materialized.append(item)
            tables[table] = materialized
            counts[table] = len(materialized)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "created_at": int(time.time()),
            "tables": tables,
            "row_counts": counts,
            "checksum": _checksum(tables),
        }
        return payload, counts
    finally:
        source.close()


def _copy_sqlite_backup(source_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"legacy-sqlite-{_timestamp()}.sqlite3"
    counter = 1
    while destination.exists():
        destination = backup_dir / f"legacy-sqlite-{_timestamp()}-{counter}.sqlite3"
        counter += 1
    shutil.copy2(source_path, destination)
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    # Verify the copied file before touching the target.
    copy = sqlite3.connect(str(destination))
    try:
        integrity = copy.execute("PRAGMA integrity_check").fetchone()[0]
        if str(integrity).lower() != "ok":
            raise MigrationError("Copied legacy backup failed its integrity check.")
    finally:
        copy.close()
    return destination


def migrate_legacy(
    source_path: Path,
    target: Database,
    backup_dir: Path,
) -> dict[str, Any]:
    source_path = Path(source_path).expanduser().resolve()
    if target.config.is_sqlite:
        try:
            if source_path == Path(target.config.sqlite_path).expanduser().resolve():
                raise MigrationError("Source and target SQLite paths are the same.")
        except OSError:
            pass
    payload, source_counts = _source_payload(source_path)
    legacy_backup = _copy_sqlite_backup(source_path, backup_dir)
    pre_target_backup = backup(target, backup_dir, encrypt=target.encryption_enabled)
    result = import_payload(target, payload, replace=False)
    target_counts = {
        table: int(target.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        for table in TABLE_ORDER
    }
    for table, expected in source_counts.items():
        if target_counts.get(table, 0) < expected:
            raise MigrationError(
                f"Migration verification failed for {table}: expected at least "
                f"{expected} rows, found {target_counts.get(table, 0)}."
            )
    return {
        "source": str(source_path),
        "legacy_backup": str(legacy_backup),
        "target_backup": pre_target_backup["path"],
        "source_rows": sum(source_counts.values()),
        "target_rows": target_counts,
        "imported": result,
    }


# --------------------------------------------------------------------------
# Re-encryption, privacy deletion, retention, integrity
# --------------------------------------------------------------------------
def reencrypt_all(db: Database) -> dict[str, int]:
    if not db.codec.enabled:
        raise BackupError("ZARA_ENCRYPTION_KEY is required for re-encryption.")
    updated = 0
    with db.transaction():
        for table, columns in SENSITIVE_COLUMNS.items():
            keys = PRIMARY_KEYS[table]
            key_columns = ",".join(keys)
            column_list = ",".join(columns)
            rows = db.execute(f"SELECT {key_columns},{column_list} FROM {table}").fetchall()
            where = " AND ".join(f"{key}=?" for key in keys)
            for row in rows:
                values = [row[column] for column in columns]
                assignments = ",".join(f"{column}=?" for column in columns)
                parameters: list[Any] = list(values)
                if table == "users":
                    parameters.append(db.username_index(row["username"]))
                    assignments += ",username_hash=?"
                parameters.extend(row[key] for key in keys)
                db.execute(
                    f"UPDATE {table} SET {assignments} WHERE {where}",
                    tuple(parameters),
                )
                updated += 1
    db.set_meta("encryption", "enabled")
    return {"rows": updated}


def _remove_user_from_sessions(db: Database, user_id: int) -> None:
    marker = str(user_id)
    for table in ("game_sessions", "hack_sessions", "bomb_sessions"):
        for row in db.execute(f"SELECT chat_id,state FROM {table}").fetchall():
            try:
                state = json.loads(row["state"])
            except (TypeError, json.JSONDecodeError):
                continue
            changed = False
            players = state.get("players")
            if isinstance(players, dict) and marker in players:
                players.pop(marker, None)
                changed = True
            if isinstance(players, list) and user_id in players:
                state["players"] = [item for item in players if item != user_id]
                changed = True
            if int(state.get("host_id", 0)) == user_id:
                db.execute(f"DELETE FROM {table} WHERE chat_id=?", (int(row["chat_id"]),))
                continue
            if changed:
                db.execute(
                    f"UPDATE {table} SET state=?,updated_at=? WHERE chat_id=?",
                    (json.dumps(state), int(time.time()), int(row["chat_id"])),
                )


def delete_user(db: Database, user_id: int) -> dict[str, int]:
    user_id = int(user_id)
    deleted: dict[str, int] = {}
    with db.transaction():
        _remove_user_from_sessions(db, user_id)
        statements = (
            ("users", "DELETE FROM users WHERE user_id=?", (user_id,)),
            ("couples", "DELETE FROM couples WHERE user_id=? OR partner_id=?", (user_id, user_id)),
            ("items", "DELETE FROM items WHERE user_id=?", (user_id,)),
            ("user_settings", "DELETE FROM user_settings WHERE user_id=?", (user_id,)),
            ("approvals", "DELETE FROM approvals WHERE user_id=?", (user_id,)),
            ("warnings", "DELETE FROM warnings WHERE user_id=?", (user_id,)),
            ("chat_members", "DELETE FROM chat_members WHERE user_id=?", (user_id,)),
        )
        for label, statement, parameters in statements:
            result = db.execute(statement, parameters)
            deleted[label] = max(0, int(result.rowcount))
    return deleted


def purge_inactive(
    db: Database,
    days: int,
    *,
    dry_run: bool = True,
    exclude_ids: Iterable[int] = (),
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")
    cutoff = int(time.time()) - days * 86400
    excluded = {int(value) for value in exclude_ids}
    rows = db.execute(
        "SELECT user_id FROM users WHERE updated_at < ? ORDER BY user_id",
        (cutoff,),
    ).fetchall()
    targets = [int(row["user_id"]) for row in rows if int(row["user_id"]) not in excluded]
    if dry_run:
        return {"dry_run": True, "cutoff": cutoff, "users": targets, "count": len(targets)}
    deleted = 0
    details: dict[str, int] = {}
    for user_id in targets:
        result = delete_user(db, user_id)
        details["users"] = details.get("users", 0) + result.get("users", 0)
        deleted += 1
    return {"dry_run": False, "cutoff": cutoff, "deleted_users": deleted, "details": details}


def integrity_report(db: Database) -> dict[str, Any]:
    counts = {
        table: int(db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        for table in TABLE_ORDER
    }
    user_rows = counts.get("users", 0)
    missing_hash = 0
    if db.encryption_enabled:
        row = db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE username IS NOT NULL "
            "AND (username_hash IS NULL OR username_hash='')"
        ).fetchone()
        missing_hash = int(row["n"]) if row else 0
    return {
        "backend": db.kind,
        "target": db.config.safe_label(),
        "schema_version": db.get_meta("schema_version"),
        "encryption": db.encryption_enabled,
        "key_fingerprint": db.export_key_fingerprint(),
        "counts": counts,
        "data_rows": sum(counts.get(table, 0) for table in DATA_TABLES),
        "users": user_rows,
        "users_missing_username_hash": missing_hash,
    }
