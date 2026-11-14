"""Economy, profile, premium, and relationship commands."""

from .bridge import bind

NAMES = {
    "economy": "economy_menu", "bal": "balance", "balance": "balance",
    "profile": "profile", "daily": "daily", "leaderboard": "leaderboard",
    "toprich": "toprich", "topkill": "topkill", "leaders": "game_leaders",
    "give": "give", "rob": "rob", "kill": "kill", "revive": "revive",
    "protect": "protect", "checkprotect": "checkprotect", "check": "checkprotect",
    "hide": "hide_balance", "unhide": "unhide_balance", "premium": "premium",
    "fpay": "fpay", "fgems": "fgems", "pay": "premium", "marry": "relationship",
    "divorce": "relationship", "couple": "relationship", "items": "item_command",
    "item": "item_command", "gift": "item_command", "rank": "item_command",
    "pfp": "pfp",
}


def commands(runtime):
    return bind(runtime, NAMES)