"""Owner-only command group."""

from .bridge import bind

NAMES = {
    "m": "owner_center", "mstats": "owner_stats",
    "maddmoney": "owner_grant", "maddgems": "owner_grant",
    "mpremium": "owner_grant", "mbroadcast": "broadcast",
}


def commands(runtime):
    return bind(runtime, NAMES)