"""Canonical schema for both SQLite and TiDB/MySQL.

The bot always speaks SQLite-flavoured SQL.  The MySQL backend translates it,
but DDL is provided explicitly because MySQL needs bounded ``VARCHAR`` primary
keys and different column types for encrypted text.
"""

from __future__ import annotations

SCHEMA_VERSION = 3

TABLE_ORDER: tuple[str, ...] = (
    "users",
    "chats",
    "chat_members",
    "game_sessions",
    "hack_sessions",
    "bomb_sessions",
    "blacklists",
    "filters",
    "action_counters",
    "warnings",
    "couples",
    "items",
    "approvals",
    "user_settings",
    "storage_meta",
    "schema_migrations",
)

# Tables that hold data created by real users.  Backups/imports include all
# tables, but integrity reports call these out separately.
DATA_TABLES: tuple[str, ...] = tuple(
    table for table in TABLE_ORDER if table not in {"storage_meta", "schema_migrations"}
)

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "users": ("user_id",),
    "chats": ("chat_id",),
    "chat_members": ("chat_id", "user_id"),
    "game_sessions": ("chat_id",),
    "hack_sessions": ("chat_id",),
    "bomb_sessions": ("chat_id",),
    "blacklists": ("chat_id", "word"),
    "filters": ("chat_id", "word"),
    "action_counters": ("action",),
    "warnings": ("chat_id", "user_id"),
    "couples": ("user_id",),
    "items": ("user_id", "item"),
    "approvals": ("chat_id", "user_id"),
    "user_settings": ("user_id",),
    "storage_meta": ("meta_key",),
    "schema_migrations": ("version",),
}

SENSITIVE_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": ("username", "name"),
    "chats": ("title", "welcome", "goodbye", "rules"),
    "filters": ("response",),
    "game_sessions": ("state",),
    "hack_sessions": ("state",),
    "bomb_sessions": ("state",),
}

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": (
        "user_id",
        "username",
        "username_hash",
        "name",
        "balance",
        "gems",
        "premium_until",
        "protection_until",
        "verified_at",
        "kills",
        "xp",
        "streak",
        "last_daily",
        "dead",
        "hide_balance",
        "created_at",
        "updated_at",
    ),
    "chats": ("chat_id", "title", "games_open", "welcome", "goodbye", "rules", "moderation", "created_at"),
    "chat_members": ("chat_id", "user_id", "first_seen_at", "last_seen_at"),
    "game_sessions": ("chat_id", "state", "updated_at"),
    "hack_sessions": ("chat_id", "state", "updated_at"),
    "bomb_sessions": ("chat_id", "state", "updated_at"),
    "blacklists": ("chat_id", "word"),
    "filters": ("chat_id", "word", "response"),
    "action_counters": ("action", "count"),
    "warnings": ("chat_id", "user_id", "count"),
    "couples": ("user_id", "partner_id", "married_at"),
    "items": ("user_id", "item", "quantity"),
    "approvals": ("chat_id", "user_id", "approved_at"),
    "user_settings": ("user_id", "emoji"),
    "storage_meta": ("meta_key", "meta_value", "updated_at"),
    "schema_migrations": ("version", "applied_at"),
}

# Columns added after the original SQLite schema.  ``initialize`` adds these
# with ALTER TABLE so an existing database is upgraded without data loss.
COLUMN_MIGRATIONS: tuple[tuple[str, str, str, str], ...] = (
    ("chats", "rules", "TEXT", "LONGTEXT NULL"),
    ("chats", "moderation", "TEXT", "LONGTEXT NULL"),
    ("users", "verified_at", "INTEGER NOT NULL DEFAULT 0", "BIGINT NOT NULL DEFAULT 0"),
    ("users", "username_hash", "TEXT", "VARCHAR(64) NULL"),
)

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, "base tables and indexes"),
    (2, "verified_at, rules, hack_sessions, chat_members"),
    (3, "username_hash, bomb_sessions, storage_meta, schema_migrations"),
)

SQLITE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
      user_id INTEGER PRIMARY KEY,
      username TEXT,
      username_hash TEXT,
      name TEXT NOT NULL,
      balance INTEGER NOT NULL DEFAULT 1000,
      gems INTEGER NOT NULL DEFAULT 0,
      premium_until INTEGER NOT NULL DEFAULT 0,
      protection_until INTEGER NOT NULL DEFAULT 0,
      verified_at INTEGER NOT NULL DEFAULT 0,
      kills INTEGER NOT NULL DEFAULT 0,
      xp INTEGER NOT NULL DEFAULT 0,
      streak INTEGER NOT NULL DEFAULT 0,
      last_daily INTEGER NOT NULL DEFAULT 0,
      dead INTEGER NOT NULL DEFAULT 0,
      hide_balance INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chats (
      chat_id INTEGER PRIMARY KEY,
      title TEXT,
      games_open INTEGER NOT NULL DEFAULT 1,
      welcome TEXT,
      goodbye TEXT,
      rules TEXT,
      moderation TEXT,
      created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_members (
      chat_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      first_seen_at INTEGER NOT NULL,
      last_seen_at INTEGER NOT NULL,
      PRIMARY KEY(chat_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS game_sessions (
      chat_id INTEGER PRIMARY KEY,
      state TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hack_sessions (
      chat_id INTEGER PRIMARY KEY,
      state TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bomb_sessions (
      chat_id INTEGER PRIMARY KEY,
      state TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS blacklists (
      chat_id INTEGER NOT NULL,
      word TEXT NOT NULL,
      PRIMARY KEY(chat_id, word)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS filters (
      chat_id INTEGER NOT NULL,
      word TEXT NOT NULL,
      response TEXT NOT NULL,
      PRIMARY KEY(chat_id, word)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS action_counters (
      action TEXT PRIMARY KEY,
      count INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS warnings (
      chat_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      count INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(chat_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS couples (
      user_id INTEGER PRIMARY KEY,
      partner_id INTEGER NOT NULL,
      married_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS items (
      user_id INTEGER NOT NULL,
      item TEXT NOT NULL,
      quantity INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(user_id, item)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS approvals (
      chat_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      approved_at INTEGER NOT NULL,
      PRIMARY KEY(chat_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_settings (
      user_id INTEGER PRIMARY KEY,
      emoji TEXT NOT NULL DEFAULT '💗'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS storage_meta (
      meta_key TEXT PRIMARY KEY,
      meta_value TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      applied_at INTEGER NOT NULL
    )
    """,
)

SQLITE_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS users_username_idx ON users(username)",
    "CREATE INDEX IF NOT EXISTS users_username_hash_idx ON users(username_hash)",
    "CREATE INDEX IF NOT EXISTS users_updated_at_idx ON users(updated_at)",
    "CREATE INDEX IF NOT EXISTS chat_members_chat_idx ON chat_members(chat_id)",
)

MYSQL_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
      user_id BIGINT NOT NULL PRIMARY KEY,
      username VARCHAR(255) NULL,
      username_hash CHAR(64) NULL,
      name TEXT NOT NULL,
      balance BIGINT NOT NULL DEFAULT 1000,
      gems BIGINT NOT NULL DEFAULT 0,
      premium_until BIGINT NOT NULL DEFAULT 0,
      protection_until BIGINT NOT NULL DEFAULT 0,
      verified_at BIGINT NOT NULL DEFAULT 0,
      kills BIGINT NOT NULL DEFAULT 0,
      xp BIGINT NOT NULL DEFAULT 0,
      streak BIGINT NOT NULL DEFAULT 0,
      last_daily BIGINT NOT NULL DEFAULT 0,
      dead BIGINT NOT NULL DEFAULT 0,
      hide_balance BIGINT NOT NULL DEFAULT 0,
      created_at BIGINT NOT NULL,
      updated_at BIGINT NOT NULL,
      KEY users_username_hash_idx (username_hash),
      KEY users_updated_at_idx (updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS chats (
      chat_id BIGINT NOT NULL PRIMARY KEY,
      title TEXT NULL,
      games_open BIGINT NOT NULL DEFAULT 1,
      welcome LONGTEXT NULL,
      goodbye LONGTEXT NULL,
      rules LONGTEXT NULL,
      moderation LONGTEXT NULL,
      created_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_members (
      chat_id BIGINT NOT NULL,
      user_id BIGINT NOT NULL,
      first_seen_at BIGINT NOT NULL,
      last_seen_at BIGINT NOT NULL,
      PRIMARY KEY(chat_id, user_id),
      KEY chat_members_chat_idx (chat_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS game_sessions (
      chat_id BIGINT NOT NULL PRIMARY KEY,
      state LONGTEXT NOT NULL,
      updated_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS hack_sessions (
      chat_id BIGINT NOT NULL PRIMARY KEY,
      state LONGTEXT NOT NULL,
      updated_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS bomb_sessions (
      chat_id BIGINT NOT NULL PRIMARY KEY,
      state LONGTEXT NOT NULL,
      updated_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS blacklists (
      chat_id BIGINT NOT NULL,
      word VARCHAR(191) NOT NULL,
      PRIMARY KEY(chat_id, word)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS filters (
      chat_id BIGINT NOT NULL,
      word VARCHAR(191) NOT NULL,
      response LONGTEXT NOT NULL,
      PRIMARY KEY(chat_id, word)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS action_counters (
      action VARCHAR(64) NOT NULL PRIMARY KEY,
      count BIGINT NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS warnings (
      chat_id BIGINT NOT NULL,
      user_id BIGINT NOT NULL,
      count BIGINT NOT NULL DEFAULT 0,
      PRIMARY KEY(chat_id, user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS couples (
      user_id BIGINT NOT NULL PRIMARY KEY,
      partner_id BIGINT NOT NULL,
      married_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS items (
      user_id BIGINT NOT NULL,
      item VARCHAR(64) NOT NULL,
      quantity BIGINT NOT NULL DEFAULT 0,
      PRIMARY KEY(user_id, item)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS approvals (
      chat_id BIGINT NOT NULL,
      user_id BIGINT NOT NULL,
      approved_at BIGINT NOT NULL,
      PRIMARY KEY(chat_id, user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS user_settings (
      user_id BIGINT NOT NULL PRIMARY KEY,
      emoji VARCHAR(32) NOT NULL DEFAULT '💗'
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS storage_meta (
      meta_key VARCHAR(64) NOT NULL PRIMARY KEY,
      meta_value LONGTEXT NOT NULL,
      updated_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version BIGINT NOT NULL PRIMARY KEY,
      applied_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
    """,
)

MYSQL_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("users_username_hash_idx", "users", "username_hash"),
    ("users_updated_at_idx", "users", "updated_at"),
    ("chat_members_chat_idx", "chat_members", "chat_id"),
)


def primary_key(table: str) -> tuple[str, ...]:
    return PRIMARY_KEYS[table]


def quoted_columns(table: str) -> str:
    return ", ".join(f"`{column}`" for column in REQUIRED_COLUMNS[table])


def mysql_column_type(column: str) -> str:
    """Only used by re-encryption/aggregate helpers in tests."""
    if column in {"username"}:
        return "VARCHAR(255)"
    if column in {"name", "title", "welcome", "goodbye", "rules", "moderation", "response", "state"}:
        return "LONGTEXT"
    return "BIGINT"
