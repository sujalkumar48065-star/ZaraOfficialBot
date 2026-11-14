"""Core navigation and identity commands."""

from .bridge import bind

NAMES = {
    "start": "start",
    "help": "help_command",
    "game": "game_menu",
    "id": "id_command",
    "info": "user_info",
    "verify": "verify",
}


def commands(runtime):
    return bind(runtime, NAMES)