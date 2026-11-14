"""Safe bridge from the launcher runtime to feature command groups.

The launcher owns the Telegram application and SQLite store. Feature modules
only describe their command family and create thin async delegates, avoiding
the duplicate-module/database problem that importing the launcher would cause.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def bind(
    runtime: Mapping[str, Any],
    names: tuple[str, ...] | Mapping[str, str],
) -> dict[str, Callable]:
    targets = names if isinstance(names, Mapping) else {name: name for name in names}
    commands: dict[str, Callable] = {}
    for name, target_name in targets.items():
        target = runtime[target_name]

        async def delegate(update: Any, context: Any, _target=target) -> Any:
            return await _target(update, context)

        delegate.__name__ = name
        commands[name] = delegate
    return commands