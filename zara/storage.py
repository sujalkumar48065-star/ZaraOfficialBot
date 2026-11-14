"""SQLite persistence for users, economy, chats, games, and moderation."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def now() -> int:
    return int(time.time())


class Store:
    """Small SQLite repository used by all command groups."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              user_id INTEGER PRIMARY KEY, username TEXT, name TEXT NOT NULL,
              balance INTEGER NOT NULL DEFAULT 1000, gems INTEGER NOT NULL DEFAULT 0,
              premium_until INTEGER NOT NULL DEFAULT 0, protection_until INTEGER NOT NULL DEFAULT 0,
              kills INTEGER NOT NULL DEFAULT 0, xp INTEGER NOT NULL DEFAULT 0,
              streak INTEGER NOT NULL DEFAULT 0, last_daily INTEGER NOT NULL DEFAULT 0,
              dead INTEGER NOT NULL DEFAULT 0, hide_balance INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS users_username_idx ON users(username);
            CREATE TABLE IF NOT EXISTS chats (
              chat_id INTEGER PRIMARY KEY, title TEXT, games_open INTEGER NOT NULL DEFAULT 1,
              welcome TEXT, goodbye TEXT, rules TEXT, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS game_sessions (
              chat_id INTEGER PRIMARY KEY, state TEXT NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS blacklists (
              chat_id INTEGER NOT NULL, word TEXT NOT NULL, PRIMARY KEY(chat_id, word)
            );
            CREATE TABLE IF NOT EXISTS filters (
              chat_id INTEGER NOT NULL, word TEXT NOT NULL, response TEXT NOT NULL,
              PRIMARY KEY(chat_id, word)
            );
            CREATE TABLE IF NOT EXISTS action_counters (
              action TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS warnings (
              chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS couples (
              user_id INTEGER PRIMARY KEY, partner_id INTEGER NOT NULL, married_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS items (
              user_id INTEGER NOT NULL, item TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(user_id, item)
            );
            CREATE TABLE IF NOT EXISTS approvals (
              chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, approved_at INTEGER NOT NULL,
              PRIMARY KEY(chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS user_settings (
              user_id INTEGER PRIMARY KEY, emoji TEXT NOT NULL DEFAULT '💗'
            );
            """
        )
        self.db.commit()

    def user(self, tg_user: Any) -> sqlite3.Row:
        stamp = now()
        self.db.execute(
            """INSERT INTO users(user_id,username,name,created_at,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
               name=excluded.name, updated_at=excluded.updated_at""",
            (tg_user.id, tg_user.username, tg_user.full_name or "Telegram user", stamp, stamp),
        )
        self.db.commit()
        return self.db.execute(
            "SELECT * FROM users WHERE user_id=?", (tg_user.id,)
        ).fetchone()

    def by_ref(self, ref: str) -> sqlite3.Row | None:
        ref = ref.strip().lstrip("@")
        if ref.isdigit():
            return self.db.execute(
                "SELECT * FROM users WHERE user_id=?", (int(ref),)
            ).fetchone()
        return self.db.execute(
            "SELECT * FROM users WHERE lower(username)=lower(?)", (ref,)
        ).fetchone()

    def add_money(self, user_id: int, amount: int) -> None:
        self.db.execute(
            "UPDATE users SET balance=MAX(0,balance+?),updated_at=? WHERE user_id=?",
            (amount, now(), user_id),
        )
        self.db.commit()

    def add_gems(self, user_id: int, amount: int) -> None:
        self.db.execute(
            "UPDATE users SET gems=MAX(0,gems+?),updated_at=? WHERE user_id=?",
            (amount, now(), user_id),
        )
        self.db.commit()

    def update(self, user_id: int, **values: Any) -> None:
        if not values:
            return
        values["updated_at"] = now()
        keys = ", ".join(f"{key}=?" for key in values)
        self.db.execute(
            f"UPDATE users SET {keys} WHERE user_id=?",
            (*values.values(), user_id),
        )
        self.db.commit()

    def chat(self, chat_id: int, title: str = "") -> sqlite3.Row:
        self.db.execute(
            """INSERT INTO chats(chat_id,title,created_at) VALUES(?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title""",
            (chat_id, title, now()),
        )
        self.db.commit()
        return self.db.execute(
            "SELECT * FROM chats WHERE chat_id=?", (chat_id,)
        ).fetchone()

    def game(self, chat_id: int) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT state FROM game_sessions WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return json.loads(row["state"]) if row else None

    def save_game(self, chat_id: int, state: dict[str, Any] | None) -> None:
        if state is None:
            self.db.execute("DELETE FROM game_sessions WHERE chat_id=?", (chat_id,))
        else:
            self.db.execute(
                """INSERT INTO game_sessions(chat_id,state,updated_at) VALUES(?,?,?)
                   ON CONFLICT(chat_id) DO UPDATE SET state=excluded.state,
                   updated_at=excluded.updated_at""",
                (chat_id, json.dumps(state), now()),
            )
        self.db.commit()