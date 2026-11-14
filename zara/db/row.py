"""A ``sqlite3.Row`` compatible row object for every backend."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


class Row:
    """Immutable row that supports both name and position lookups.

    ``sqlite3.Row`` is used throughout the bot.  The storage adapter returns
    this class instead so MySQL/TiDB rows look identical to the caller:
    ``row["balance"]``, ``row[0]``, ``dict(row)`` and ``row.keys()`` all work.
    """

    __slots__ = ("_columns", "_values", "_index")

    def __init__(self, columns: tuple[str, ...], values: tuple[Any, ...]):
        if len(columns) != len(values):
            raise ValueError("Row column/value length mismatch")
        self._columns = columns
        self._values = values
        self._index = {name: position for position, name in enumerate(columns)}

    def keys(self) -> tuple[str, ...]:
        return self._columns

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        try:
            return self._values[self._index[key]]
        except KeyError as exc:
            raise IndexError(f"No item with key: {key}") from exc

    def __iter__(self) -> Iterator[Any]:
        # sqlite3.Row iterates over values, not keys.
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, key: object) -> bool:
        return key in self._index

    def get(self, key: str, default: Any = None) -> Any:
        position = self._index.get(key)
        return self._values[position] if position is not None else default

    def items(self) -> tuple[tuple[str, Any], ...]:
        return tuple(zip(self._columns, self._values))

    def __repr__(self) -> str:
        pairs = ", ".join(f"{name}={value!r}" for name, value in zip(self._columns, self._values))
        return f"Row({pairs})"
