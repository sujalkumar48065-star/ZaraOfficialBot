"""Group moderation commands requiring administrator permissions."""

from .bridge import bind

NAMES = {
    command: "admin_command"
    for command in (
        "ban", "unban", "kick", "mute", "unmute", "tmute", "tban", "warn",
        "promote", "demote", "pin", "unpin", "purge", "del", "lock", "unlock",
        "admins",
    )
}


def commands(runtime):
    return bind(runtime, NAMES)