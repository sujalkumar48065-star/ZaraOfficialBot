"""Custom emoji utility commands."""

from .bridge import bind

NAMES = {"setemoji": "utility_command"}


def commands(runtime):
    return bind(runtime, NAMES)