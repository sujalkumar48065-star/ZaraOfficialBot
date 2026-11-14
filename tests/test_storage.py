"""Storage-layer tests that run against a temporary SQLite database.

TiDB/MySQL integration needs real credentials and is intentionally covered by
``tests/test_storage_mysql.py`` (skipped unless ZARA_TEST_MYSQL_URL is set).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from zara.db import (
    BackupError,
    DatabaseConfig,
    EncryptionCodec,
    EncryptionError,
    EncryptionKeyMissingError,
    InsufficientBalanceError,
    Row,
    StorageConfigurationError,
    StorageWriteError,
    create_database,
)
from zara.db.crypto import PREFIX
from zara.db.maintenance import (
    backup,
    delete_user,
    encode_export,
    decode_export,
    export_payload,
    export_to_file,
    import_payload,
    integrity_report,
    migrate_legacy,
    purge_inactive,
    reencrypt_all,
    restore,
)
from zara.db.schema import SCHEMA_VERSION, TABLE_ORDER
from zara.db.translate import is_read_sql, to_mysql, to_sqlite


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def sqlite_config(path: Path, key: str = "") -> DatabaseConfig:
    return DatabaseConfig(backend="sqlite", sqlite_path=Path(path), encryption_key=key)


def make_db(path: Path, key: str = ""):
    return create_database(sqlite_config(path, key))


def insert_user(
    db,
    user_id: int = 1,
    username: str | None = "Alice",
    name: str = "Alice",
    balance: int = 1000,
    updated_at: int | None = None,
) -> None:
    stamp = int(time.time()) if updated_at is None else int(updated_at)
    db.execute(
        "INSERT INTO users(user_id,username,username_hash,name,balance,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (user_id, username, db.username_index(username), name, balance, stamp, stamp),
    )
    db.commit()


def raw_user(path: Path) -> tuple:
    connection = sqlite3.connect(str(path))
    try:
        return connection.execute(
            "SELECT username,name,username_hash FROM users WHERE user_id=1"
        ).fetchone()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# schema, rows, health
# ---------------------------------------------------------------------------
def test_schema_initializes_and_health_is_green(tmp_path: Path) -> None:
    db = make_db(tmp_path / "zara.sqlite3")
    try:
        health = db.health_check()
        assert health["backend"] == "sqlite"
        assert health["schema_version"] == str(SCHEMA_VERSION)
        assert health["tables"] == len(TABLE_ORDER) == 16
        assert db.table_names() >= set(TABLE_ORDER)
        assert db.get_meta("storage_format") == "zara-v1"
        assert db.get_meta("encryption") == "disabled"
        assert integrity_report(db)["counts"]["users"] == 0
    finally:
        db.close()


def test_row_supports_name_position_and_mapping_access() -> None:
    row = Row(("user_id", "balance"), (7, 250))
    assert row["user_id"] == 7
    assert row[1] == 250
    assert row.get("missing", "fallback") == "fallback"
    assert row.keys() == ("user_id", "balance")
    assert list(row) == [7, 250]
    assert dict(row) == {"user_id": 7, "balance": 250}
    assert "balance" in row
    assert repr(row) == "Row(user_id=7, balance=250)"


# ---------------------------------------------------------------------------
# encryption and blind index
# ---------------------------------------------------------------------------
def test_encryption_roundtrip_and_ciphertext_at_rest(tmp_path: Path) -> None:
    key = EncryptionCodec.generate_key()
    path = tmp_path / "encrypted.sqlite3"
    db = make_db(path, key)
    try:
        insert_user(db, username="Alice", name="Alice Secret")
        row = db.execute("SELECT * FROM users WHERE user_id=1").fetchone()
        assert row["username"] == "Alice"
        assert row["name"] == "Alice Secret"
        assert row["username_hash"] == db.username_index("@ALICE")
        assert db.execute(
            "SELECT * FROM users WHERE username_hash=?",
            (db.username_index("alice"),),
        ).fetchone() is not None
        raw_username, raw_name, raw_hash = raw_user(path)
        assert raw_username.startswith(PREFIX)
        assert raw_name.startswith(PREFIX)
        assert "Alice" not in raw_username
        assert raw_hash == db.username_index("alice")
        assert integrity_report(db)["users_missing_username_hash"] == 0
    finally:
        db.close()


def test_encrypted_database_without_key_is_refused(tmp_path: Path) -> None:
    key = EncryptionCodec.generate_key()
    path = tmp_path / "encrypted.sqlite3"
    db = make_db(path, key)
    try:
        insert_user(db, username="Alice", name="Alice")
    finally:
        db.close()

    missing = make_db(path)
    try:
        with pytest.raises(EncryptionKeyMissingError):
            missing.health_check()
    finally:
        missing.close()

    wrong = make_db(path, EncryptionCodec.generate_key())
    try:
        with pytest.raises(EncryptionError):
            wrong.health_check()
    finally:
        wrong.close()


def test_reencrypt_all_encrypts_legacy_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "legacy-plain.sqlite3"
    db = make_db(path)
    try:
        insert_user(db, username="Alice", name="Alice Secret")
        assert raw_user(path)[0] == "Alice"
    finally:
        db.close()

    key = EncryptionCodec.generate_key()
    encrypted = make_db(path, key)
    try:
        result = reencrypt_all(encrypted)
        assert result["rows"] >= 1
        raw_username, raw_name, raw_hash = raw_user(path)
        assert raw_username.startswith(PREFIX)
        assert raw_name.startswith(PREFIX)
        assert raw_hash == encrypted.username_index("alice")
        assert encrypted.execute("SELECT * FROM users").fetchone()["username"] == "Alice"
    finally:
        encrypted.close()


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------
def test_transaction_rolls_back_every_write(tmp_path: Path) -> None:
    db = make_db(tmp_path / "zara.sqlite3")
    try:
        with pytest.raises(RuntimeError):
            with db.transaction():
                insert_user(db, 1, "Alice")
                insert_user(db, 2, "Bob")
                raise RuntimeError("boom")
        assert db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
    finally:
        db.close()


def test_nested_transaction_uses_savepoint(tmp_path: Path) -> None:
    db = make_db(tmp_path / "zara.sqlite3")
    try:
        with db.transaction():
            insert_user(db, 1, "Alice")
            with pytest.raises(RuntimeError):
                with db.transaction():
                    insert_user(db, 2, "Bob")
                    raise RuntimeError("inner")
        assert db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1
        assert db.execute("SELECT username FROM users WHERE user_id=1").fetchone()["username"] == "Alice"
    finally:
        db.close()


def test_write_errors_are_storage_errors(tmp_path: Path) -> None:
    db = make_db(tmp_path / "zara.sqlite3")
    try:
        insert_user(db, 1, "Alice")
        with pytest.raises(StorageWriteError):
            insert_user(db, 1, "Duplicate")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# export / import / backup / restore
# ---------------------------------------------------------------------------
def test_encrypted_export_import_roundtrip(tmp_path: Path) -> None:
    key = EncryptionCodec.generate_key()
    source = make_db(tmp_path / "source.sqlite3", key)
    try:
        insert_user(source, 1, "Alice", "Alice Secret", 750)
        source.execute(
            "INSERT INTO chats(chat_id,title,created_at) VALUES(?,?,?)",
            (10, "Test Chat", int(time.time())),
        )
        source.execute(
            "INSERT INTO filters(chat_id,word,response) VALUES(?,?,?)",
            (10, "hello", "Hi there"),
        )
        source.commit()
        payload = export_payload(source)
        assert payload["tables"]["users"][0]["username"] == "Alice"
        text = encode_export(payload, source.codec, True)
        assert "Alice" not in text
        assert "Hi there" not in text
        decoded = decode_export(text, source.codec)
        assert decoded["checksum"] == payload["checksum"]
    finally:
        source.close()

    target = make_db(tmp_path / "target.sqlite3", key)
    try:
        result = import_payload(target, decoded)
        assert result["rows"] >= 3
        user = target.execute("SELECT * FROM users WHERE user_id=1").fetchone()
        assert user["username"] == "Alice"
        assert user["name"] == "Alice Secret"
        assert user["balance"] == 750
        assert target.execute("SELECT * FROM filters").fetchone()["response"] == "Hi there"
        assert target.get_meta("schema_version") == str(SCHEMA_VERSION)
    finally:
        target.close()


def test_export_checksum_rejects_tampering(tmp_path: Path) -> None:
    db = make_db(tmp_path / "zara.sqlite3")
    try:
        insert_user(db)
        payload = export_payload(db)
        text = encode_export(payload, db.codec, False)
        document = json.loads(text)
        document["tables"]["users"][0]["name"] = "Mallory"
        with pytest.raises(BackupError, match="checksum"):
            decode_export(json.dumps(document), db.codec)
    finally:
        db.close()


def test_encrypted_export_requires_correct_key(tmp_path: Path) -> None:
    key = EncryptionCodec.generate_key()
    db = make_db(tmp_path / "zara.sqlite3", key)
    try:
        insert_user(db)
        text = encode_export(export_payload(db), db.codec, True)
    finally:
        db.close()
    with pytest.raises(EncryptionError):
        decode_export(text, EncryptionCodec(EncryptionCodec.generate_key()))
    with pytest.raises(BackupError, match="ZARA_ENCRYPTION_KEY"):
        decode_export(text, EncryptionCodec(""))


def test_backup_and_restore_roundtrip(tmp_path: Path) -> None:
    key = EncryptionCodec.generate_key()
    db = make_db(tmp_path / "zara.sqlite3", key)
    try:
        insert_user(db, 1, "Alice", "Alice Secret")
        backup_result = backup(db, tmp_path / "backups", encrypt=True)
        backup_path = Path(backup_result["path"])
        assert backup_path.is_file()
        assert backup_path.stat().st_mode & 0o077 == 0

        db.execute("DELETE FROM users")
        db.commit()
        assert db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0

        restored = restore(
            db,
            backup_path,
            replace=True,
            backup_dir=tmp_path / "pre-restore",
        )
        assert restored["pre_restore_backup"]
        assert db.execute("SELECT username FROM users WHERE user_id=1").fetchone()["username"] == "Alice"
    finally:
        db.close()


def test_export_refuses_overwrite_without_force(tmp_path: Path) -> None:
    db = make_db(tmp_path / "zara.sqlite3")
    try:
        output = tmp_path / "export.json"
        export_to_file(db, output, encrypt=False)
        with pytest.raises(BackupError, match="overwrite"):
            export_to_file(db, output, encrypt=False)
        export_to_file(db, output, encrypt=False, force=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# legacy migration
# ---------------------------------------------------------------------------
def test_legacy_sqlite_migration(tmp_path: Path) -> None:
    source_path = tmp_path / "legacy.sqlite3"
    source = sqlite3.connect(str(source_path))
    try:
        source.executescript(
            """
            CREATE TABLE users (
              user_id INTEGER PRIMARY KEY, username TEXT, name TEXT NOT NULL,
              balance INTEGER NOT NULL DEFAULT 1000, gems INTEGER NOT NULL DEFAULT 0,
              premium_until INTEGER NOT NULL DEFAULT 0,
              protection_until INTEGER NOT NULL DEFAULT 0,
              kills INTEGER NOT NULL DEFAULT 0, xp INTEGER NOT NULL DEFAULT 0,
              streak INTEGER NOT NULL DEFAULT 0, last_daily INTEGER NOT NULL DEFAULT 0,
              dead INTEGER NOT NULL DEFAULT 0, hide_balance INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE chats (
              chat_id INTEGER PRIMARY KEY, title TEXT,
              games_open INTEGER NOT NULL DEFAULT 1, welcome TEXT, goodbye TEXT,
              created_at INTEGER NOT NULL
            );
            """
        )
        stamp = int(time.time())
        source.execute(
            "INSERT INTO users(user_id,username,name,balance,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (1, "Alice", "Alice Secret", 500, stamp, stamp),
        )
        source.execute(
            "INSERT INTO chats(chat_id,title,created_at) VALUES(?,?,?)",
            (10, "Legacy Chat", stamp),
        )
        source.commit()
    finally:
        source.close()

    target = make_db(tmp_path / "target.sqlite3", EncryptionCodec.generate_key())
    try:
        result = migrate_legacy(source_path, target, tmp_path / "backups")
        assert result["source_rows"] == 2
        assert Path(result["legacy_backup"]).is_file()
        assert Path(result["target_backup"]).is_file()
        user = target.execute("SELECT * FROM users WHERE user_id=1").fetchone()
        assert user["username"] == "Alice"
        assert user["name"] == "Alice Secret"
        assert user["username_hash"] == target.username_index("Alice")
        assert target.execute("SELECT title FROM chats WHERE chat_id=10").fetchone()["title"] == "Legacy Chat"
    finally:
        target.close()


# ---------------------------------------------------------------------------
# privacy / retention
# ---------------------------------------------------------------------------
def test_delete_user_removes_related_rows_and_sessions(tmp_path: Path) -> None:
    db = make_db(tmp_path / "zara.sqlite3")
    try:
        insert_user(db, 1, "Alice")
        insert_user(db, 2, "Bob")
        stamp = int(time.time())
        db.execute("INSERT INTO couples(user_id,partner_id,married_at) VALUES(?,?,?)", (1, 2, stamp))
        db.execute("INSERT INTO items(user_id,item,quantity) VALUES(?,?,?)", (1, "rose", 3))
        db.execute("INSERT INTO user_settings(user_id,emoji) VALUES(?,?)", (1, "🌹"))
        db.execute("INSERT INTO approvals(chat_id,user_id,approved_at) VALUES(?,?,?)", (10, 1, stamp))
        db.execute("INSERT INTO warnings(chat_id,user_id,count) VALUES(?,?,?)", (10, 1, 2))
        db.execute("INSERT INTO chat_members(chat_id,user_id,first_seen_at,last_seen_at) VALUES(?,?,?,?)", (10, 1, stamp, stamp))
        db.execute(
            "INSERT INTO game_sessions(chat_id,state,updated_at) VALUES(?,?,?)",
            (10, json.dumps({"host_id": 1, "players": {"1": {}}}), stamp),
        )
        db.execute(
            "INSERT INTO bomb_sessions(chat_id,state,updated_at) VALUES(?,?,?)",
            (11, json.dumps({"host_id": 2, "players": {"1": {}, "2": {}}}), stamp),
        )
        db.commit()

        deleted = delete_user(db, 1)
        assert deleted["users"] == 1
        assert db.execute("SELECT COUNT(*) AS n FROM users WHERE user_id=1").fetchone()["n"] == 0
        assert db.execute("SELECT COUNT(*) AS n FROM couples").fetchone()["n"] == 0
        assert db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"] == 0
        assert db.execute("SELECT COUNT(*) AS n FROM approvals").fetchone()["n"] == 0
        assert db.execute("SELECT COUNT(*) AS n FROM warnings").fetchone()["n"] == 0
        assert db.execute("SELECT COUNT(*) AS n FROM chat_members WHERE user_id=1").fetchone()["n"] == 0
        assert db.execute("SELECT COUNT(*) AS n FROM game_sessions WHERE chat_id=10").fetchone()["n"] == 0
        bomb = json.loads(db.execute("SELECT state FROM bomb_sessions WHERE chat_id=11").fetchone()["state"])
        assert "1" not in bomb["players"]
        assert "2" in bomb["players"]
    finally:
        db.close()


def test_purge_inactive_is_dry_run_by_default(tmp_path: Path) -> None:
    db = make_db(tmp_path / "zara.sqlite3")
    try:
        insert_user(db, 1, "Old", updated_at=0)
        insert_user(db, 2, "Fresh")
        report = purge_inactive(db, 30)
        assert report["dry_run"] is True
        assert report["users"] == [1]
        assert db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 2

        applied = purge_inactive(db, 30, dry_run=False)
        assert applied["dry_run"] is False
        assert applied["deleted_users"] == 1
        assert db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1
        assert db.execute("SELECT username FROM users").fetchone()["username"] == "Fresh"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# configuration and SQL translation
# ---------------------------------------------------------------------------
def test_mysql_url_configuration() -> None:
    config = DatabaseConfig.from_env(
        environ={
            "ZARA_DB_URL": "mysql://user:p%40ss@gateway01.ap-northeast-1.prod.aws.tidbcloud.com:4000/zara?ssl=true",
            "ZARA_DB_SSL": "required",
        }
    )
    assert config.backend == "mysql"
    assert config.host == "gateway01.ap-northeast-1.prod.aws.tidbcloud.com"
    assert config.port == 4000
    assert config.user == "user"
    assert config.password == "p@ss"
    assert config.database == "zara"
    assert config.is_mysql is True


def test_configuration_validation() -> None:
    with pytest.raises(StorageConfigurationError, match="Unsupported"):
        DatabaseConfig.from_env(environ={"ZARA_DB_URL": "postgres://example/db"})
    with pytest.raises(StorageConfigurationError, match="incomplete"):
        DatabaseConfig.from_env(environ={"ZARA_DB_BACKEND": "mysql"})
    with pytest.raises(StorageConfigurationError, match="ZARA_DB_SSL"):
        DatabaseConfig.from_env(
            environ={
                "ZARA_DB_BACKEND": "mysql",
                "ZARA_DB_HOST": "host",
                "ZARA_DB_USER": "user",
                "ZARA_DB_NAME": "zara",
                "ZARA_DB_SSL": "sometimes",
            }
        )


def test_explicit_sqlite_path_wins_over_environment() -> None:
    path = Path("/tmp/zara-explicit.sqlite3")
    config = DatabaseConfig.from_env(
        explicit_sqlite_path=path,
        environ={
            "ZARA_DB_BACKEND": "mysql",
            "ZARA_DB_HOST": "host",
            "ZARA_DB_USER": "user",
            "ZARA_DB_NAME": "zara",
        },
    )
    assert config.backend == "sqlite"
    assert config.sqlite_path == path


def test_sql_translation() -> None:
    sql = (
        "INSERT INTO users(user_id,name) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name"
    )
    assert to_mysql(sql) == (
        "INSERT INTO users(user_id,name) VALUES(%s,%s) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name)"
    )
    assert to_mysql("UPDATE users SET balance=MAX(0,balance+?) WHERE user_id=?") == (
        "UPDATE users SET balance=GREATEST(0,balance+%s) WHERE user_id=%s"
    )
    assert to_mysql("INSERT OR IGNORE INTO users(user_id) VALUES(?)") == (
        "INSERT IGNORE INTO users(user_id) VALUES(%s)"
    )
    assert to_sqlite("SELECT * FROM users WHERE user_id=? FOR UPDATE") == (
        "SELECT * FROM users WHERE user_id=?"
    )
    assert is_read_sql("SELECT 1") is True
    assert is_read_sql("WITH x AS (SELECT 1) SELECT * FROM x") is True
    assert is_read_sql("UPDATE users SET name=?") is False


def test_ssl_ca_material_accepts_pem_and_rejects_garbage(tmp_path: Path) -> None:
    from zara.db.config import ssl_ca_material

    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
    assert ssl_ca_material(pem) == pem
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text(pem)
    assert ssl_ca_material(str(ca_file)) == str(ca_file)
    with pytest.raises(StorageConfigurationError):
        ssl_ca_material("not-a-certificate")
