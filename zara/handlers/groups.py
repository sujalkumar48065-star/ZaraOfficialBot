"""Group settings and moderation configuration commands."""

from .bridge import bind

NAMES = {
    command: "group_settings"
    for command in (
        "rules", "setrules", "clearrules", "welcome", "setwelcome", "resetwelcome",
        "goodbye", "setgoodbye", "resetgoodbye",
        "filter", "filters", "blacklist", "unblacklist", "stop", "stopall",
        "approve", "disapprove", "close", "open",
        "quicksettings", "antilink", "antibio", "antispam", "antiflood",
        "antiforward", "antiinline", "antimedia", "antisticker", "antivoice",
        "antipoll", "antimassmention", "anticaps", "antilong", "antichannel",
        "antibot", "captcha", "setflood", "warnlimit",
        "setwarnaction", "logchannel", "setlog", "unsetlog",
        "report", "reports", "modstats",
    )
}
NAMES.update({
    "warnings": "warnings_command",
    "resetwarns": "reset_warnings",
})


def commands(runtime):
    return bind(runtime, NAMES)