"""Group moderation settings, menu metadata, and template helpers."""

from __future__ import annotations

import json
from typing import Any

DEFAULT_WELCOME = "ʜ𝙴𝙴𝙸𝙸 {mention},\nWelcome in {group_name}"

DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
QUICK_SETTINGS_PAGE_SIZE = 6
_SMALL_CAPS = str.maketrans(
    "abcdefghijklmnopqrstuvwxyz",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ",
)
_STYLED_DIGITS = str.maketrans("0123456789", "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿")


def styled(value: object) -> str:
    """Match the small-caps typography used across Zara's existing menus."""
    return str(value).lower().translate(_SMALL_CAPS).translate(_STYLED_DIGITS)


def quick_settings_page_count() -> int:
    return max(
        1,
        (len(TOGGLE_LABELS) + QUICK_SETTINGS_PAGE_SIZE - 1)
        // QUICK_SETTINGS_PAGE_SIZE,
    )


def quick_settings_page_for_key(key: str) -> int:
    for index, (item_key, _) in enumerate(TOGGLE_LABELS):
        if item_key == key:
            return index // QUICK_SETTINGS_PAGE_SIZE
    return 0


def quick_settings_page_items(page: int) -> list[tuple[str, str]]:
    page = max(0, min(page, quick_settings_page_count() - 1))
    start = page * QUICK_SETTINGS_PAGE_SIZE
    return list(TOGGLE_LABELS[start : start + QUICK_SETTINGS_PAGE_SIZE])

PENALTY_CYCLE: tuple[str, ...] = ("ban", "mute", "nothing")
PENALTY_LABELS = {
    "ban": "Ban",
    "mute": "Mute",
    "nothing": "Nothing",
}
DEFAULT_PENALTIES: dict[str, str] = {
    "antilink": "nothing",
    "antibio": "nothing",
    "antispam": "mute",
    "antiflood": "mute",
    "antiforward": "nothing",
    "antiinline": "nothing",
    "antimedia": "nothing",
    "antisticker": "nothing",
    "antivoice": "nothing",
    "antipoll": "nothing",
    "antimassmention": "nothing",
    "anticaps": "nothing",
    "antilong": "nothing",
    "antichannel": "nothing",
}
CONFIG_KEYS = frozenset(DEFAULT_PENALTIES)

DEFAULT_SETTINGS: dict[str, Any] = {
    "welcome_enabled": True,
    "goodbye_enabled": False,
    "antilink": False,
    "antibio": False,
    "antispam": False,
    "antiflood": False,
    "antiforward": False,
    "antiinline": False,
    "antimedia": False,
    "antisticker": False,
    "antivoice": False,
    "antipoll": False,
    "antimassmention": False,
    "anticaps": False,
    "antilong": False,
    "antichannel": False,
    "antibot": False,
    "captcha": False,
    "penalties": dict(DEFAULT_PENALTIES),
    "flood_limit": 5,
    "warn_limit": 3,
    "warn_action": "mute",
    "log_channel": 0,
    "log_enabled": True,
    "reports": False,
}

TOGGLE_LABELS: tuple[tuple[str, str], ...] = (
    ("welcome_enabled", "Welcome"),
    ("goodbye_enabled", "Goodbye"),
    ("antilink", "Anti-link"),
    ("antibio", "Anti-bio"),
    ("antispam", "Anti-spam"),
    ("antiflood", "Anti-flood"),
    ("antiforward", "Anti-forward"),
    ("antiinline", "Anti-inline"),
    ("antimedia", "Anti-media"),
    ("antisticker", "Anti-sticker/gif"),
    ("antivoice", "Anti-voice/doc"),
    ("antipoll", "Anti-poll/location"),
    ("antimassmention", "Anti-massmention"),
    ("anticaps", "Anti-caps"),
    ("antilong", "Anti-long"),
    ("antichannel", "Anti-channel"),
    ("antibot", "Anti-bot"),
    ("captcha", "Captcha"),
)

PROTECTION_LABELS = {
    "antilink": "Anti-link",
    "antibio": "Anti-bio",
    "antispam": "Anti-spam",
    "antiflood": "Anti-flood",
    "antiforward": "Anti-forward",
    "antiinline": "Anti-inline",
    "antimedia": "Anti-media",
    "antisticker": "Anti-sticker/gif",
    "antivoice": "Anti-voice/doc",
    "antipoll": "Anti-poll/location",
    "antimassmention": "Anti-massmention",
    "anticaps": "Anti-caps",
    "antilong": "Anti-long",
    "antichannel": "Anti-channel",
    "antibot": "Anti-bot",
    "captcha": "Captcha",
}

CATEGORIES: dict[str, dict[str, Any]] = {
    "welcome": {
        "title": "Welcome & Goodbye",
        "rows": [
            ("welcome", "show the active welcome message"),
            ("setwelcome", "set a custom welcome message"),
            ("resetwelcome", "restore the default welcome"),
            ("goodbye", "show the goodbye message"),
            ("setgoodbye", "set a custom goodbye message"),
            ("resetgoodbye", "turn goodbye off"),
        ],
        "note": (
            "Variables: {mention} {name} {first_name} {last_name} "
            "{username} {id} {group_name} {group_username} {group_id}"
        ),
    },
    "rules": {
        "title": "Rules & Filters",
        "rows": [
            ("rules", "show group rules"),
            ("setrules", "set group rules"),
            ("clearrules", "clear group rules"),
            ("filter", "add an auto-reply filter"),
            ("filters", "list group filters"),
            ("stop", "remove one filter"),
            ("stopall", "remove all filters"),
            ("blacklist", "block an abusive word"),
            ("unblacklist", "remove a blocked word"),
        ],
        "note": "Filters use /filter word|reply.",
    },
    "protection": {
        "title": "Abuse & Link Protection",
        "rows": [
            ("antilink", "block links in chat"),
            ("antibio", "block link-filled new profiles"),
            ("antispam", "block repeated spam"),
            ("antiflood", "block message flooding"),
            ("setflood", "set the flood message limit"),
            ("antiforward", "block forwarded messages"),
            ("antiinline", "block inline button messages"),
            ("antimedia", "block photo/video messages"),
            ("antisticker", "block stickers and GIFs"),
            ("antivoice", "block voice/audio/document"),
            ("antipoll", "block polls/location/contact"),
            ("antimassmention", "block mass mentions"),
            ("anticaps", "block all-caps messages"),
            ("antilong", "block very long messages"),
            ("antichannel", "block channel messages"),
            ("antibot", "remove bots that join"),
            ("captcha", "verify new members"),
        ],
        "note": "Use /command on, /command off, or /command config.",
    },
    "punish": {
        "title": "Warn & Punish",
        "rows": [
            ("warn", "warn a user"),
            ("warnings", "show a user's warnings"),
            ("resetwarns", "reset a user's warnings"),
            ("warnlimit", "set the warning limit"),
            ("setwarnaction", "set the limit action"),
            ("mute", "mute a user"),
            ("tmute", "temporarily mute a user"),
            ("unmute", "unmute a user"),
            ("ban", "ban a user"),
            ("tban", "temporarily ban a user"),
            ("unban", "unban a user"),
            ("kick", "kick a user"),
            ("purge", "delete messages up to a reply"),
            ("del", "delete the replied message"),
        ],
        "note": "Reply to a user or pass their @username/ID.",
    },
    "admin": {
        "title": "Admin Tools",
        "rows": [
            ("admins", "list group admins"),
            ("promote", "promote a user"),
            ("demote", "demote a user"),
            ("pin", "pin the replied message"),
            ("unpin", "unpin the pinned message"),
            ("approve", "approve a user"),
            ("disapprove", "remove approval"),
            ("close", "close economy games"),
            ("open", "open economy games"),
            ("lock", "lock the group"),
            ("unlock", "unlock the group"),
        ],
        "note": "Bot needs Telegram admin rights.",
    },
    "logs": {
        "title": "Logs & Reports",
        "rows": [
            ("logchannel", "set the moderation log channel"),
            ("setlog", "enable moderation logs"),
            ("unsetlog", "disable moderation logs"),
            ("report", "report a message to admins"),
            ("reports", "show report settings"),
            ("modstats", "show group moderation stats"),
        ],
        "note": "Log channel must be a channel where Zara is admin.",
    },
}


def load_settings(row: Any) -> dict[str, Any]:
    """Return persisted moderation settings merged with safe defaults."""
    raw = ""
    if row is not None:
        try:
            raw = row["moderation"] or ""
        except (IndexError, KeyError, TypeError):
            raw = ""
    settings = dict(DEFAULT_SETTINGS)
    # The penalties mapping is nested; never let one chat mutate the module
    # defaults or another chat's settings.
    settings["penalties"] = dict(DEFAULT_PENALTIES)
    if raw:
        try:
            stored = json.loads(raw)
        except (TypeError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key not in settings:
                    continue
                if key == "penalties" and isinstance(value, dict):
                    penalties = dict(DEFAULT_PENALTIES)
                    for feature, penalty in value.items():
                        if feature in penalties and penalty in PENALTY_CYCLE:
                            penalties[feature] = penalty
                    settings[key] = penalties
                else:
                    settings[key] = value
    return settings


def save_settings(store: Any, chat_id: int, settings: dict[str, Any]) -> None:
    """Persist only known moderation settings for one chat."""
    clean = {
        key: settings[key]
        for key in DEFAULT_SETTINGS
        if key in settings
    }
    store.db.execute(
        "UPDATE chats SET moderation=? WHERE chat_id=?",
        (json.dumps(clean, ensure_ascii=False), chat_id),
    )
    store.db.commit()


def toggle_setting(settings: dict[str, Any], key: str) -> bool:
    if key not in settings or not isinstance(settings[key], bool):
        raise KeyError(key)
    settings[key] = not settings[key]
    return bool(settings[key])


def penalty_for(settings: dict[str, Any], key: str) -> str:
    penalties = settings.get("penalties")
    if not isinstance(penalties, dict):
        return DEFAULT_PENALTIES.get(key, "nothing")
    value = penalties.get(key, DEFAULT_PENALTIES.get(key, "nothing"))
    return value if value in PENALTY_CYCLE else "nothing"


def set_penalty(settings: dict[str, Any], key: str, penalty: str) -> str:
    if key not in CONFIG_KEYS:
        raise KeyError(key)
    if penalty not in PENALTY_CYCLE:
        raise ValueError(penalty)
    penalties = settings.setdefault("penalties", dict(DEFAULT_PENALTIES))
    if not isinstance(penalties, dict):
        penalties = dict(DEFAULT_PENALTIES)
        settings["penalties"] = penalties
    penalties[key] = penalty
    return penalty


def cycle_penalty(settings: dict[str, Any], key: str) -> str:
    current = penalty_for(settings, key)
    index = PENALTY_CYCLE.index(current)
    return set_penalty(settings, key, PENALTY_CYCLE[(index + 1) % len(PENALTY_CYCLE)])


def protection_status_text(key: str, settings: dict[str, Any]) -> str:
    label = PROTECTION_LABELS.get(key, key)
    enabled = bool(settings.get(key))
    lines = [
        f"🛡️ <b>{styled(label)}</b>",
        DIVIDER,
        "<blockquote>",
        f"▫️ {styled('status')}: {'✅ ' + styled('on') if enabled else '❌ ' + styled('off')}",
    ]
    if key in CONFIG_KEYS:
        lines.append(
            f"▫️ {styled('penalty')}: ⚙️ "
            f"{styled(PENALTY_LABELS[penalty_for(settings, key)])}"
        )
    lines.append("</blockquote>")
    lines.append("<blockquote>")
    lines.append(f"▫️ {styled('tap status to toggle on/off')}")
    if key in CONFIG_KEYS:
        lines.append(f"▫️ {styled('tap penalty to change action')}")
    lines.append("</blockquote>")
    return "\n".join(lines)


def quick_settings_text(settings: dict[str, Any], page: int = 0) -> str:
    page = max(0, min(page, quick_settings_page_count() - 1))
    lines = [
        f"⚙️ <b>{styled('quick settings')}</b>",
        DIVIDER,
        "<blockquote>",
        f"▫️ {styled('page')}: <b>{styled(f'{page + 1}/{quick_settings_page_count()}')}</b>",
        f"▫️ {styled('flood limit')}: <b>{styled(int(settings.get('flood_limit', 5)))}</b>"
        f" • {styled('warn limit')}: <b>{styled(int(settings.get('warn_limit', 3)))}</b>",
        f"▫️ {styled('warn action')}: <b>{styled(settings.get('warn_action', 'mute'))}</b>",
        "</blockquote>",
        "<blockquote>"
        f"🏷️ {styled('feature')} • ✅ {styled('status')} • ⚙️ {styled('penalty')}\n"
        f"▫️ {styled('tap status to toggle')}\n"
        f"▫️ {styled('tap penalty to change action')}"
        "</blockquote>",
    ]
    return "\n".join(lines)
