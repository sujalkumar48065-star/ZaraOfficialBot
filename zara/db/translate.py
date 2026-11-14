"""Translate the bot's SQLite SQL dialect to TiDB/MySQL.

The bot historically used SQLite.  Keeping SQLite SQL in the handlers avoids a
risky rewrite; this module performs the small, well-understood set of
translations required by MySQL/TiDB:

* ``?`` placeholders become ``%s``
* ``INSERT OR REPLACE`` becomes ``REPLACE``
* ``INSERT OR IGNORE`` becomes ``INSERT IGNORE``
* ``ON CONFLICT (...) DO UPDATE SET ...`` becomes ``ON DUPLICATE KEY UPDATE ...``
* ``excluded.col`` becomes ``VALUES(col)``
* scalar ``MAX(a, b)`` becomes ``GREATEST(a, b)``
"""

from __future__ import annotations

import re

_ON_CONFLICT = re.compile(
    r"(?is)\bON\s+CONFLICT\s*(?:\(([^)]*)\))?\s+DO\s+(UPDATE\s+SET\s+(.+)|NOTHING)\s*$"
)
_EXCLUDED = re.compile(r"\bexcluded\.([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_SCALAR_MAX = re.compile(r"\bMAX\s*\(", re.IGNORECASE)
_FOR_UPDATE = re.compile(r"\s+FOR\s+UPDATE\s*$", re.IGNORECASE)


def to_mysql(sql: str) -> str:
    translated = sql
    translated = re.sub(r"\bINSERT\s+OR\s+REPLACE\b", "REPLACE", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT IGNORE", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bINSERT\s+OR\s+ABORT\b", "INSERT", translated, flags=re.IGNORECASE)
    translated = _rewrite_conflict(translated)
    # Only scalar MAX() calls exist in the runtime SQL.  Aggregate MAX() is not
    # used, so replacing it unconditionally is safe and avoids MySQL treating
    # MAX(0, balance + ?) as an invalid aggregate.
    translated = _SCALAR_MAX.sub("GREATEST(", translated)
    translated = translated.replace("?", "%s")
    return translated


def to_sqlite(sql: str) -> str:
    # SQLite cannot use SELECT ... FOR UPDATE.  Writers are serialized by
    # BEGIN IMMEDIATE in the SQLite backend, so dropping the suffix is safe.
    return _FOR_UPDATE.sub("", sql)


def _rewrite_conflict(sql: str) -> str:
    match = _ON_CONFLICT.search(sql)
    if not match:
        return sql
    action = (match.group(2) or "").strip()
    if action.upper() == "NOTHING":
        prefix = sql[: match.start()]
        prefix = re.sub(r"^(\s*)INSERT\s+", r"\1INSERT IGNORE ", prefix, count=1, flags=re.IGNORECASE)
        return prefix.rstrip()
    set_clause = action[len("UPDATE SET ") :]
    set_clause = _EXCLUDED.sub(r"VALUES(\1)", set_clause)
    return sql[: match.start()] + "ON DUPLICATE KEY UPDATE " + set_clause.rstrip()


def is_read_sql(sql: str) -> bool:
    stripped = sql.lstrip().lower()
    return stripped.startswith(("select", "show", "describe", "pragma", "with"))
