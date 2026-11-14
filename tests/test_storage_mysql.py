"""Opt-in TiDB/MySQL integration test.

Run against a dedicated test database only:

    ZARA_TEST_MYSQL_URL='mysql://user:pass@gateway01...:4000/zara_test?ssl=true' \
    ZARA_DB_SSL_CA=/path/to/ca.pem \
    python -m pytest -q tests/test_storage_mysql.py

The test creates the canonical schema if needed, uses one temporary user row,
and deletes it afterwards. It never deletes unrelated users or tables.
"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path

import pytest

from zara.db import DatabaseConfig, EncryptionCodec, create_database
from zara.db.maintenance import delete_user

MYSQL_URL = os.getenv("ZARA_TEST_MYSQL_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not MYSQL_URL,
    reason="set ZARA_TEST_MYSQL_URL to run the TiDB/MySQL integration test",
)


def test_mysql_health_encryption_and_transaction_rollback() -> None:
    key = EncryptionCodec.generate_key()
    config = DatabaseConfig.from_env(
        environ={
            "ZARA_DB_URL": MYSQL_URL,
            "ZARA_ENCRYPTION_KEY": key,
            "ZARA_DB_SSL": os.getenv("ZARA_DB_SSL", "required"),
            "ZARA_DB_SSL_CA": os.getenv("ZARA_DB_SSL_CA", ""),
        }
    )
    assert config.is_mysql is True
    db = create_database(config, initialize=True)
    user_id = 9_000_000_000_000_000 + random.randint(1, 999_999_999)
    stamp = int(time.time())
    try:
        health = db.health_check()
        assert health["backend"] == "mysql"
        assert health["schema_version"] == "3"
        assert health["tables"] >= 16

        # A failed transaction must not leave the user row behind.
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.execute(
                    "INSERT INTO users(user_id,username,username_hash,name,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (user_id, "mysql_test", db.username_index("mysql_test"), "MySQL Test", stamp, stamp),
                )
                raise RuntimeError("rollback")
        assert db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone() is None

        # A committed row must decrypt correctly and use the blind index.
        db.execute(
            "INSERT INTO users(user_id,username,username_hash,name,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, "mysql_test", db.username_index("@MYSQL_TEST"), "MySQL Test", stamp, stamp),
        )
        db.commit()
        row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        assert row is not None
        assert row["username"] == "mysql_test"
        assert row["name"] == "MySQL Test"
        assert row["username_hash"] == db.username_index("mysql_test")
        found = db.execute(
            "SELECT * FROM users WHERE username_hash=?",
            (db.username_index("MYSQL_TEST"),),
        ).fetchone()
        assert found is not None and int(found["user_id"]) == user_id
    finally:
        try:
            delete_user(db, user_id)
        finally:
            db.close()
