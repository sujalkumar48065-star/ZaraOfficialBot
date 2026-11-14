"""Single command registry assembled from readable feature modules."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from .handlers import (
    actions,
    admin,
    core,
    economy,
    games,
    groups,
    owner,
    tools,
    utilities,
)

GROUPS = (core, economy, games, actions, groups, utilities, tools, owner)


def build_commands(runtime: Mapping[str, Any]) -> dict[str, Callable]:
    result: dict[str, Callable] = {}
    for group in GROUPS:
        overlap = result.keys() & set(group.NAMES)
        if overlap:
            raise RuntimeError(f"Duplicate Zara command registration: {sorted(overlap)}")
        result.update(group.commands(runtime))
    return result


def build_admin_commands(runtime: Mapping[str, Any]) -> dict[str, Callable]:
    return admin.commands(runtime)