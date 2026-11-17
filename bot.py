from __future__ import annotations

import hashlib
import hmac
import html
import io
import json
import logging
import os
import random
import re
import sqlite3
import time
import asyncio
import unicodedata
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
from zara import ai as zara_ai
from zara import config as app_config
from zara import moderation as modcfg
from zara import tools as zara_tools
from zara.command_registry import build_admin_commands, build_commands
from zara.db import (
    DatabaseConfig,
    InsufficientBalanceError,
    RecordNotFoundError,
    StorageError,
    StorageWriteError,
    create_database,
)
from zara.menus import (
    card_keyboard as modular_card_keyboard,
    category_keyboard as modular_category_keyboard,
    explore_keyboard as modular_explore_keyboard,
    game_detail_keyboard as modular_game_detail_keyboard,
    games_keyboard as modular_games_keyboard,
    games_page_keyboard as modular_games_page_keyboard,
    moderation_category_keyboard as modular_moderation_category_keyboard,
    moderation_keyboard as modular_moderation_keyboard,
    protection_keyboard as modular_protection_keyboard,
    quick_settings_keyboard as modular_quick_settings_keyboard,
    start_keyboard as modular_start_keyboard,
)
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InputFile,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputMediaPhoto,
    InlineQueryResultGif,
    InputTextMessageContent,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatAction, ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    TypeHandler,
    filters,
)


ROOT = app_config.ROOT
DATA_DIR = app_config.DATA_DIR
DB_PATH = app_config.DB_PATH
OWNER_ID = app_config.OWNER_ID
ADMIN_IDS = app_config.ADMIN_IDS
UPDATE_CHANNEL = app_config.UPDATE_CHANNEL
UPDATE_CHANNEL_URL = app_config.UPDATE_CHANNEL_URL
UPDATE_CHANNEL_LABEL = app_config.UPDATE_CHANNEL_LABEL
UPDATE_CHANNEL_CHAT_ID = app_config.UPDATE_CHANNEL_CHAT_ID
SUPPORT_USERNAME = app_config.SUPPORT_USERNAME
DEVELOPER_USERNAME = app_config.DEVELOPER_USERNAME
DEVELOPER_URL = app_config.DEVELOPER_URL
BOT_NAME = "Zara"
START_IMAGE = app_config.START_IMAGE
POLICY_URL = app_config.POLICY_URL
PAYMENT_URL = app_config.PAYMENT_URL
OPENROUTER_MODEL = app_config.OPENROUTER_MODEL
VERIFY_WEBAPP_URL = app_config.VERIFY_WEBAPP_URL
STICKER_PACK_LINKS = app_config.STICKER_PACK_LINKS

AUTO_STICKER_TRIGGERS = (
    "send sticker",
    "sticker please",
    "sticker pls",
    "sticker",
    "haha",
    "hahaha",
    "lol",
    "😂",
    "🤣",
    "😭",
    "aww",
    "cute",
)
AUTO_STICKER_COOLDOWN = 30

LINK_PATTERN = re.compile(
    r"(?:https?://|www\.|t\.me/|telegram\.me/|joinchat/"
    r"|\b[a-z0-9-]+\.(?:com|net|org|in|io|me|ly|co|app|dev|xyz)\b)",
    re.IGNORECASE,
)
FLOOD_TRACKER: dict[tuple[int, int], deque[float]] = {}
SPAM_TRACKER: dict[tuple[int, int], tuple[str, int, float]] = {}
BIO_CACHE: dict[int, tuple[float, bool]] = {}
MENU_IMAGES = {
    "nav:home": ROOT / "assets" / "moderation" / "anime-1.jpg",
    "explore": ROOT / "assets" / "moderation" / "anime-2.jpg",
    "nav:explore": ROOT / "assets" / "moderation" / "anime-2.jpg",
    "commands": ROOT / "assets" / "moderation" / "anime-2.jpg",
    "cat:games": ROOT / "assets" / "moderation" / "anime-3.jpg",
    "games": ROOT / "assets" / "moderation" / "anime-3.jpg",
    "game_prompts": ROOT / "assets" / "moderation" / "anime-5.jpg",
    "cat:moderation": ROOT / "assets" / "moderation" / "anime-4.jpg",
    "mod:home": ROOT / "assets" / "moderation" / "anime-4.jpg",
    "mod:quick": ROOT / "assets" / "moderation" / "anime-6.jpg",
    "premium": ROOT / "assets" / "moderation" / "anime-7.jpg",
    "cat:owner": ROOT / "assets" / "moderation" / "anime-5.jpg",
}
MASS_MENTION_LIMIT = 5
LONG_MESSAGE_LIMIT = 1000
CONFIG_MESSAGE_TTL = 30
_AUTO_DELETE_TASKS: dict[tuple[int, int], asyncio.Task] = {}
_ANSWERED_CALLBACKS: set[str] = set()
_MEMBER_EVENTS: dict[tuple[int, int, str], float] = {}
VIOLATION_WARNINGS = {
    "antibio": "aapke bio me link hai, pehle link hatao",
    "antilink": "links are not allowed in this group",
    "antispam": "spam is not allowed in this group",
    "antiflood": "flooding is not allowed in this group",
    "antiforward": "forwarded messages are not allowed in this group",
    "antiinline": "inline button messages are not allowed in this group",
    "antimedia": "media messages are not allowed in this group",
    "antisticker": "stickers and gifs are not allowed in this group",
    "antivoice": "voice, audio and document messages are not allowed",
    "antipoll": "polls, location and contact messages are not allowed",
    "antimassmention": "mass mentions are not allowed in this group",
    "anticaps": "too many capital letters, please use normal text",
    "antilong": "your message is too long",
    "antichannel": "channel messages are not allowed in this group",
    "blacklist": "this word is not allowed in this group",
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("zara")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class TelegramTokenRedactor(logging.Filter):
    """Prevent Telegram bot tokens from appearing in logs or tracebacks."""

    token_pattern = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        exception_text = ""
        if record.exc_info:
            exception_text = logging.Formatter().formatException(record.exc_info)
        if self.token_pattern.search(message) or self.token_pattern.search(exception_text):
            record.msg = self.token_pattern.sub("[telegram-token-redacted]", message)
            record.args = ()
            record.exc_info = None
            record.exc_text = None
        return True


for _handler in logging.getLogger().handlers:
    _handler.addFilter(TelegramTokenRedactor())


def small_caps(value: str) -> str:
    table = str.maketrans(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ",
    )
    # The first half above intentionally preserves ASCII capitals in labels.
    lower = value.lower()
    return lower.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"))


DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
_DIGIT_MAP = str.maketrans("0123456789", "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿")


def styled(value: str) -> str:
    """Small-caps text with styled digits for premium command lists."""
    return small_caps(value).translate(_DIGIT_MAP)


def command_page(header: str, sections, note: str = "") -> str:
    """Build one consistent small-caps command list inside a blockquote."""
    lines = [header, DIVIDER]
    for heading, bullet, rows in sections:
        lines.append(heading)
        for command, description in rows:
            lines.append(f"{bullet} /{command} — {styled(description)}")
        lines.append(DIVIDER)
    if note:
        lines.append(note)
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


EXPLORE_TEXT = (
    f"🔍 {styled('Explore Zara')}\n"
    f"{DIVIDER}\n"
    f"<blockquote>{styled('Choose a category to see its command list')}</blockquote>"
)


GAMES_PAGE = (
    f"🎮 {styled('Zara Games')}\n"
    f"{DIVIDER}\n"
    f"<blockquote>{styled('choose a game to see its commands and how-to-play guide')}</blockquote>"
)


def game_page(
    header: str,
    intro: str,
    commands: list[tuple[str, str]],
    steps: list[str],
    note: str = "",
) -> str:
    """Build a game detail page with commands, a guide, and a consistent UI."""
    lines = [header, DIVIDER, f"<blockquote>{styled(intro)}\n"]
    lines.append(f"📜 {styled('Commands')}")
    for command, description in commands:
        lines.append(f"▫️ /{command} — {styled(description)}")
    lines.append("")
    lines.append(f"🎯 {styled('How to play')}")
    for index, step in enumerate(steps, 1):
        lines.append(f"{styled(f'{index}.')} {styled(step)}")
    if note:
        lines.append("")
        lines.append(f"📌 {styled('Note')}: {styled(note)}")
    lines.append("</blockquote>")
    return "\n".join(lines)


GAME_CARD_PAGE = game_page(
    f"🎴 {styled('Card Game')}",
    "A five-round multiplayer card battle. Highest card each round scores points; highest total wins the pot.",
    [
        ("bet [amount]", "open the card lobby, for example /bet 2000"),
        ("card", "join the lobby; at least two players are needed"),
        ("flip a|b|c|d|e", "flip one of your five private cards on your turn"),
    ],
    [
        "Use /bet in a group to open a lobby and set the entry amount.",
        "Other players join with /card. Everyone needs enough coins for the entry.",
        "Zara sends five private cards (1-9) to each player when the game starts.",
        "On your turn use /flip a, b, c, d or e. The highest card wins the round and adds its value to your score.",
        "After five rounds the player with the highest total score wins the pot.",
    ],
    "group admins can use /open and /close to enable or disable games.",
)

GAME_BOMB_PAGE = game_page(
    f"💣 {styled('Bomb Game')}",
    "A hot-potato style group game. Pass the bomb before it explodes.",
    [
        ("bomb", "start the bomb lobby"),
        ("join", "join the current bomb lobby"),
        ("pass", "pass the bomb to the next player"),
    ],
    [
        "Use /bomb to create a lobby. Friends join with /join.",
        "Players take turns using /pass. Every pass moves the bomb forward.",
        "The bomb explodes on a hidden pass count; the player holding it loses the round.",
        "Start a fresh /bomb lobby after an explosion.",
    ],
)

GAME_ROULETTE_PAGE = game_page(
    f"🎰 {styled('Roulette')}",
    "Bet coins on an even-number spin. Even wins double your stake; odd loses the bet.",
    [
        ("roulette [amount]", "spin the wheel with a coin bet, for example /roulette 500"),
    ],
    [
        "Use /roulette [amount] with the number of coins you want to bet.",
        "Zara draws a number from 0 to 36 and shows the result in red or black.",
        "An even result pays double your stake. An odd result loses the stake.",
        "Play responsibly and never bet more than you can afford to lose.",
    ],
)

GAME_BLUFF_PAGE = game_page(
    f"🎭 {styled('Bluff')}",
    "Get a random bluff prompt and turn it into a fun group conversation game.",
    [
        ("bluff", "receive a random bluff prompt"),
    ],
    [
        "Use /bluff in the group to get a random statement.",
        "Take turns adding a convincing story or calling out the bluff.",
        "Vote for the best storyteller or the funniest bluff.",
        "Use /truth, /dare, or /roast for more prompt-based fun.",
    ],
)

GAME_HACK_PAGE = game_page(
    f"🔐 {styled('Hack Game')}",
    "Crack a hidden 3-6 digit password before anyone else and win the reward.",
    [
        ("hack [reward] [digits]", "start a game, for example /hack 5000 4"),
        ("register [amount] coins|gems", "join the running game"),
        ("guess [password]", "try a password with unique digits"),
        ("end", "host only; end the game and refund entries"),
    ],
    [
        "The host starts with /hack [reward] [digits]. The reward is funded by the host.",
        "Players join with /register [reward] coins or /register [reward] gems.",
        "Use /guess with exactly the same number of unique digits as the password length.",
        "Hacks are correct digits in the correct position. Glitches are correct digits in the wrong position.",
        "Crack the password to win the reward. The host can use /end to stop and refund.",
    ],
)

GAME_PROMPTS_PAGE = game_page(
    f"🎲 {styled('Truth, Dare and Roast')}",
    "Quick prompt games for group chats. No setup needed.",
    [
        ("truth", "get a random truth question"),
        ("dare", "get a random dare task"),
        ("roast", "get a playful roast line"),
    ],
    [
        "Use /truth, /dare, or /roast alone to get a prompt for yourself.",
        "Reply to a user or pass their @username to give the prompt to them.",
        "Take turns answering the truth, completing the dare, or reacting to the roast.",
        "Keep it friendly and stop if someone is uncomfortable.",
        "Use /bluff for another prompt-based game.",
    ],
)

ECONOMY_PAGE = command_page(
    f"💰 {styled('Economy Commands')}",
    [
        (f"👤 {styled('Basic Economy')}", "▫️", [
            ("bal", "balance"),
            ("balance", "alias"),
            ("profile", "profile card"),
            ("rank", "your rank"),
            ("pfp", "profile photo"),
            ("economy", "menu"),
        ]),
        (f"🏆 {styled('Earn and Leaderboard')}", "🔹", [
            ("daily", "+2,000 coins"),
            ("leaderboard", "top richest"),
            ("toprich", "alias"),
            ("topkill", "top kills"),
            ("leaders", "leaders"),
        ]),
        (f"💸 {styled('Transfer')}", "▪️", [
            ("give", "send coins"),
            ("fpay [P]", "Telegram Stars"),
            ("fgems [P]", "buy gems"),
        ]),
        (f"⚔️ {styled('Risk and Combat')}", "🔸", [
            ("rob", "rob"),
            ("kill", "attack"),
            ("revive", "revive"),
        ]),
        (f"🛡️ {styled('Safety and Protection')}", "◈", [
            ("protect", "buy protection"),
            ("checkprotect", "protection status"),
            ("check", "alias"),
        ]),
        (f"🔒 {styled('Privacy')}", "▸", [
            ("hide [P]", "hide balance"),
            ("unhide", "show balance"),
        ]),
        (f"💎 {styled('Premium')}", "✧", [
            ("premium [P]", "premium"),
            ("pay", "payment options"),
        ]),
        (f"🎒 {styled('Items and Inventory')}", "◆", [
            ("items", "your inventory"),
            ("item", "item info"),
            ("gift", "gift an item"),
        ]),
    ],
    note=f"📌 {styled('Note')}: [P] = {styled('premium feature')}",
)

ACTIONS_PAGE = command_page(
    f"🎭 {styled('Actions and Social')}",
    [
        (f"💗 {styled('Action Commands')}", "▫️", [
            ("love", "send a love GIF"),
            ("crush", "send a crush GIF"),
            ("kiss", "kiss a user"),
            ("hug", "hug a user"),
            ("slap", "slap a user"),
            ("punch", "punch a user"),
            ("bite", "bite a user"),
            ("murder", "dramatic action"),
            ("pat", "pat a user"),
            ("cuddle", "cuddle a user"),
            ("dance", "dance with a user"),
            ("highfive", "high-five a user"),
            ("bonk", "bonk a user"),
        ]),
        (f"💞 {styled('Relationships')}", "🔹", [
            ("marry", "marry a user"),
            ("divorce", "divorce a user"),
            ("couple", "show couple info"),
        ]),
    ],
    note=f"📌 {styled('Note')}: {styled('reply to a user with any action command')}",
)

TOOLS_PAGE = command_page(
    f"🛠️ {styled('Tools')}",
    [
        (f"🪪 {styled('User Info')}", "▫️", [
            ("id", "show a Telegram ID"),
            ("info", "show user information"),
        ]),
        (f"🌤️ {styled('Weather and Time')}", "🔹", [
            ("weather [city]", "current weather and forecast"),
            ("time [city]", "local time and timezone"),
        ]),
        (f"📚 {styled('Knowledge')}", "▪️", [
            ("define [word]", "English word meaning"),
            ("wiki [topic]", "Wikipedia summary"),
        ]),
        (f"🎨 {styled('Media')}", "▸", [
            ("tts [lang] text", "text to speech audio"),
            ("qr [text]", "generate a QR code"),
            ("image [prompt]", "generate an AI image"),
            ("format [png/jpg/webp]", "convert a replied image"),
        ]),
        (f"🧰 {styled('Utility')}", "◆", [
            ("calc [expression]", "safe calculator"),
            ("shorten [url]", "shorten a link"),
            ("translate [lang] text", "translate text"),
            ("currency 100 usd inr", "convert currency"),
        ]),
        (f"📊 {styled('Polls')}", "◈", [
            ("poll question | option 1 | option 2", "create a poll"),
        ]),
        (f"✨ {styled('Custom')}", "🔸", [
            ("setemoji", "set your custom emoji"),
        ]),
        (f"✅ {styled('Verification')}", "▸", [
            ("verify", "verify channel membership"),
        ]),
    ],
    note=f"📌 {styled('Note')}: {styled('verification is required before /daily')}",
)

MODERATION_PAGE = (
    f"🛡️ {styled('Moderation')}\n"
    f"{DIVIDER}\n"
    "<blockquote>"
    f"{styled('group protection and admin controls')}:\n"
    f"▫️ {styled('welcome and goodbye')}\n"
    f"▫️ {styled('rules and filters')}\n"
    f"▫️ {styled('abuse and link protection')}\n"
    f"▫️ {styled('warn and punish')}\n"
    f"▫️ {styled('admin tools')}\n"
    f"▫️ {styled('logs and reports')}\n"
    f"▫️ {styled('quick settings')}"
    "</blockquote>\n"
    f"{DIVIDER}\n"
    f"📌 {styled('Note')}: 👮 {styled('group admin only')} • "
    f"{styled('commands work in groups')}"
)


def moderation_category_page(key: str) -> str:
    data = modcfg.CATEGORIES[key]
    return command_page(
        f"🛡️ {styled(str(data['title']))}",
        [
            (
                f"📂 {styled(str(data['title']))}",
                "▫️",
                list(data["rows"]),
            )
        ],
        note=f"📌 {styled('Note')}: {data['note']}",
    )


MODERATION_CATEGORY_PAGES = {
    f"mod:cat:{key}": moderation_category_page(key)
    for key in modcfg.CATEGORIES
}

QUICK_SETTINGS_INFO = (
    f"⚙️ {styled('Quick Settings')}\n"
    f"{DIVIDER}\n"
    "<blockquote>"
    f"▫️ {styled('send /quicksettings in the group')}\n"
    f"▫️ {styled('tap a feature to toggle it')}\n"
    f"▫️ {styled('tap the penalty button to change it')}\n"
    f"▫️ {styled('use the page buttons to see more features')}"
    "</blockquote>\n"
    f"{DIVIDER}\n"
    f"📌 {styled('Note')}: {styled('group admin only')}"
)

OWNER_PAGE = command_page(
    f"👑 {styled('Owner Panel')}",
    [
        (f"👑 {styled('Owner Controls')}", "▫️", [
            ("m", "owner control center"),
            ("mstats", "bot statistics"),
            ("maddmoney", "add money"),
            ("maddgems", "add gems"),
            ("mpremium", "grant premium"),
            ("mbroadcast", "broadcast"),
        ]),
    ],
    note=f"📌 {styled('Note')}: {styled('owner only')}",
)

_premium_links = [f"💬 {styled('manual payment')}: @{SUPPORT_USERNAME}"]
if PAYMENT_URL:
    _premium_links.insert(0, f"🔗 {styled('pay online')}: {PAYMENT_URL}")
if POLICY_URL:
    _premium_links.append(f"📜 {styled('terms and refund')}: {POLICY_URL}")

PREMIUM_PAGE = (
    f"💎 {styled('Zara Premium Access')}\n"
    f"{DIVIDER}\n"
    "<blockquote>"
    f"{styled('premium users get')}:\n"
    f"✦ {styled('4 day protection for $2,000')}\n"
    f"✦ {styled('premium gems')}\n"
    f"✦ {styled('extra profile features')}\n\n"
    f"{styled('payment commands')}:\n"
    f"▫️ /fpay — {styled('pay with Telegram Stars')}\n"
    f"▫️ /fgems — {styled('buy gems')}\n"
    f"▫️ /id — {styled('send your id to support')}"
    "</blockquote>\n"
    f"{DIVIDER}\n"
    + "\n".join(_premium_links)
)

CATEGORY_PAGES = {
    "cat:games": GAMES_PAGE,
    "cat:economy": ECONOMY_PAGE,
    "cat:actions": ACTIONS_PAGE,
    "cat:tools": TOOLS_PAGE,
    "cat:moderation": MODERATION_PAGE,
    "cat:owner": OWNER_PAGE,
}


def esc(value: str | None) -> str:
    return html.escape(value or "unknown", quote=False)


PRE_TEXT_MAX = 120
MODERATION_REPLY_COMMANDS = frozenset(
    {
        "ban",
        "tban",
        "unban",
        "kick",
        "mute",
        "tmute",
        "unmute",
        "warn",
        "warnings",
        "resetwarns",
        "promote",
        "demote",
        "pin",
        "unpin",
        "purge",
        "del",
        "lock",
        "unlock",
        "approve",
        "disapprove",
        "admins",
    }
)
FORMAT_REPLY_COMMANDS = MODERATION_REPLY_COMMANDS | frozenset(
    {
        "rob",
        "kill",
        "revive",
        "give",
        "protect",
        "checkprotect",
        "marry",
        "divorce",
        "gift",
        "hide",
        "unhide",
        "kiss",
        "hug",
        "slap",
        "punch",
        "murder",
        "bite",
        "crush",
        "love",
        "pat",
        "cuddle",
        "dance",
        "highfive",
        "bonk",
    }
)


def pre_block(value: str) -> str:
    """Render compact stats as pre; profiles keep this even if copy appears."""
    return f"<pre>{html.escape(value, quote=False)}</pre>"


def pre_or_quote(value: str, limit: int = PRE_TEXT_MAX) -> str:
    """Use pre for short one-line replies and blockquote for long/multiline text."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", value))
    if "\n" in plain or len(plain) > limit:
        return f"<blockquote>{html.escape(plain, quote=False)}</blockquote>"
    return f"<pre>{html.escape(plain, quote=False)}</pre>"


def auto_format_reply(text: str) -> str:
    """Format short replies with pre and long/multiline/link replies with quote."""
    lowered = text.lower()
    if "<pre>" in lowered or "<blockquote>" in lowered:
        return text
    if re.search(r"<a\b", text, re.IGNORECASE):
        return f"<blockquote>{text}</blockquote>"
    plain = html.unescape(re.sub(r"<[^>]+>", "", text))
    if "\n" in plain or len(plain) > PRE_TEXT_MAX:
        return f"<blockquote>{html.escape(plain, quote=False)}</blockquote>"
    return f"<pre>{html.escape(plain, quote=False)}</pre>"


def message_command(update: Update) -> str:
    message = getattr(update, "effective_message", None)
    text = getattr(message, "text", None) or ""
    if not text.startswith("/"):
        return ""
    return text.split()[0].lstrip("/").split("@", 1)[0].lower()


def clean_display_name(value: str | None) -> str:
    """Remove profile decorations that make action captions noisy."""
    name = unicodedata.normalize("NFKC", value or "Telegram user").strip()
    name = re.sub(r"\s*\([^)]*\)", "", name).strip()
    name = re.sub(r"^[#\s]+", "", name)
    small_caps = str.maketrans(
        "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ",
        "abcdefghijklmnopqrstuvwxyz",
    )
    name = name.translate(small_caps)
    name = "".join(
        character
        for character in name
        if character.isspace()
        or unicodedata.category(character)[0] in {"L", "N"}
    )
    return re.sub(r"\s+", " ", name).strip() or "Telegram user"


def display_name(user: Any) -> str:
    """Return a clean, full name for plain-text action messages."""
    return clean_display_name(
        getattr(user, "full_name", None)
        or getattr(user, "username", None)
        or "Telegram user"
    )


def row_label(row: sqlite3.Row | None) -> str:
    """Prefer a readable name or @username over a synthetic Telegram ID label."""
    if not row:
        return "Telegram user"
    name = clean_display_name(row["name"])
    username = row["username"] if "username" in row.keys() else None
    if name.casefold().startswith("telegram user"):
        if username:
            return f"@{str(username).lstrip('@')}"
        return "user"
    return name


def display_name_row(row: sqlite3.Row | None) -> str:
    return row_label(row)


def mention(user: Any) -> str:
    name = esc(
        clean_display_name(
            getattr(user, "full_name", None)
            or getattr(user, "username", None)
            or str(user.id)
        )
    )
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def mention_row(row: sqlite3.Row | None) -> str:
    """Build a fresh clickable mention from persisted user data."""
    if not row:
        return "unknown user"
    return (
        f'<a href="tg://user?id={int(row["user_id"])}">'
        f'{esc(row_label(row))}</a>'
    )


def mention_styled(user: Any) -> str:
    """Clickable mention whose visible name matches Zara's small-caps font."""
    name = esc(styled(clean_display_name(
        getattr(user, "full_name", None)
        or getattr(user, "username", None)
        or str(user.id)
    )))
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def mention_row_styled(row: sqlite3.Row | None) -> str:
    if not row:
        return styled("unknown user")
    return (
        f'<a href="tg://user?id={int(row["user_id"])}">'
        f'{esc(styled(row_label(row)))}</a>'
    )


def matches_filter_word(text: str, word: str) -> bool:
    word = word.strip().lower()
    if not word:
        return False
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text.lower()) is not None


def violation_warning(
    violation: str,
    user: Any,
    removed: bool = True,
    action: str = "nothing",
    outcome: str = "no action",
) -> str:
    label = modcfg.PROTECTION_LABELS.get(violation, "protection")
    reason = VIOLATION_WARNINGS.get(
        violation, "this message is not allowed in this group"
    )
    footer = "message removed" if removed else "could not remove message — check my delete rights"
    action_line = ""
    if action != "nothing":
        action_line = (
            f"\n⚙️ {styled('action')}: <b>{esc(styled(outcome))}</b>"
        )
    return (
        f"🚫 <b>{styled(label)}</b>\n"
        f"{DIVIDER}\n"
        f"<blockquote>▫️ {styled('dear')} {mention_styled(user)}, "
        f"{styled(reason)}.{action_line}</blockquote>\n"
        f"{DIVIDER}\n"
        f"📌 {styled(footer)}"
    )


def full_member_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_react_to_messages=True,
    )


def locked_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_react_to_messages=False,
    )


def chat_default_permissions(chat: Any) -> ChatPermissions:
    permissions = getattr(chat, "permissions", None)
    return permissions if isinstance(permissions, ChatPermissions) else full_member_permissions()


def render_template(template: str, user: Any, chat: Any) -> str:
    """Render welcome/goodbye variables without breaking HTML parse mode."""
    full_name = clean_display_name(
        getattr(user, "full_name", None)
        or getattr(user, "username", None)
        or "Telegram user"
    )
    username = str(getattr(user, "username", "") or "").lstrip("@")
    chat_title = getattr(chat, "title", None) or "this group"
    chat_username = str(getattr(chat, "username", "") or "").lstrip("@")
    values = {
        "{mention}": mention(user),
        "{name}": esc(full_name),
        "{full_name}": esc(full_name),
        "{first_name}": esc(getattr(user, "first_name", None) or full_name),
        "{last_name}": html.escape(str(getattr(user, "last_name", None) or ""), quote=False),
        "{username}": f"@{esc(username)}" if username else "no username",
        "{id}": str(getattr(user, "id", "")),
        "{user_id}": str(getattr(user, "id", "")),
        "{group_name}": esc(chat_title),
        "{group_username}": f"@{esc(chat_username)}" if chat_username else esc(chat_title),
        "{group_id}": str(getattr(chat, "id", "")),
    }
    rendered = template.replace("\\n", "\n")
    for variable, value in values.items():
        rendered = rendered.replace(variable, value)
    return rendered


def pair_value(first_id: int, second_id: int) -> int:
    """Stable, pair-specific relationship value in a friendly 50–100 range."""
    low, high = sorted((int(first_id), int(second_id)))
    return 50 + ((low * 31 + high * 17) % 51)


def is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == ChatType.PRIVATE)


def now() -> int:
    return int(time.time())


class Store:
    """Persistent store backed by SQLite locally or TiDB/MySQL in production.

    All existing callers keep using ``STORE.db.execute(...)``.  The storage
    layer translates SQLite-flavoured SQL for MySQL, returns sqlite3.Row-like
    objects, encrypts sensitive columns when a key is configured, and provides
    explicit transactions for multi-step money/game operations.
    """

    def __init__(self, path: Path | None = None):
        config = DatabaseConfig.from_env(explicit_sqlite_path=path)
        self.config = config
        self.db = create_database(config, initialize=True)
        self._maybe_auto_migrate()
        self.health_check()

    def _maybe_auto_migrate(self) -> None:
        if not self.config.auto_migrate_legacy or not self.config.is_mysql:
            return
        legacy = Path(self.config.legacy_sqlite_path)
        if not legacy.is_file():
            log.warning("Auto-migration requested but legacy database is missing: %s", legacy)
            return
        row = self.db.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        if row and int(row["n"]) > 0:
            log.info("Auto-migration skipped; target already contains users")
            return
        from zara.db.maintenance import migrate_legacy

        report = migrate_legacy(legacy, self.db, self.config.backup_dir)
        log.info(
            "Auto-migrated %s legacy rows into %s; legacy backup=%s",
            report["source_rows"],
            self.config.safe_label(),
            report["legacy_backup"],
        )

    def health_check(self) -> dict[str, Any]:
        health = self.db.health_check()
        log.info(
            "Storage healthy: backend=%s target=%s schema=%s encryption=%s latency=%sms",
            health["backend"],
            health["target"],
            health["schema_version"],
            health["encryption"],
            health["latency_ms"],
        )
        return health

    def transaction(self):
        return self.db.transaction()

    def user(self, tg_user: Any) -> sqlite3.Row:
        stamp = now()
        user_id = int(tg_user.id)
        username = getattr(tg_user, "username", None)
        name = (
            getattr(tg_user, "full_name", None)
            or getattr(tg_user, "first_name", None)
            or "Telegram user"
        )
        username_hash = self.db.username_index(username)
        self.db.execute(
            """INSERT INTO users(user_id,username,username_hash,name,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
               username_hash=excluded.username_hash, name=excluded.name,
               updated_at=excluded.updated_at""",
            (user_id, username, username_hash, name, stamp, stamp),
        )
        self.db.commit()
        row = self.db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            raise StorageWriteError(f"User {user_id} was not persisted")
        return row

    def by_ref(self, ref: str) -> sqlite3.Row | None:
        ref = ref.strip().lstrip("@")
        if ref.isdigit():
            return self.db.execute(
                "SELECT * FROM users WHERE user_id=?", (int(ref),)
            ).fetchone()
        if self.db.encryption_enabled:
            index = self.db.username_index(ref)
            if index:
                row = self.db.execute(
                    "SELECT * FROM users WHERE username_hash=?", (index,)
                ).fetchone()
                if row:
                    return row
        # Plaintext fallback covers rows created before encryption was enabled
        # and keeps /balance @user working until reencrypt runs.
        return self.db.execute(
            "SELECT * FROM users WHERE lower(username)=lower(?)", (ref,)
        ).fetchone()

    def add_money(self, user_id: int, amount: int) -> None:
        result = self.db.execute(
            "UPDATE users SET balance=MAX(0,balance+?),updated_at=? WHERE user_id=?",
            (amount, now(), user_id),
        )
        if result.rowcount != 1:
            raise RecordNotFoundError(f"User {user_id} does not exist")
        self.db.commit()

    def add_gems(self, user_id: int, amount: int) -> None:
        result = self.db.execute(
            "UPDATE users SET gems=MAX(0,gems+?),updated_at=? WHERE user_id=?",
            (amount, now(), user_id),
        )
        if result.rowcount != 1:
            raise RecordNotFoundError(f"User {user_id} does not exist")
        self.db.commit()

    def debit(self, user_id: int, amount: int) -> bool:
        """Atomically subtract coins, refusing to go below zero."""
        if amount <= 0:
            return True
        result = self.db.execute(
            "UPDATE users SET balance=balance-?,updated_at=? "
            "WHERE user_id=? AND balance>=?",
            (amount, now(), user_id, amount),
        )
        self.db.commit()
        return result.rowcount == 1

    def debit_gems(self, user_id: int, amount: int) -> bool:
        if amount <= 0:
            return True
        result = self.db.execute(
            "UPDATE users SET gems=gems-?,updated_at=? "
            "WHERE user_id=? AND gems>=?",
            (amount, now(), user_id, amount),
        )
        self.db.commit()
        return result.rowcount == 1

    def update(self, user_id: int, **values: Any) -> None:
        if not values:
            return
        values["updated_at"] = now()
        keys = ", ".join(f"{key}=?" for key in values)
        result = self.db.execute(
            f"UPDATE users SET {keys} WHERE user_id=?",
            (*values.values(), user_id),
        )
        if result.rowcount != 1:
            raise RecordNotFoundError(f"User {user_id} does not exist")
        self.db.commit()

    def chat(self, chat_id: int, title: str = "") -> sqlite3.Row:
        self.db.execute(
            "INSERT INTO chats(chat_id,title,created_at) VALUES(?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
            (chat_id, title, now()),
        )
        self.db.commit()
        row = self.db.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        if row is None:
            raise StorageWriteError(f"Chat {chat_id} was not persisted")
        return row

    def _load_session(self, table: str, chat_id: int) -> dict[str, Any] | None:
        row = self.db.execute(
            f"SELECT state FROM {table} WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return json.loads(row["state"]) if row else None

    def _save_session(self, table: str, chat_id: int, state: dict[str, Any] | None) -> None:
        if state is None:
            self.db.execute(f"DELETE FROM {table} WHERE chat_id=?", (chat_id,))
            self.db.commit()
            return
        encoded = json.dumps(state)
        self.db.execute(
            f"INSERT INTO {table}(chat_id,state,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET state=excluded.state,"
            "updated_at=excluded.updated_at",
            (chat_id, encoded, now()),
        )
        self.db.commit()
        row = self.db.execute(
            f"SELECT state FROM {table} WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if row is None or row["state"] != encoded:
            raise StorageWriteError(f"{table} state for chat {chat_id} was not persisted")

    def game(self, chat_id: int) -> dict[str, Any] | None:
        return self._load_session("game_sessions", chat_id)

    def save_game(self, chat_id: int, state: dict[str, Any] | None) -> None:
        self._save_session("game_sessions", chat_id, state)

    def hack_game(self, chat_id: int) -> dict[str, Any] | None:
        return self._load_session("hack_sessions", chat_id)

    def save_hack_game(self, chat_id: int, state: dict[str, Any] | None) -> None:
        self._save_session("hack_sessions", chat_id, state)

    def bomb_game(self, chat_id: int) -> dict[str, Any] | None:
        return self._load_session("bomb_sessions", chat_id)

    def save_bomb_game(self, chat_id: int, state: dict[str, Any] | None) -> None:
        self._save_session("bomb_sessions", chat_id, state)

    def mark_chat_member(self, chat_id: int, user_id: int) -> None:
        stamp = now()
        self.db.execute(
            """INSERT INTO chat_members(chat_id,user_id,first_seen_at,last_seen_at)
               VALUES(?,?,?,?)
               ON CONFLICT(chat_id,user_id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
            (chat_id, user_id, stamp, stamp),
        )
        self.db.commit()

    def claim_daily(self, user_id: int, reward: int, xp: int = 100) -> sqlite3.Row | None:
        """Atomically claim /daily; returns the fresh row or None on cooldown."""
        stamp = now()
        result = self.db.execute(
            """UPDATE users SET last_daily=?,
               streak=CASE WHEN last_daily>=? THEN streak+1 ELSE 1 END,
               xp=xp+?, balance=balance+?, updated_at=?
               WHERE user_id=? AND (last_daily=0 OR last_daily<=?)""",
            (stamp, stamp - 2 * 86400, xp, reward, stamp, user_id, stamp - 86400),
        )
        self.db.commit()
        if result.rowcount != 1:
            return None
        return self.db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

    def transfer(
        self,
        sender_id: int,
        target_id: int,
        amount: int,
        *,
        sender_is_staff: bool = False,
    ) -> None:
        if amount <= 0:
            raise StorageWriteError("Transfer amount must be positive")
        with self.db.transaction():
            if not sender_is_staff and not self.debit(sender_id, amount):
                raise InsufficientBalanceError("Insufficient balance")
            self.add_money(target_id, amount)

    def buy_protection(self, user_id: int, price: int, days: int) -> bool:
        stamp = now()
        result = self.db.execute(
            "UPDATE users SET balance=balance-?, "
            "protection_until=MAX(protection_until,?)+?, updated_at=? "
            "WHERE user_id=? AND balance>=?",
            (price, stamp, days * 86400, stamp, user_id, price),
        )
        self.db.commit()
        return result.rowcount == 1

    def rob(self, target_id: int, actor_id: int) -> int:
        with self.db.transaction():
            row = self.db.execute(
                "SELECT balance FROM users WHERE user_id=? FOR UPDATE", (target_id,)
            ).fetchone()
            if not row:
                raise RecordNotFoundError(f"User {target_id} does not exist")
            balance = max(0, int(row["balance"]))
            if balance <= 0:
                return 0
            amount = max(1, min(balance, random.randint(50, 500)))
            if not self.debit(target_id, amount):
                return 0
            self.add_money(actor_id, amount)
            return amount

    def kill(self, target_id: int, actor_id: int) -> None:
        with self.db.transaction():
            result = self.db.execute(
                "UPDATE users SET dead=1,updated_at=? WHERE user_id=?",
                (now(), target_id),
            )
            if result.rowcount != 1:
                raise RecordNotFoundError(f"User {target_id} does not exist")
            result = self.db.execute(
                "UPDATE users SET kills=kills+1,xp=xp+50,updated_at=? WHERE user_id=?",
                (now(), actor_id),
            )
            if result.rowcount != 1:
                raise RecordNotFoundError(f"User {actor_id} does not exist")

    def revive(self, target_id: int, actor_id: int, cost: int) -> bool:
        with self.db.transaction():
            if cost and not self.debit(actor_id, cost):
                return False
            result = self.db.execute(
                "UPDATE users SET dead=0,updated_at=? WHERE user_id=?",
                (now(), target_id),
            )
            if result.rowcount != 1:
                raise RecordNotFoundError(f"User {target_id} does not exist")
            return True

    def settle_roulette(self, user_id: int, amount: int, win: bool, is_staff: bool) -> bool:
        with self.db.transaction():
            if amount > 0 and not is_staff and not self.debit(user_id, amount):
                return False
            if win and not is_staff:
                self.add_money(user_id, amount * 2)
            return True

    def grant(
        self,
        target_id: int,
        coins: int = 0,
        gems: int = 0,
        premium_days: int = 0,
    ) -> None:
        with self.db.transaction():
            if coins:
                self.add_money(target_id, coins)
            if gems:
                self.add_gems(target_id, gems)
            if premium_days:
                self.update(
                    target_id,
                    premium_until=now() + max(1, premium_days) * 86400,
                )


STORE = Store()


def staff(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in ADMIN_IDS


def owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def role(user_id: int) -> str:
    return "OWNER" if owner(user_id) else "ADMIN" if user_id in ADMIN_IDS else "PREMIUM" if is_premium(user_id) else "NORMAL"


def is_premium(user_id: int) -> bool:
    row = STORE.db.execute("SELECT premium_until FROM users WHERE user_id=?", (user_id,)).fetchone()
    return bool(row and row["premium_until"] > now())


def telegram_web_app_user_id(init_data: str, bot_token: str) -> int | None:
    """Validate Telegram Web App initData and return its signed user id."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", "")
        auth_date = int(pairs.get("auth_date", "0"))
        if not received_hash or not auth_date or now() - auth_date > 86400:
            return None
        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(pairs.items())
        )
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            return None
        user = json.loads(pairs.get("user", "{}"))
        user_id = int(user.get("id", 0))
        return user_id or None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def verification_markup() -> InlineKeyboardMarkup:
    buttons = []
    if UPDATE_CHANNEL_URL:
        buttons.append([
            InlineKeyboardButton("📢 Join updates", url=UPDATE_CHANNEL_URL),
        ])
    if VERIFY_WEBAPP_URL:
        buttons.append([
            InlineKeyboardButton(
                "✅ Open verification",
                web_app=WebAppInfo(url=VERIFY_WEBAPP_URL),
            )
        ])
    return InlineKeyboardMarkup(buttons)


def normal_user_verified(row: sqlite3.Row | None) -> bool:
    if not UPDATE_CHANNEL_CHAT_ID:
        return True
    return bool(row and int(row["verified_at"] or 0) >= now() - 86400)


def verification_required_text() -> str:
    return (
        "🔒 ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ʀᴇǫᴜɪʀᴇᴅ ғᴏʀ ɴᴏʀᴍᴀʟ ᴜsᴇʀs.\n"
        f"ᴊᴏɪɴ {UPDATE_CHANNEL_URL} ᴛʜᴇɴ ᴛᴀᴘ <b>Open verification</b>."
    )


def invincible(user_id: int) -> bool:
    return staff(user_id)


def games_are_open(chat_id: int) -> bool:
    row = STORE.chat(chat_id)
    return bool(row["games_open"])


def staff_filter_sql() -> tuple[str, tuple[int, ...]]:
    """Return the legacy staff exclusion for callers that need it."""
    ids = tuple(sorted({OWNER_ID, *ADMIN_IDS}))
    return ",".join("?" for _ in ids), ids


def ranking_rows(order_by: str, limit: int = 10) -> list[sqlite3.Row]:
    """Return rankings with the owner visible and virtual resources respected."""
    allowed_order = {
        "balance": "balance DESC",
        "kills": "kills DESC, xp DESC",
        "xp": "xp DESC, kills DESC",
    }
    ordering = allowed_order[order_by]
    # Staff are included, and the owner is pinned first because their virtual
    # balance is unlimited even though SQLite stores a normal starter balance.
    return STORE.db.execute(
        f"""SELECT * FROM users
            ORDER BY CASE WHEN user_id=? THEN 0 ELSE 1 END, {ordering}
            LIMIT ?""",
        (OWNER_ID, limit),
    ).fetchall()


def group_ranking_rows(chat_id: int, limit: int = 10) -> list[sqlite3.Row]:
    """Return richest users seen in this group, with owner pinned first."""
    return STORE.db.execute(
        """SELECT users.* FROM users
           INNER JOIN chat_members ON chat_members.user_id=users.user_id
           WHERE chat_members.chat_id=?
           ORDER BY CASE WHEN users.user_id=? THEN 0 ELSE 1 END,
                    users.balance DESC, users.xp DESC
           LIMIT ?""",
        (chat_id, OWNER_ID, limit),
    ).fetchall()


def leaderboard_marker(row: sqlite3.Row) -> str:
    return "💓" if is_premium(int(row["user_id"])) else "👤"


def leaderboard_name(row: sqlite3.Row) -> str:
    return esc(styled(str(row["name"] or "Telegram user")))


def leaderboard_text(
    rows: list[sqlite3.Row],
    title: str,
    include_global_button: bool = False,
) -> tuple[str, InlineKeyboardMarkup | None]:
    lines = [f"🏆 <b>{title}</b>", DIVIDER]
    for row in rows:
        balance = "∞" if staff(int(row["user_id"])) else f"${int(row['balance']):,}"
        lines.append(f"{leaderboard_marker(row)} {leaderboard_name(row)}: {balance}")
    lines.extend(
        [
            DIVIDER,
            "💓 = ᴘʀᴇᴍɪᴜᴍ • 👤 = ɴᴏʀᴍᴀʟ",
            "✅ ᴜᴘɢʀᴀᴅᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ: /pay",
        ]
    )
    return "<blockquote>" + "\n".join(lines) + "</blockquote>", None


def number_arg(update: Update, default: int = 0) -> int:
    text = update.effective_message.text or ""
    match = re.search(r"\b(\d+)\b", text)
    return int(match.group(1)) if match else default


def ipv4_async_client(**kwargs: Any) -> httpx.AsyncClient:
    kwargs.setdefault(
        "transport", httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    )
    return httpx.AsyncClient(**kwargs)


async def safe_reply(update: Update, text: str, **kwargs: Any) -> Any:
    text = auto_format_reply(text)
    if update.effective_message:
        try:
            return await update.effective_message.reply_text(
                text, parse_mode=ParseMode.HTML, **kwargs
            )
        except RetryAfter as exc:
            log.warning("Telegram flood control in safe_reply: %ss", exc.retry_after)
            return None
        except TelegramError:
            try:
                return await update.effective_message.reply_text(
                    re.sub("<[^>]+>", "", text),
                    **{k: v for k, v in kwargs.items() if k != "parse_mode"},
                )
            except RetryAfter as exc:
                log.warning(
                    "Telegram flood control in safe_reply fallback: %ss",
                    exc.retry_after,
                )
                return None
            except TelegramError:
                return None
    return None


async def send_chat_notice(
    context: ContextTypes.DEFAULT_TYPE,
    chat: Any,
    text: str,
    **kwargs: Any,
) -> None:
    """Send a group notice without replying to a message that may be deleted."""
    send_kwargs = dict(kwargs)
    send_kwargs.setdefault("parse_mode", ParseMode.HTML)
    try:
        await context.bot.send_message(chat.id, text, **send_kwargs)
        return
    except RetryAfter as exc:
        log.warning("Telegram flood control in group notice: %ss", exc.retry_after)
        return
    except TelegramError:
        pass
    plain_kwargs = {k: v for k, v in kwargs.items() if k != "parse_mode"}
    try:
        await context.bot.send_message(
            chat.id, re.sub("<[^>]+>", "", text), **plain_kwargs
        )
    except RetryAfter as exc:
        log.warning(
            "Telegram flood control in group notice fallback: %ss",
            exc.retry_after,
        )
    except TelegramError:
        log.info("Could not send group notice in %s", chat.id)


async def edit_callback_message(
    query: Any,
    text: str,
    image_path: Path | None = None,
    **kwargs: Any,
) -> Any:
    """Edit a callback menu, keeping short pages as photo + caption."""
    message = query.message
    if not message:
        return None
    bot = query.get_bot()
    parse_mode = kwargs.get("parse_mode")
    reply_markup = kwargs.get("reply_markup")
    has_photo = bool(getattr(message, "photo", None))

    if image_path is not None and has_photo:
        try:
            return await query.edit_message_media(
                media=InputMediaPhoto(
                    media=image_path.read_bytes(),
                    caption=text,
                    parse_mode=parse_mode,
                ),
                reply_markup=reply_markup,
            )
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return message
        except TelegramError:
            pass

    if has_photo:
        if len(re.sub("<[^>]+>", "", text)) <= 1024:
            try:
                return await query.edit_message_caption(
                    caption=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            except BadRequest as exc:
                if "not modified" in str(exc).lower():
                    return message
            except TelegramError:
                pass
        try:
            await message.delete()
        except TelegramError:
            pass
        try:
            return await bot.send_message(
                chat_id=message.chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        except TelegramError:
            pass

    try:
        return await query.edit_message_text(text, **kwargs)
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return message
    except TelegramError:
        pass
    try:
        return await bot.send_message(
            chat_id=message.chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
    except TelegramError:
        return await bot.send_message(
            chat_id=message.chat_id,
            text=re.sub("<[^>]+>", "", text),
            reply_markup=reply_markup,
        )


def schedule_auto_delete(
    bot: Any,
    chat_id: int,
    message_id: int,
    delay: int = CONFIG_MESSAGE_TTL,
) -> None:
    if not message_id:
        return
    key = (int(chat_id), int(message_id))
    previous = _AUTO_DELETE_TASKS.pop(key, None)
    if previous and not previous.done():
        previous.cancel()

    async def delete_later() -> None:
        try:
            await asyncio.sleep(delay)
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except asyncio.CancelledError:
            raise
        except TelegramError:
            pass
        finally:
            if _AUTO_DELETE_TASKS.get(key) is asyncio.current_task():
                _AUTO_DELETE_TASKS.pop(key, None)

    _AUTO_DELETE_TASKS[key] = asyncio.create_task(delete_later())


async def edit_config_message(query: Any, text: str, **kwargs: Any) -> None:
    result = await edit_callback_message(query, text, **kwargs)
    message = result if getattr(result, "message_id", None) else query.message
    if (
        message
        and message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
    ):
        schedule_auto_delete(
            query.get_bot(), message.chat_id, message.message_id
        )


async def answer_callback(query: Any, text: str | None = None, show_alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=show_alert)
    except TelegramError:
        return
    if len(_ANSWERED_CALLBACKS) > 1000:
        _ANSWERED_CALLBACKS.clear()
    _ANSWERED_CALLBACKS.add(str(query.id))


async def user_bio_has_link(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    current = time.monotonic()
    cached = BIO_CACHE.get(user_id)
    if cached and current - cached[0] < 600:
        return cached[1]
    has_link = False
    try:
        profile = await context.bot.get_chat(user_id)
        has_link = bool(LINK_PATTERN.search(str(getattr(profile, "bio", "") or "")))
    except TelegramError:
        has_link = False
    BIO_CACHE[user_id] = (current, has_link)
    return has_link


async def perform_penalty_action(
    context: ContextTypes.DEFAULT_TYPE,
    chat: Any,
    user_id: int,
    action: str,
) -> str:
    """Apply one concrete ban/mute/kick and return a human-readable outcome."""
    action = action if action in {"ban", "mute", "kick"} else "nothing"
    if action == "nothing":
        return "no action"
    allowed, reason_text = await bot_admin_check(
        context, chat, "can_restrict_members"
    )
    if not allowed:
        return f"{action} failed — {re.sub('<[^>]+>', '', reason_text)}"
    member, error = await target_member(context, chat.id, user_id)
    if not member:
        return f"{action} failed — {error.lstrip('❌ ')}"
    if member.status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}:
        return f"{action} skipped — protected admin"
    try:
        if action == "ban":
            await context.bot.ban_chat_member(chat.id, user_id)
            return "banned"
        if action == "mute":
            await context.bot.restrict_chat_member(
                chat.id,
                user_id,
                ChatPermissions(can_send_messages=False),
                until_date=now() + 600,
            )
            return "muted 10m"
        await context.bot.ban_chat_member(chat.id, user_id)
        try:
            await context.bot.unban_chat_member(chat.id, user_id, only_if_banned=True)
        except TelegramError:
            return "kick failed — unban failed"
        return "kicked"
    except TelegramError as exc:
        return f"{action} failed — {type(exc).__name__}"


async def apply_penalty(
    context: ContextTypes.DEFAULT_TYPE,
    chat: Any,
    user_id: int,
    action: str,
    settings: dict[str, Any],
    reason: str,
    detail: str = "",
) -> str:
    action = action if action in modcfg.PENALTY_CYCLE else "nothing"
    outcome = "no action"
    if action == "warn":
        with STORE.transaction():
            STORE.db.execute(
                "INSERT INTO warnings(chat_id,user_id,count) VALUES(?,?,1) "
                "ON CONFLICT(chat_id,user_id) DO UPDATE SET count=count+1",
                (chat.id, user_id),
            )
            warning = STORE.db.execute(
                "SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
                (chat.id, user_id),
            ).fetchone()
        count = int(warning["count"])
        limit = max(1, int(settings.get("warn_limit", 3)))
        if count < limit:
            outcome = f"warning {count}/{limit}"
        else:
            warn_action = str(settings.get("warn_action", "mute"))
            if warn_action not in {"mute", "kick", "ban"}:
                warn_action = "mute"
            sub_outcome = await perform_penalty_action(
                context, chat, user_id, warn_action
            )
            outcome = f"warning {count}/{limit} → {sub_outcome}"
            if sub_outcome in {"banned", "muted 10m", "kicked"}:
                STORE.db.execute(
                    "DELETE FROM warnings WHERE chat_id=? AND user_id=?",
                    (chat.id, user_id),
                )
                STORE.db.commit()
    elif action != "nothing":
        outcome = await perform_penalty_action(context, chat, user_id, action)
    suffix = f" → {esc(detail[:200])}" if detail else ""
    await send_mod_log(
        context,
        chat,
        f"🛡️ {reason}: {mention_row(STORE.db.execute('SELECT * FROM users WHERE user_id=?', (user_id,)).fetchone())}"
        f" — <b>{esc(outcome)}</b>{suffix}",
        settings,
    )
    return outcome


async def is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    if not chat or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return False
    if staff(update.effective_user.id):
        return True
    try:
        member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
        return member.status in {
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        }
    except TelegramError:
        return False


async def bot_admin_check(
    context: ContextTypes.DEFAULT_TYPE,
    chat: Any,
    permission: str | None = None,
) -> tuple[bool, str]:
    """Check that Zara is an admin and, optionally, has one specific right."""
    try:
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
    except TelegramError:
        return False, "❌ ɪ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴄʜᴇᴄᴋ ᴍʏ ᴀᴅᴍɪɴ ʀɪɢʜᴛs. ᴛʀʏ ᴀɢᴀɪɴ."
    if member.status == ChatMemberStatus.OWNER:
        return True, ""
    if member.status != ChatMemberStatus.ADMINISTRATOR:
        return False, "❌ ᴍᴀᴋᴇ ᴢᴀʀᴀ ᴀ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ ғɪʀsᴛ."
    if permission and not getattr(member, permission, False):
        label = permission.replace("can_", "").replace("_", " ")
        return False, f"❌ ɪ ɴᴇᴇᴅ ᴛʜᴇ <b>{esc(label)}</b> ᴀᴅᴍɪɴ ʀɪɢʜᴛ ғᴏʀ ᴛʜɪs."
    return True, ""


async def target_member(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
) -> tuple[Any | None, str]:
    try:
        return await context.bot.get_chat_member(chat_id, user_id), ""
    except BadRequest:
        return None, "❌ ᴜsᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ."
    except TelegramError:
        return None, "❌ ᴛᴇʟᴇɢʀᴀᴍ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴠᴇʀɪғʏ ᴛʜᴀᴛ ᴜsᴇʀ. ᴛʀʏ ᴀɢᴀɪɴ."


async def validate_moderation_target(
    context: ContextTypes.DEFAULT_TYPE,
    chat: Any,
    target_id: int,
    command: str,
    invoker_id: int,
) -> tuple[Any | None, str]:
    if target_id <= 0:
        return None, "❌ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴜsᴇ ᴀ ᴠᴀʟɪᴅ @ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ."
    if target_id == context.bot.id:
        return None, "❌ ɪ ᴄᴀɴ'ᴛ ᴍᴏᴅᴇʀᴀᴛᴇ ᴍʏsᴇʟғ."
    if target_id == invoker_id:
        return None, "❌ ʏᴏᴜ ᴄᴀɴ'ᴛ ᴍᴏᴅᴇʀᴀᴛᴇ ʏᴏᴜʀsᴇʟғ."
    if staff(target_id):
        return None, "🛡️ ᴢᴀʀᴀ sᴛᴀғғ ᴀʀᴇ ᴘʀᴏᴛᴇᴄᴛᴇᴅ ғʀᴏᴍ ɢʀᴏᴜᴘ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ."
    member, error = await target_member(context, chat.id, target_id)
    if not member:
        return None, error
    if member.status == ChatMemberStatus.OWNER:
        return None, "🛡️ ᴛʜᴇ ɢʀᴏᴜᴘ ᴏᴡɴᴇʀ ɪs ᴘʀᴏᴛᴇᴄᴛᴇᴅ."
    if member.status == ChatMemberStatus.ADMINISTRATOR and command != "demote":
        return None, "🛡️ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴀʀᴇ ᴘʀᴏᴛᴇᴄᴛᴇᴅ. ᴅᴇᴍᴏᴛᴇ ᴛʜᴇᴍ ғɪʀsᴛ."
    return member, ""


_DURATION_RE = re.compile(r"^(\d+)([smhdw])?$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(value: str) -> int | None:
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        return None
    seconds = int(match.group(1)) * _DURATION_UNITS[(match.group(2) or "s").lower()]
    if seconds <= 0 or seconds > 366 * 86400:
        return None
    return seconds


def duration_text(seconds: int) -> str:
    for unit, size in (("ᴅ", 86400), ("ʜ", 3600), ("ᴍ", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def split_duration_args(
    args: list[str], has_reply: bool
) -> tuple[int | None, list[str], str]:
    parsed_values = [
        (index, value, parsed)
        for index, value in enumerate(args)
        if (parsed := parse_duration(value)) is not None
    ]
    with_units = [
        item for item in parsed_values if re.search(r"[smhdw]$", item[1], re.IGNORECASE)
    ]
    candidates = with_units or parsed_values
    if not candidates:
        return None, args, "❌ ɪɴᴄʟᴜᴅᴇ ᴀ ᴅᴜʀᴀᴛɪᴏɴ, ᴇ.ɢ. <code>10m</code>, <code>2h</code> ᴏʀ <code>1d</code>."
    if has_reply:
        if len(candidates) > 1:
            return None, args, "❌ ᴜsᴇ ᴏɴʟʏ ᴏɴᴇ ᴅᴜʀᴀᴛɪᴏɴ."
        index, _, seconds = candidates[0]
        target_args = args[:index] + args[index + 1 :]
        if target_args:
            return None, args, "❌ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘᴀss ᴀ ᴛᴀʀɢᴇᴛ, ɴᴏᴛ ʙᴏᴛʜ."
        return seconds, [], ""
    if with_units:
        if len(with_units) > 1:
            return None, args, "❌ ᴜsᴇ ᴏɴʟʏ ᴏɴᴇ ᴅᴜʀᴀᴛɪᴏɴ."
        index, _, seconds = with_units[0]
        target_args = args[:index] + args[index + 1 :]
        if not target_args:
            return None, args, "❌ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴜsᴇʀ ᴡɪᴛʜ ᴛʜᴇ ᴅᴜʀᴀᴛɪᴏɴ, ᴏʀ ᴜsᴇ <code>/tban @user 1h</code>."
        return seconds, target_args, ""
    if len(parsed_values) < 2:
        return None, args, "❌ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴜsᴇʀ ᴡɪᴛʜ ᴛʜᴇ ᴅᴜʀᴀᴛɪᴏɴ, ᴏʀ ᴜsᴇ <code>/tban @user 1h</code>."
    index, _, seconds = parsed_values[-1]
    target_args = args[:index] + args[index + 1 :]
    if not target_args:
        return None, args, "❌ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴜsᴇʀ ᴡɪᴛʜ ᴛʜᴇ ᴅᴜʀᴀᴛɪᴏɴ, ᴏʀ ᴜsᴇ <code>/tban @user 1h</code>."
    return seconds, target_args, ""


def synthetic_user_row(user_id: int) -> dict[str, Any]:
    """Represent a Telegram ID that has not used Zara, without creating a row."""
    return {
        "user_id": user_id,
        "username": None,
        "name": f"Telegram user {user_id}",
        "balance": 0,
        "gems": 0,
        "xp": 0,
        "kills": 0,
        "dead": 0,
        "protection_until": 0,
        "hide_balance": 0,
    }


async def send_mod_log(
    context: ContextTypes.DEFAULT_TYPE,
    chat: Any,
    text: str,
    settings: dict[str, Any] | None = None,
) -> str:
    """Send a moderation log and return sent / not_configured / failed."""
    if settings is None:
        row = STORE.chat(chat.id, chat.title or "")
        settings = modcfg.load_settings(row)
    channel = settings.get("log_channel") or 0
    if not channel or not settings.get("log_enabled", True):
        return "not_configured"
    try:
        await context.bot.send_message(
            chat_id=int(channel),
            text=f"🛡️ <b>{esc(getattr(chat, 'title', None) or 'group')}</b>\n{text}",
            parse_mode=ParseMode.HTML,
        )
        return "sent"
    except TelegramError as exc:
        log.info("Moderation log failed for %s: %s", channel, type(exc).__name__)
        return "failed"


async def target_user(
    update: Update,
    args: list[str],
    *,
    allow_synthetic: bool = False,
) -> tuple[int, Any | None]:
    message = update.effective_message
    if message and message.reply_to_message:
        replied_user = message.reply_to_message.from_user
        if not replied_user:
            # Channel posts and anonymous admins have no from_user. Never fall
            # back to the command sender in that case.
            return 0, None
        return replied_user.id, STORE.user(replied_user)
    if args:
        # Commands such as /give 500 @user have an amount before the target.
        # Prefer an @username, then the last numeric argument when there are
        # multiple arguments, while keeping /bal 123 valid.
        ref = next((item for item in args if item.startswith("@")), None)
        if ref is None:
            numeric = [item for item in args if item.strip().isdigit()]
            ref = numeric[-1] if len(args) > 1 and numeric else (args[0] if args else "")
        row = STORE.by_ref(ref)
        if row:
            return int(row["user_id"]), row
        # A numeric ID can be moderated even before that user has opened Zara.
        # Do not insert a synthetic row: that would create free starter coins.
        if ref.strip().isdigit():
            user_id = int(ref.strip())
            return (user_id, synthetic_user_row(user_id)) if allow_synthetic else (0, None)
        # Do not silently target the sender when an explicit username is
        # unknown. Users can reply to a message to target anyone reliably.
        return 0, None
    return update.effective_user.id, STORE.user(update.effective_user)


def display_balance(row: sqlite3.Row, user_id: int) -> str:
    if row["hide_balance"] and user_id != int(row["user_id"]) and not staff(user_id):
        return "hidden"
    return "∞" if staff(int(row["user_id"])) else f"${row['balance']:,}"


def protection_text(row: sqlite3.Row, user_id: int) -> str:
    if staff(user_id):
        return "∞"
    remaining = max(0, int(row["protection_until"]) - now())
    if remaining <= 0:
        return "none"
    return f"{remaining // 86400}d {(remaining % 86400) // 3600}h"


def profile_rank(row: sqlite3.Row) -> int | str:
    profile_user_id = int(row["user_id"])
    if owner(profile_user_id):
        return 1
    if staff(profile_user_id):
        return 2
    staff_count = len({OWNER_ID, *ADMIN_IDS})
    rank_row = STORE.db.execute(
        """SELECT COUNT(*)+1 AS rank FROM users
           WHERE balance > ? AND user_id != ?""",
        (row["balance"], OWNER_ID),
    ).fetchone()
    return int(rank_row["rank"]) + staff_count if rank_row else "—"


def profile_text(row: sqlite3.Row, viewer_id: int | None = None) -> str:
    viewer_id = viewer_id or int(row["user_id"])
    xp = int(row["xp"])
    level = xp // 2000 + 1
    progress = xp % 2000
    status = "dead" if row["dead"] else "alive"
    profile_user_id = int(row["user_id"])
    rank = profile_rank(row)
    bal = "👑 OWNER BALANCE" if owner(int(row["user_id"])) else "🛡️ ADMIN BALANCE" if int(row["user_id"]) in ADMIN_IDS else display_balance(row, viewer_id)
    gems = "∞" if staff(int(row["user_id"])) else f"{row['gems']:,}"
    return pre_block(
        f"👤 ɴᴀᴍᴇ: {row['name'] or 'Telegram user'}\n"
        f"💰 ʙᴀʟᴀɴᴄᴇ: {bal}\n"
        f"💎 ɢᴇᴍs: {gems}\n"
        f"🏆 ɢʟᴏʙᴀʟ ʀᴀɴᴋ: {rank}\n"
        f"🔓 sᴛᴀᴛᴜs: {status}\n"
        f"⚔️ ᴋɪʟʟs: {row['kills']}\n"
        f"🟤 ʟᴇᴠᴇʟ {level}: {progress}/2000\n"
        f"🛡️ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ: {protection_text(row, int(row['user_id']))}"
    )


def start_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎮 ᴘʟᴀʏ ɢᴀᴍᴇs", callback_data="games"),
                InlineKeyboardButton("💰 ᴇᴄᴏɴᴏᴍʏ", callback_data="economy"),
            ],
            [
                InlineKeyboardButton("📚 ᴄᴏᴍᴍᴀɴᴅs", callback_data="commands"),
                InlineKeyboardButton("💎 ᴘʀᴇᴍɪᴜᴍ", callback_data="premium"),
            ],
            [
                InlineKeyboardButton("➕ ᴀᴅᴅ ᴍᴇ ᴛᴏ ɢʀᴏᴜᴘ", url=f"https://t.me/{bot_username}?startgroup=true"),
            ],
            [
                InlineKeyboardButton("📢 ᴜᴘᴅᴀᴛᴇs", url=UPDATE_CHANNEL_URL),
                InlineKeyboardButton("💬 sᴜᴘᴘᴏʀᴛ", url=f"https://t.me/{SUPPORT_USERNAME}"),
            ],
        ]
    )


def games_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎴 ᴄᴀʀᴅ ɢᴀᴍᴇ", callback_data="game_card"),
                InlineKeyboardButton("💣 ʙᴏᴍʙ", callback_data="game_bomb"),
            ],
            [
                InlineKeyboardButton("🎰 ʀᴏᴜʟᴇᴛᴛᴇ", callback_data="game_roulette"),
                InlineKeyboardButton("🎭 ʙʟᴜғғ", callback_data="game_bluff"),
            ],
            [
                InlineKeyboardButton("➕ ᴀᴅᴅ ᴢᴀʀᴀ", url=f"https://t.me/{bot_username}?startgroup=true"),
            ],
        ]
    )


def card_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎴 ᴊᴏɪɴ ᴄᴀʀᴅ ɢᴀᴍᴇ", callback_data="card_join"),
                InlineKeyboardButton("💰 ᴜsᴇ /ʙᴇᴛ", callback_data="card_bet"),
            ],
            [
                InlineKeyboardButton("🅰️", callback_data="flip:a"),
                InlineKeyboardButton("🅱️", callback_data="flip:b"),
                InlineKeyboardButton("©️", callback_data="flip:c"),
                InlineKeyboardButton("🅳", callback_data="flip:d"),
                InlineKeyboardButton("🅴", callback_data="flip:e"),
            ],
        ]
    )


# Keep the original call sites compatible while the menu implementations live
# in the readable zara.menus module.
start_keyboard = modular_start_keyboard
games_keyboard = modular_games_keyboard
games_page_keyboard = modular_games_page_keyboard
game_detail_keyboard = modular_game_detail_keyboard
card_keyboard = modular_card_keyboard
explore_keyboard = modular_explore_keyboard
category_keyboard = modular_category_keyboard


def sticker_pack_name(link: str) -> str | None:
    match = re.search(r"/addstickers/([^/?#\s]+)", link)
    return match.group(1) if match else None


async def load_sticker_packs(application: Application) -> None:
    """Load sticker file IDs once; Telegram file IDs remain reusable at runtime."""
    packs: dict[str, tuple[str, ...]] = {}
    failed: list[str] = []
    names = [name for link in STICKER_PACK_LINKS if (name := sticker_pack_name(link))]
    results = await asyncio.gather(
        *(application.bot.get_sticker_set(name) for name in names),
        return_exceptions=True,
    )
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            failed.append(name)
            log.warning("Could not load sticker pack %s: %s", name, type(result).__name__)
            continue
        file_ids = tuple(sticker.file_id for sticker in result.stickers)
        if file_ids:
            packs[name] = file_ids
        else:
            failed.append(name)
    application.bot_data["sticker_packs"] = packs
    application.bot_data["sticker_failed"] = tuple(failed)
    log.info(
        "Loaded %s sticker packs with %s stickers; %s packs unavailable",
        len(packs),
        sum(len(items) for items in packs.values()),
        len(failed),
    )


async def self_ping_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep the optional Mini App web service awake without blocking polling."""
    url = app_config.SELF_PING_URL
    if not url:
        return
    try:
        async with ipv4_async_client(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(
                url, headers={"User-Agent": "ZaraKeepAlive/1.0"}
            )
        log.info("Self-ping %s -> HTTP %s", url, response.status_code)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("Self-ping failed: %s", exc)


async def post_init(application: Application) -> None:
    bot = application.bot
    me = await bot.get_me()
    public_commands = [
        ("start", "open the Zara main menu"),
        ("help", "see all commands"),
        ("game", "open the mini games menu"),
        ("economy", "open the economy menu"),
        ("bet", "start a multiplayer card lobby"),
        ("card", "join or start a card game"),
        ("flip", "flip a card: a, b, c, d, e"),
        ("bomb", "start the bomb game"),
        ("join", "join the bomb game"),
        ("pass", "pass the bomb"),
        ("roulette", "play roulette"),
        ("bluff", "play bluff"),
        ("hack", "start a group hack game"),
        ("register", "join the running hack game"),
        ("guess", "guess the hack password"),
        ("end", "end the hack game and refund entry fees"),
        ("bal", "check your or a friend's balance"),
        ("balance", "check your or a friend's balance"),
        ("profile", "show a profile"),
        ("pfp", "show a user's profile photo"),
        ("daily", "claim your daily reward"),
        ("leaderboard", "see the top richest players"),
        ("toprich", "see the top richest players"),
        ("topkill", "see the top killers"),
        ("rank", "see a user's rank"),
        ("leaders", "see game leaders"),
        ("give", "give coins to a replied user"),
        ("rob", "rob a user"),
        ("kill", "attack a user"),
        ("revive", "revive a dead user"),
        ("protect", "buy protection"),
        ("check", "check protection time"),
        ("checkprotect", "check protection time"),
        ("hide", "hide your balance"),
        ("unhide", "show your balance"),
        ("premium", "see premium access"),
        ("pay", "open premium payment options"),
        ("fpay", "pay with Telegram Stars"),
        ("fgems", "buy gems"),
        ("love", "send a love GIF"),
        ("crush", "send a crush GIF"),
        ("marry", "marry a user"),
        ("divorce", "divorce a user"),
        ("couple", "show couple info"),
        ("items", "list your items"),
        ("item", "use an item"),
        ("gift", "gift an item"),
        ("kiss", "kiss a user"),
        ("hug", "hug a user"),
        ("slap", "slap a user"),
        ("punch", "punch a user"),
        ("bite", "bite a user"),
        ("murder", "dramatic action"),
        ("pat", "pat a user"),
        ("cuddle", "cuddle a user"),
        ("dance", "dance with a user"),
        ("highfive", "high-five a user"),
        ("bonk", "bonk a user"),
        ("roast", "roast a user"),
        ("truth", "get a truth question"),
        ("dare", "get a dare"),
        ("id", "show a Telegram ID"),
        ("verify", "verify channel membership"),
        ("rules", "show group rules"),
        ("welcome", "show the welcome message"),
        ("goodbye", "show the goodbye message"),
        ("filters", "list group filters"),
        ("filter", "add a group filter"),
        ("blacklist", "blacklist a word"),
        ("unblacklist", "remove a blacklisted word"),
        ("stop", "remove one filter"),
        ("stopall", "remove all filters"),
        ("info", "show user information"),
        ("weather", "current weather and forecast"),
        ("time", "local time and timezone"),
        ("define", "English word meaning"),
        ("wiki", "Wikipedia summary"),
        ("tts", "text to speech audio"),
        ("qr", "generate a QR code"),
        ("image", "generate an AI image"),
        ("format", "convert a replied image"),
        ("calc", "safe calculator"),
        ("shorten", "shorten a link"),
        ("translate", "translate text"),
        ("currency", "convert currency"),
        ("poll", "create a poll"),
        ("setemoji", "set your custom emoji"),
        ("setwelcome", "set the group welcome"),
        ("setgoodbye", "set the group goodbye"),
        ("setrules", "set the group rules"),
        ("close", "close economy games in this chat"),
        ("open", "open economy games in this chat"),
    ]
    try:
        await bot.set_my_commands([BotCommand(a, b) for a, b in public_commands])
    except TelegramError as exc:
        log.warning("Could not set public command menu: %s", exc)
    admin_menu_hidden = {
        "balance", "check", "checkprotect", "toprich",
        "crush", "bonk", "highfive", "dance", "pat",
    }
    admin_commands = [
        item for item in public_commands if item[0] not in admin_menu_hidden
    ] + [
        ("ban", "ban a user"),
        ("unban", "unban a user"),
        ("kick", "kick a user"),
        ("mute", "mute a user"),
        ("unmute", "unmute a user"),
        ("warn", "warn a user"),
        ("warnings", "show warnings"),
        ("resetwarns", "reset warnings"),
        ("promote", "promote a user"),
        ("demote", "demote a user"),
        ("pin", "pin the replied message"),
        ("unpin", "unpin the latest message"),
        ("purge", "delete a replied message"),
        ("del", "delete a replied message"),
        ("lock", "lock the group"),
        ("unlock", "unlock the group"),
        ("admins", "list group admins"),
    ]
    for admin_id in {OWNER_ID, *ADMIN_IDS}:
        try:
            await bot.set_my_commands(
                [BotCommand(a, b) for a, b in admin_commands],
                scope=BotCommandScopeChat(admin_id),
            )
        except TelegramError as exc:
            log.warning("Could not set admin command menu: %s", exc)
    owner_commands = public_commands + [
        ("m", "owner control center"),
        ("maddmoney", "owner: add money"),
        ("maddgems", "owner: add gems"),
        ("mpremium", "owner: grant premium"),
        ("mstats", "owner: bot statistics"),
        ("mbroadcast", "owner: broadcast"),
    ]
    try:
        await bot.set_my_commands([BotCommand(a, b) for a, b in owner_commands], scope=BotCommandScopeChat(OWNER_ID))
    except TelegramError as exc:
        log.warning("Could not set owner command menu: %s", exc)
    # These profile updates are cosmetic. Telegram rate-limits them for hours,
    # so only run them when explicitly requested by the owner.
    if os.getenv("ZARA_UPDATE_PROFILE", "").strip().lower() in {"1", "true", "yes"}:
        try:
            await bot.set_my_description(
                "Zara — a gaming, economy, moderation and Hinglish chat companion for Telegram groups."
            )
        except TelegramError as exc:
            log.warning("Could not set bot description: %s", exc)
        try:
            await bot.set_my_name("Zara")
        except TelegramError as exc:
            log.warning("Could not set bot name: %s", exc)
    application.bot_data["username"] = me.username or "ZaraBot"
    await load_sticker_packs(application)
    if app_config.SELF_PING_URL and application.job_queue is not None:
        application.job_queue.run_repeating(
            self_ping_job,
            interval=app_config.SELF_PING_INTERVAL,
            first=app_config.SELF_PING_DELAY,
            name="zara-self-ping",
        )
        log.info(
            "Self-ping enabled for %s every %ss (first ping in %ss)",
            app_config.SELF_PING_URL,
            app_config.SELF_PING_INTERVAL,
            app_config.SELF_PING_DELAY,
        )
    else:
        log.info(
            "Self-ping disabled; set ZARA_SELF_PING_URL or ZARA_WEB_URL when the web URL is ready"
        )
    log.info("Zara started as @%s", me.username)


def start_message(user: Any, row: sqlite3.Row) -> str:
    return (
        f"💙 <b>ʜɪᴇᴇᴇᴇᴇ {esc(user.full_name)}!</b>\n\n"
        "ɪ'ᴍ <b>ᴢᴀʀᴀ</b> — ᴀ ɢᴀᴍɪɴɢ ᴀɴᴅ ᴄʜᴀᴛᴛɪɴɢ ɢɪʀʟ "
        "ʜᴀᴠɪɴɢ ʟᴏᴛs ᴏғ ғᴇᴀᴛᴜʀᴇs ᴛᴏ ᴇɴɢᴀɢᴇ ʏᴏᴜʀ ɢʀᴏᴜᴘ.\n\n"
        "📊 <b>ʏᴏᴜʀ sᴛᴀᴛs:</b>\n"
        + pre_block(
            f"💰 ʙᴀʟᴀɴᴄᴇ: {display_balance(row, user.id)}\n"
            f"🏆 ʀᴀɴᴋ: {profile_rank(row)}\n"
            f"💎 ɢᴇᴍs: {row['gems']:,}\n"
            f"🔪 ᴋɪʟʟs: {row['kills']}"
        )
        + "\n\n👇 <b>ᴛᴀᴘ ᴇxᴘʟᴏʀᴇ ᴛᴏ sᴇᴇ ᴇᴠᴇʀʏ ᴄᴏᴍᴍᴀɴᴅ.</b>"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    row = STORE.user(user)
    if update.effective_chat:
        STORE.chat(update.effective_chat.id, update.effective_chat.title or "")
    bot_username = context.application.bot_data.get("username", "ZaraBot")
    text = start_message(user, row)
    keyboard = start_keyboard(bot_username)
    if START_IMAGE.exists():
        try:
            await update.effective_message.reply_photo(START_IMAGE.open("rb"), caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            return
        except TelegramError:
            pass
    await safe_reply(update, text, reply_markup=keyboard)


def member_event_seen(chat_id: int, user_id: int, action: str, ttl: int = 20) -> bool:
    """Deduplicate join/leave updates that arrive as both service and member events."""
    key = (int(chat_id), int(user_id), action)
    current = time.monotonic()
    previous = _MEMBER_EVENTS.get(key)
    if previous and current - previous < ttl:
        return True
    _MEMBER_EVENTS[key] = current
    if len(_MEMBER_EVENTS) > 2000:
        cutoff = current - ttl
        for item, stamp in list(_MEMBER_EVENTS.items()):
            if stamp < cutoff:
                _MEMBER_EVENTS.pop(item, None)
    return False


async def send_welcome(
    context: ContextTypes.DEFAULT_TYPE,
    chat: Any,
    row: sqlite3.Row,
    settings: dict[str, Any],
    new_user: Any,
) -> None:
    if not settings.get("welcome_enabled"):
        return
    template = row["welcome"] or modcfg.DEFAULT_WELCOME
    text = render_template(template, new_user, chat)
    await send_chat_notice(context, chat, text)
    log.info("Welcome sent for %s in %s", new_user.id, chat.id)


async def process_join(
    context: ContextTypes.DEFAULT_TYPE, chat: Any, new_user: Any
) -> None:
    if not chat or not new_user:
        return
    if member_event_seen(chat.id, new_user.id, "join"):
        return
    row = STORE.chat(chat.id, chat.title or "")
    settings = modcfg.load_settings(row)

    if new_user.is_bot:
        if new_user.id == context.bot.id:
            await send_chat_notice(
                context,
                chat,
                "🃏 <b>ʜɪɪɪ, ᴛʜᴀɴᴋ ʏᴏᴜ ғᴏʀ ᴀᴅᴅɪɴɢ ᴢᴀʀᴀ!</b>\n\n"
                "<blockquote>"
                f"▫️ {styled('Card game')}: /bet 2000 → /card → /flip a\n"
                f"🔹 {styled('Economy')}: /bal • /daily • /leaderboard\n"
                f"▪️ {styled('Group tools')}: /rules • /welcome • /admins"
                "</blockquote>\n\n"
                f"📢 ᴜᴘᴅᴀᴛᴇs: {UPDATE_CHANNEL_LABEL}\n"
                f"💬 sᴜᴘᴘᴏʀᴛ: @{SUPPORT_USERNAME}\n"
                "ᴜsᴇ /help ғᴏʀ ᴛʜᴇ ᴄᴏᴍᴘʟᴇᴛᴇ ᴍᴇɴᴜ.",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            "➕ ᴏᴘᴇɴ ᴢᴀʀᴀ",
                            url=(
                                "https://t.me/"
                                f"{context.application.bot_data.get('username', 'ZaraBot')}"
                            ),
                        ),
                        *(
                            [InlineKeyboardButton("📢 ᴜᴘᴅᴀᴛᴇs", url=UPDATE_CHANNEL_URL)]
                            if UPDATE_CHANNEL_URL
                            else []
                        ),
                    ]]
                ),
            )
            return
        if settings.get("antibot"):
            allowed, error = await bot_admin_check(
                context, chat, "can_restrict_members"
            )
            if not allowed:
                await send_mod_log(
                    context,
                    chat,
                    f"🤖 {styled('anti-bot')}: ᴄᴏᴜʟᴅ ɴᴏᴛ ᴋɪᴄᴋ {mention(new_user)} — {esc(error)}",
                    settings,
                )
                return
            try:
                member = await context.bot.get_chat_member(chat.id, new_user.id)
                if member.status in {
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                }:
                    return
                await context.bot.ban_chat_member(chat.id, new_user.id)
                await context.bot.unban_chat_member(
                    chat.id, new_user.id, only_if_banned=True
                )
                await send_mod_log(
                    context,
                    chat,
                    f"🤖 {styled('anti-bot')}: {mention(new_user)} ᴋɪᴄᴋᴇᴅ.",
                    settings,
                )
            except TelegramError as exc:
                await send_mod_log(
                    context,
                    chat,
                    f"🤖 {styled('anti-bot')}: ᴄᴏᴜʟᴅ ɴᴏᴛ ᴋɪᴄᴋ {mention(new_user)} — {type(exc).__name__}",
                    settings,
                )
        return

    STORE.user(new_user)
    await send_welcome(context, chat, row, settings, new_user)
    approved = STORE.db.execute(
        "SELECT 1 FROM approvals WHERE chat_id=? AND user_id=?",
        (chat.id, new_user.id),
    ).fetchone()
    if settings.get("captcha") and not approved:
        allowed, error = await bot_admin_check(
            context, chat, "can_restrict_members"
        )
        if not allowed:
            await send_mod_log(
                context,
                chat,
                f"🛡️ ᴄᴀᴘᴛᴄʜᴀ sᴋɪᴘᴘᴇᴅ ғᴏʀ {mention(new_user)} — {esc(error)}",
                settings,
            )
            return
        try:
            member = await context.bot.get_chat_member(chat.id, new_user.id)
            if member.status in {
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            }:
                return
            await context.bot.restrict_chat_member(
                chat.id,
                new_user.id,
                ChatPermissions(can_send_messages=False),
            )
        except TelegramError as exc:
            await send_mod_log(
                context,
                chat,
                f"🛡️ ᴄᴀᴘᴛᴄʜᴀ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇsᴛʀɪᴄᴛ {mention(new_user)} — {type(exc).__name__}",
                settings,
            )
            return
        await send_chat_notice(
            context,
            chat,
            f"🛡️ {mention(new_user)}, ᴘʟᴇᴀsᴇ ᴠᴇʀɪғʏ ʏᴏᴜ ᴀʀᴇ ʜᴜᴍᴀɴ.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "✅ ᴠᴇʀɪғʏ",
                        callback_data=f"captcha:verify:{new_user.id}",
                    )
                ]]
            ),
        )
    await send_mod_log(
        context,
        chat,
        f"➕ {mention(new_user)} ᴊᴏɪɴᴇᴅ ᴛʜᴇ ɢʀᴏᴜᴘ.",
        settings,
    )


async def process_leave(
    context: ContextTypes.DEFAULT_TYPE, chat: Any, left_user: Any
) -> None:
    if not chat or not left_user or left_user.is_bot:
        return
    if member_event_seen(chat.id, left_user.id, "leave"):
        return
    row = STORE.chat(chat.id, chat.title or "")
    settings = modcfg.load_settings(row)
    await send_mod_log(
        context,
        chat,
        f"➖ {mention(left_user)} ʟᴇғᴛ ᴛʜᴇ ɢʀᴏᴜᴘ.",
        settings,
    )
    if not settings.get("goodbye_enabled"):
        return
    template = row["goodbye"] or modcfg.DEFAULT_GOODBYE
    goodbye = render_template(template, left_user, chat)
    await send_chat_notice(context, chat, goodbye)
    log.info("Goodbye sent for %s in %s", left_user.id, chat.id)


async def new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.new_chat_members:
        return
    chat = update.effective_chat
    for new_user in update.message.new_chat_members:
        await process_join(context, chat, new_user)


async def goodbye_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.left_chat_member:
        return
    await process_leave(
        context, update.effective_chat, update.message.left_chat_member
    )


async def chat_member_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    event = update.chat_member
    if not event:
        return
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    left_statuses = {
        ChatMemberStatus.LEFT,
        ChatMemberStatus.BANNED,
    }
    active_statuses = {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
        ChatMemberStatus.RESTRICTED,
    }
    if old in left_statuses and new in active_statuses:
        await process_join(context, event.chat, event.new_chat_member.user)
    elif old in active_statuses and new in left_statuses:
        await process_leave(context, event.chat, event.new_chat_member.user)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(
        update,
        EXPLORE_TEXT,
        reply_markup=explore_keyboard(owner(update.effective_user.id)),
    )


async def economy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(update, ECONOMY_PAGE, reply_markup=category_keyboard())


async def game_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(update, GAMES_PAGE, reply_markup=games_page_keyboard())


async def relationship(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command = (update.effective_message.text or "").split()[0].lstrip("/").split("@", 1)[0].lower()
    target_id, target = await target_user(update, context.args)
    if not target or target_id == update.effective_user.id:
        await safe_reply(update, f"ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴛᴏ ᴜsᴇ /{command}.")
        return
    if command == "marry":
        with STORE.transaction():
            sender_partner = STORE.db.execute(
                "SELECT partner_id FROM couples WHERE user_id=?",
                (update.effective_user.id,),
            ).fetchone()
            target_partner = STORE.db.execute(
                "SELECT partner_id FROM couples WHERE user_id=?", (target_id,)
            ).fetchone()
            if sender_partner or target_partner:
                await safe_reply(update, "❌ ᴏɴᴇ ᴏғ ʏᴏᴜ ɪs ᴀʟʀᴇᴀᴅʏ ᴍᴀʀʀɪᴇᴅ.")
                return
            stamp = now()
            STORE.db.execute(
                "INSERT INTO couples(user_id,partner_id,married_at) VALUES(?,?,?)",
                (update.effective_user.id, target_id, stamp),
            )
            STORE.db.execute(
                "INSERT INTO couples(user_id,partner_id,married_at) VALUES(?,?,?)",
                (target_id, update.effective_user.id, stamp),
            )
        await safe_reply(update, f"💍 {mention(update.effective_user)} ᴀɴᴅ {mention_row(target)} ᴀʀᴇ ɴᴏᴡ ᴍᴀʀʀɪᴇᴅ!")
    elif command == "divorce":
        with STORE.transaction():
            partner = STORE.db.execute(
                "SELECT partner_id FROM couples WHERE user_id=?",
                (update.effective_user.id,),
            ).fetchone()
            if not partner or int(partner["partner_id"]) != target_id:
                await safe_reply(update, f"❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴍᴀʀʀɪᴇᴅ ᴛᴏ {mention_row(target)}.")
                return
            STORE.db.execute(
                "DELETE FROM couples WHERE user_id IN (?,?)",
                (update.effective_user.id, target_id),
            )
        await safe_reply(update, f"💔 {mention(update.effective_user)} ᴀɴᴅ {mention_row(target)} ᴀʀᴇ ɴᴏᴡ sɪɴɢʟᴇ.")
    else:
        row = STORE.db.execute("SELECT partner_id FROM couples WHERE user_id=?", (target_id,)).fetchone()
        if row:
            partner = STORE.db.execute("SELECT * FROM users WHERE user_id=?", (row["partner_id"],)).fetchone()
            await safe_reply(update, f"💞 {mention_row(target)} ɪs ᴡɪᴛʜ {mention_row(partner)}.")
        else:
            await safe_reply(update, f"💞 {mention_row(target)} ɪs ᴄᴜʀʀᴇɴᴛʟʏ sɪɴɢʟᴇ.")


async def group_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command = (update.effective_message.text or "").split()[0].lstrip("/").split("@", 1)[0].lower()
    if not update.effective_chat or update.effective_chat.type == ChatType.PRIVATE:
        if command in MODERATION_REPLY_COMMANDS or command in {
            "setwelcome",
            "setgoodbye",
            "setrules",
            "setflood",
            "warnlimit",
            "setwarnaction",
            "filter",
            "stop",
            "filters",
            "logs",
            "report",
        }:
            hint = (
                "🛡️ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ᴡᴏʀᴋs ɪɴ ɢʀᴏᴜᴘs.\n"
                "ᴀᴅᴅ ᴢᴀʀᴀ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ᴀɴᴅ ᴜsᴇ ɪᴛ ᴛʜᴇʀᴇ."
            )
        else:
            hint = (
                "🛡️ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ ᴄᴏᴍᴍᴀɴᴅs ᴡᴏʀᴋ ɪɴ ɢʀᴏᴜᴘs.\n"
                "ᴀᴅᴅ ᴢᴀʀᴀ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ᴀɴᴅ ᴜsᴇ /quicksettings ᴛᴏ ᴄᴏɴғɪɢᴜʀᴇ ɪᴛ."
            )
        await safe_reply(update, hint)
        return
    chat = update.effective_chat
    row = STORE.chat(chat.id, chat.title or "")
    settings = modcfg.load_settings(row)
    value = (update.effective_message.text or "").partition(" ")[2].strip()
    arg = value.lower().split()[0] if value else ""

    if command == "welcome":
        if not settings.get("welcome_enabled"):
            await safe_reply(update, "👋 ᴡᴇʟᴄᴏᴍᴇ ɪs ᴏғғ. ᴜsᴇ /quicksettings ᴛᴏ ᴛᴜʀɴ ɪᴛ ᴏɴ.")
            return
        template = row["welcome"] or modcfg.DEFAULT_WELCOME
        label = "ᴄᴜsᴛᴏᴍ" if row["welcome"] else "ᴅᴇғᴀᴜʟᴛ"
        await safe_reply(update, f"👋 <b>ᴀᴄᴛɪᴠᴇ ᴡᴇʟᴄᴏᴍᴇ ({label})</b>\n<code>{esc(template)}</code>")
        return
    if command == "goodbye":
        template = row["goodbye"] or modcfg.DEFAULT_GOODBYE
        label = "ᴄᴜsᴛᴏᴍ" if row["goodbye"] else "ᴅᴇғᴀᴜʟᴛ"
        if not settings.get("goodbye_enabled"):
            await safe_reply(
                update,
                f"👋 <b>ɢᴏᴏᴅʙʏᴇ ɪs ᴏғғ ({label})</b>\n<code>{esc(template)}</code>\n\nᴜsᴇ /quicksettings ᴛᴏ ᴛᴜʀɴ ɪᴛ ᴏɴ.",
            )
            return
        await safe_reply(update, f"👋 <b>ᴀᴄᴛɪᴠᴇ ɢᴏᴏᴅʙʏᴇ ({label})</b>\n<code>{esc(template)}</code>")
        return
    if command == "rules":
        await safe_reply(update, f"📜 <b>ɢʀᴏᴜᴘ ʀᴜʟᴇs</b>\n{esc(row['rules'] or 'ɴᴏ ʀᴜʟᴇs sᴇᴛ')}")
        return
    if command == "filters":
        values = STORE.db.execute("SELECT word FROM filters WHERE chat_id=?", (chat.id,)).fetchall()
        await safe_reply(update, "🧰 <b>ғɪʟᴛᴇʀs</b>\n" + ("\n".join(f"• {esc(r['word'])}" for r in values) or "ɴᴏ ғɪʟᴛᴇʀs sᴇᴛ."))
        return
    if command == "reports":
        if arg in {"on", "off"}:
            if not await is_group_admin(update, context):
                await safe_reply(update, "❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏɴʟʏ.")
                return
            settings["reports"] = arg == "on"
            modcfg.save_settings(STORE, chat.id, settings)
            await safe_reply(update, f"📨 ʀᴇᴘᴏʀᴛs {'ᴇɴᴀʙʟᴇᴅ' if settings['reports'] else 'ᴅɪsᴀʙʟᴇᴅ'}.")
            return
        await safe_reply(
            update,
            f"📨 <b>ʀᴇᴘᴏʀᴛs</b>: {'✅ ᴏɴ' if settings.get('reports') else '❌ ᴏғғ'}\n"
            "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ /report ᴛᴏ sᴇɴᴅ ɪᴛ ᴛᴏ ᴛʜᴇ ʟᴏɢ ᴄʜᴀɴɴᴇʟ.",
        )
        return
    if command == "report":
        if not settings.get("reports"):
            await safe_reply(update, "📨 ʀᴇᴘᴏʀᴛs ᴀʀᴇ ᴏғғ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.")
            return
        target = (
            update.effective_message.reply_to_message
            if update.effective_message
            else None
        )
        if not target:
            await safe_reply(update, "ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴍᴇssᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ʀᴇᴘᴏʀᴛ.")
            return
        reported = getattr(target, "from_user", None)
        detail = (target.text or target.caption or "media message")[:300]
        delivery = await send_mod_log(
            context,
            chat,
            f"🚨 {mention(update.effective_user)} ʀᴇᴘᴏʀᴛᴇᴅ "
            f"{mention(reported) if reported else 'a message'}:\n{esc(detail)}",
            settings,
        )
        if delivery == "sent":
            await safe_reply(update, "✅ ʀᴇᴘᴏʀᴛ sᴇɴᴛ ᴛᴏ ᴛʜᴇ ʟᴏɢ ᴄʜᴀɴɴᴇʟ.")
        elif delivery == "not_configured":
            await safe_reply(
                update,
                "❌ ɴᴏ ʟᴏɢ ᴄʜᴀɴɴᴇʟ ɪs ᴄᴏɴғɪɢᴜʀᴇᴅ. ᴀsᴋ ᴀ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ ᴛᴏ ᴜsᴇ /logchannel.",
            )
        else:
            await safe_reply(
                update,
                "❌ ᴛʜᴇ ʀᴇᴘᴏʀᴛ ᴄᴏᴜʟᴅ ɴᴏᴛ ʙᴇ sᴇɴᴛ ᴛᴏ ᴛʜᴇ ʟᴏɢ ᴄʜᴀɴɴᴇʟ.",
            )
        return
    if command == "modstats":
        warnings = STORE.db.execute("SELECT COUNT(*) AS n FROM warnings WHERE chat_id=?", (chat.id,)).fetchone()["n"]
        filters = STORE.db.execute("SELECT COUNT(*) AS n FROM filters WHERE chat_id=?", (chat.id,)).fetchone()["n"]
        blocked = STORE.db.execute("SELECT COUNT(*) AS n FROM blacklists WHERE chat_id=?", (chat.id,)).fetchone()["n"]
        await safe_reply(
            update,
            "📊 <b>ᴍᴏᴅ sᴛᴀᴛs</b>\n"
            f"⚠️ ᴡᴀʀɴɪɴɢs: <b>{warnings}</b>\n"
            f"🧰 ғɪʟᴛᴇʀs: <b>{filters}</b>\n"
            f"🚫 ʙʟᴀᴄᴋʟɪsᴛ: <b>{blocked}</b>",
        )
        return

    if not await is_group_admin(update, context):
        await safe_reply(update, "❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏɴʟʏ.")
        return

    if command == "quicksettings":
        message = await safe_reply(
            update,
            modcfg.quick_settings_text(settings),
            reply_markup=modular_quick_settings_keyboard(settings),
        )
        if message:
            schedule_auto_delete(context.bot, chat.id, message.message_id)
        return
    if command in {"setwelcome", "setgoodbye", "setrules"}:
        field = {"setwelcome": "welcome", "setgoodbye": "goodbye", "setrules": "rules"}[command]
        if not value:
            await safe_reply(update, f"ᴜsᴇ /{command} ᴛᴇxᴛ")
            return
        STORE.db.execute(f"UPDATE chats SET {field}=? WHERE chat_id=?", (value, chat.id))
        STORE.db.commit()
        if command == "setwelcome":
            settings["welcome_enabled"] = True
        elif command == "setgoodbye":
            settings["goodbye_enabled"] = True
        modcfg.save_settings(STORE, chat.id, settings)
        await safe_reply(update, f"✅ {command} sᴀᴠᴇᴅ.")
        return
    if command == "resetwelcome":
        STORE.db.execute("UPDATE chats SET welcome=NULL WHERE chat_id=?", (chat.id,))
        STORE.db.commit()
        settings["welcome_enabled"] = True
        modcfg.save_settings(STORE, chat.id, settings)
        await safe_reply(update, "✅ ᴅᴇғᴀᴜʟᴛ ᴡᴇʟᴄᴏᴍᴇ ʀᴇsᴛᴏʀᴇᴅ.")
        return
    if command == "resetgoodbye":
        STORE.db.execute("UPDATE chats SET goodbye=NULL WHERE chat_id=?", (chat.id,))
        STORE.db.commit()
        settings["goodbye_enabled"] = True
        modcfg.save_settings(STORE, chat.id, settings)
        await safe_reply(update, "✅ ᴅᴇғᴀᴜʟᴛ ɢᴏᴏᴅʙʏᴇ ʀᴇsᴛᴏʀᴇᴅ.")
        return
    if command == "clearrules":
        STORE.db.execute("UPDATE chats SET rules=NULL WHERE chat_id=?", (chat.id,))
        STORE.db.commit()
        await safe_reply(update, "✅ ɢʀᴏᴜᴘ ʀᴜʟᴇs ᴄʟᴇᴀʀᴇᴅ.")
        return
    if command in {"filter", "blacklist"}:
        parts = value.split("|", 1)
        word = parts[0].strip().lower()
        response = parts[1].strip() if len(parts) > 1 else f"ᴍᴀᴛᴄʜᴇᴅ: {word}"
        if not word:
            await safe_reply(update, f"ᴜsᴇ /{command} ᴡᴏʀᴅ|ʀᴇsᴘᴏɴsᴇ")
            return
        if command == "filter":
            STORE.db.execute("INSERT OR REPLACE INTO filters(chat_id,word,response) VALUES(?,?,?)", (chat.id, word, response))
        else:
            STORE.db.execute("INSERT OR IGNORE INTO blacklists(chat_id,word) VALUES(?,?)", (chat.id, word))
        STORE.db.commit()
        await safe_reply(update, f"✅ {command} sᴀᴠᴇᴅ ғᴏʀ <code>{esc(word)}</code>.")
        return
    if command == "unblacklist":
        word = value.lower().strip()
        if not word:
            await safe_reply(update, "ᴜsᴇ /unblacklist ᴡᴏʀᴅ")
            return
        result = STORE.db.execute(
            "DELETE FROM blacklists WHERE chat_id=? AND word=?", (chat.id, word)
        )
        STORE.db.commit()
        if result.rowcount:
            await safe_reply(update, f"✅ ʀᴇᴍᴏᴠᴇᴅ <code>{esc(word)}</code> ғʀᴏᴍ ᴛʜᴇ ʙʟᴀᴄᴋʟɪsᴛ.")
        else:
            await safe_reply(update, f"ℹ️ <code>{esc(word)}</code> ɪs ɴᴏᴛ ʙʟᴀᴄᴋʟɪsᴛᴇᴅ.")
        return
    if command in {"stop", "stopall"}:
        if command == "stop" and not value.strip():
            await safe_reply(update, "ᴜsᴇ /stop ᴡᴏʀᴅ")
            return
        if command == "stop":
            result = STORE.db.execute(
                "DELETE FROM filters WHERE chat_id=? AND word=?",
                (chat.id, value.lower().strip()),
            )
        else:
            result = STORE.db.execute(
                "DELETE FROM filters WHERE chat_id=?", (chat.id,)
            )
        STORE.db.commit()
        if command == "stop":
            if result.rowcount:
                await safe_reply(update, f"✅ ʀᴇᴍᴏᴠᴇᴅ ғɪʟᴛᴇʀ <code>{esc(value.lower().strip())}</code>.")
            else:
                await safe_reply(update, f"ℹ️ ɴᴏ ғɪʟᴛᴇʀ ɴᴀᴍᴇᴅ <code>{esc(value.lower().strip())}</code>.")
        else:
            await safe_reply(update, f"✅ ʀᴇᴍᴏᴠᴇᴅ <b>{result.rowcount}</b> ғɪʟᴛᴇʀ(s).")
        return
    if command in {"approve", "disapprove"}:
        if not update.effective_message.reply_to_message and not context.args:
            await safe_reply(update, "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴜsᴇ ᴛʜᴇɪʀ ɪᴅ.")
            return
        target_id, target = await target_user(
            update, context.args, allow_synthetic=True
        )
        if target_id <= 0 or not target:
            await safe_reply(update, "❌ ᴜsᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ. ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇɪʀ ᴍᴇssᴀɢᴇ ᴏʀ ᴜsᴇ ᴀ ᴠᴀʟɪᴅ @ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.")
            return
        if target_id == context.bot.id:
            await safe_reply(update, "❌ ɪ ᴄᴀɴ'ᴛ ᴀᴘᴘʀᴏᴠᴇ ᴍʏsᴇʟғ.")
            return
        if command == "approve":
            STORE.db.execute("INSERT OR REPLACE INTO approvals(chat_id,user_id,approved_at) VALUES(?,?,?)", (chat.id, target_id, now()))
        else:
            result = STORE.db.execute(
                "DELETE FROM approvals WHERE chat_id=? AND user_id=?",
                (chat.id, target_id),
            )
            if not result.rowcount:
                await safe_reply(update, f"ℹ️ {esc(target['name'])} ᴡᴀs ɴᴏᴛ ᴀᴘᴘʀᴏᴠᴇᴅ.")
                return
        STORE.db.commit()
        await safe_reply(update, f"✅ {command}ᴅ {esc(target['name'])}.")
        return
    if command in {"close", "open"}:
        STORE.db.execute("UPDATE chats SET games_open=? WHERE chat_id=?", (1 if command == "open" else 0, chat.id))
        STORE.db.commit()
        await safe_reply(update, "✅ ɢᴀᴍᴇs ᴀʀᴇ ᴏᴘᴇɴ." if command == "open" else "🔒 ɢᴀᴍᴇs ᴀʀᴇ ᴄʟᴏsᴇᴅ.")
        return

    protection_commands = {
        "antilink": "antilink",
        "antibio": "antibio",
        "antispam": "antispam",
        "antiflood": "antiflood",
        "antiforward": "antiforward",
        "antiinline": "antiinline",
        "antimedia": "antimedia",
        "antisticker": "antisticker",
        "antivoice": "antivoice",
        "antipoll": "antipoll",
        "antimassmention": "antimassmention",
        "anticaps": "anticaps",
        "antilong": "antilong",
        "antichannel": "antichannel",
        "antibot": "antibot",
        "captcha": "captcha",
    }
    if command in protection_commands:
        key = protection_commands[command]
        if arg in {"on", "enable", "yes"}:
            settings[key] = True
            modcfg.save_settings(STORE, chat.id, settings)
        elif arg in {"off", "disable", "no"}:
            settings[key] = False
            modcfg.save_settings(STORE, chat.id, settings)
        elif arg in {"config", "penalty", "action"}:
            if key in modcfg.CONFIG_KEYS:
                modcfg.cycle_penalty(settings, key)
                modcfg.save_settings(STORE, chat.id, settings)
            else:
                await safe_reply(update, f"⚙️ /{command} has no penalty config.")
                return
        elif arg in set(modcfg.PENALTY_CYCLE) | {"none", "noaction"}:
            if key not in modcfg.CONFIG_KEYS:
                await safe_reply(update, f"⚙️ /{command} has no penalty config.")
                return
            penalty = "nothing" if arg in {"none", "noaction"} else arg
            modcfg.set_penalty(settings, key, penalty)
            modcfg.save_settings(STORE, chat.id, settings)
        message = await safe_reply(
            update,
            modcfg.protection_status_text(key, settings),
            reply_markup=modular_protection_keyboard(key, settings),
        )
        if message:
            schedule_auto_delete(context.bot, chat.id, message.message_id)
        return
    if command == "setflood":
        try:
            limit = int(arg)
        except ValueError:
            limit = 0
        if not 2 <= limit <= 100:
            await safe_reply(update, "ᴜsᴇ /setflood 2-100")
            return
        settings["flood_limit"] = limit
        modcfg.save_settings(STORE, chat.id, settings)
        await safe_reply(update, f"✅ ғʟᴏᴏᴅ ʟɪᴍɪᴛ sᴇᴛ ᴛᴏ <b>{limit}</b> ᴍᴇssᴀɢᴇs.")
        return
    if command == "warnlimit":
        try:
            limit = int(arg)
        except ValueError:
            limit = 0
        if not 1 <= limit <= 10:
            await safe_reply(update, "ᴜsᴇ /warnlimit 1-10")
            return
        settings["warn_limit"] = limit
        modcfg.save_settings(STORE, chat.id, settings)
        await safe_reply(update, f"✅ ᴡᴀʀɴ ʟɪᴍɪᴛ sᴇᴛ ᴛᴏ <b>{limit}</b>.")
        return
    if command == "setwarnaction":
        if arg not in {"mute", "kick", "ban"}:
            await safe_reply(update, "ᴜsᴇ /setwarnaction mute|kick|ban")
            return
        settings["warn_action"] = arg
        modcfg.save_settings(STORE, chat.id, settings)
        await safe_reply(update, f"✅ ᴡᴀʀɴ ᴀᴄᴛɪᴏɴ sᴇᴛ ᴛᴏ <b>{arg}</b>.")
        return
    if command == "logchannel":
        if arg in {"off", "none", "0"}:
            settings["log_channel"] = 0
            settings["log_enabled"] = False
            modcfg.save_settings(STORE, chat.id, settings)
            await safe_reply(update, "✅ ʟᴏɢ ᴄʜᴀɴɴᴇʟ ʀᴇᴍᴏᴠᴇᴅ.")
            return
        if not value:
            current = settings.get("log_channel") or "ɴᴏᴛ sᴇᴛ"
            state = "ᴏɴ" if settings.get("log_enabled", True) else "ᴏғғ"
            await safe_reply(
                update,
                f"📊 ʟᴏɢ ᴄʜᴀɴɴᴇʟ: <code>{current}</code>\n"
                f"📨 ʟᴏɢs: <b>{state}</b>\n"
                "ᴜsᴇ /logchannel -100... ᴏʀ @channel",
            )
            return
        try:
            channel_ref = int(value) if value.lstrip("-").isdigit() else value
            channel_chat = await context.bot.get_chat(channel_ref)
        except (TelegramError, ValueError):
            await safe_reply(update, "❌ ɪɴᴠᴀʟɪᴅ ᴄʜᴀɴɴᴇʟ ᴏʀ ɪ ᴄᴀɴ'ᴛ ᴀᴄᴄᴇss ɪᴛ.")
            return
        if channel_chat.type != ChatType.CHANNEL:
            await safe_reply(update, "❌ ᴛʜᴇ ʟᴏɢ ᴛᴀʀɢᴇᴛ ᴍᴜsᴛ ʙᴇ ᴀ ᴄʜᴀɴɴᴇʟ.")
            return
        try:
            bot_member = await context.bot.get_chat_member(
                channel_chat.id, context.bot.id
            )
        except TelegramError:
            await safe_reply(update, "❌ ɪ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴄʜᴇᴄᴋ ᴍʏ ʀɪɢʜᴛs ɪɴ ᴛʜᴀᴛ ᴄʜᴀɴɴᴇʟ.")
            return
        can_post = bot_member.status == ChatMemberStatus.OWNER or (
            bot_member.status == ChatMemberStatus.ADMINISTRATOR
            and bool(getattr(bot_member, "can_post_messages", False))
        )
        if not can_post:
            await safe_reply(
                update,
                "❌ ᴍᴀᴋᴇ ᴢᴀʀᴀ ᴀɴ ᴀᴅᴍɪɴ ɪɴ ᴛʜᴀᴛ ᴄʜᴀɴɴᴇʟ ᴡɪᴛʜ ᴘᴏsᴛ ᴘᴇʀᴍɪssɪᴏɴ.",
            )
            return
        try:
            await context.bot.send_message(
                channel_chat.id,
                "🛡️ <b>ᴢᴀʀᴀ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ ʟᴏɢs ᴄᴏɴɴᴇᴄᴛᴇᴅ.</b>",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            await safe_reply(update, "❌ ɪ ᴄᴏᴜʟᴅ ɴᴏᴛ sᴇɴᴅ ᴀ ᴛᴇsᴛ ᴍᴇssᴀɢᴇ ᴛᴏ ᴛʜᴀᴛ ᴄʜᴀɴɴᴇʟ.")
            return
        settings["log_channel"] = channel_chat.id
        settings["log_enabled"] = True
        modcfg.save_settings(STORE, chat.id, settings)
        await safe_reply(update, f"✅ ʟᴏɢ ᴄʜᴀɴɴᴇʟ sᴇᴛ ᴛᴏ <code>{channel_chat.id}</code>.")
        return
    if command == "setlog":
        if not settings.get("log_channel"):
            await safe_reply(update, "sᴇᴛ ᴀ ʟᴏɢ ᴄʜᴀɴɴᴇʟ ғɪʀsᴛ: /logchannel @channel")
            return
        settings["log_enabled"] = True
        modcfg.save_settings(STORE, chat.id, settings)
        await safe_reply(update, "✅ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ ʟᴏɢs ᴀʀᴇ ᴇɴᴀʙʟᴇᴅ.")
        return
    if command == "unsetlog":
        settings["log_enabled"] = False
        modcfg.save_settings(STORE, chat.id, settings)
        await safe_reply(
            update,
            "✅ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ ʟᴏɢs ᴀʀᴇ ᴅɪsᴀʙʟᴇᴅ. ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ɪs ᴋᴇᴘᴛ; ᴜsᴇ /setlog ᴛᴏ ʀᴇ-ᴇɴᴀʙʟᴇ.",
        )
        return


async def relationship_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command = (update.effective_message.text or "").split()[0].lstrip("/").split("@", 1)[0].lower()
    target_id, target = await target_user(update, context.args)
    if not target or target_id == update.effective_user.id:
        await safe_reply(update, f"ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴜsᴇʀ ғᴏʀ /{command}.")
        return
    seed = pair_value(update.effective_user.id, target_id)
    if command == "crush":
        await safe_reply(
            update,
            f"💘 <b>{mention_row(target)}'s sᴇᴄʀᴇᴛ ᴄʀᴜsʜ ɪs {mention(update.effective_user)} 🫀🧿🎀</b>\n\n"
            f"ᴄʀᴜsʜ ʟᴇᴠᴇʟ: <b>{seed}%</b> ❤️",
        )
    else:
        await safe_reply(
            update,
            "❤️ <b>ʟᴏᴠᴇ ᴍᴇᴛᴇʀ ʀᴇᴘᴏʀᴛ</b> ❤️\n"
            f"{mention(update.effective_user)} ❤️ {mention_row(target)}\n"
            f"ʟᴏᴠᴇ ᴄᴏᴍᴘᴀᴛɪʙɪʟɪᴛʏ: <b>{seed}%</b> ❤️",
        )


async def item_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command = (
        (update.effective_message.text or "")
        .split()[0]
        .lstrip("/")
        .split("@", 1)[0]
        .lower()
    )
    if command in {"items", "item"}:
        rows = STORE.db.execute("SELECT item,quantity FROM items WHERE user_id=? AND quantity>0", (update.effective_user.id,)).fetchall()
        await safe_reply(update, "🎒 <b>ɪᴛᴇᴍs</b>\n" + ("\n".join(f"• {esc(r['item'])}: {r['quantity']}" for r in rows) or "ɴᴏ ɪᴛᴇᴍs ʏᴇᴛ."))
        return
    if command == "gift":
        target_id, target = await target_user(update, context.args)
        if not target:
            await safe_reply(update, "❌ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴜsᴇ ᴛʜᴇɪʀ @ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.")
            return
        if target_id == update.effective_user.id:
            await safe_reply(update, "❌ ʏᴏᴜ ᴄᴀɴ'ᴛ ɢɪғᴛ ᴀɴ ɪᴛᴇᴍ ᴛᴏ ʏᴏᴜʀsᴇʟғ.")
            return
        item_args = list(context.args)
        if update.effective_message and update.effective_message.reply_to_message:
            item = item_args[-1].strip().lower() if item_args else ""
        else:
            target_ref = next(
                (value for value in item_args if value.startswith("@")), None
            )
            if target_ref is None:
                numeric = [value for value in item_args if value.strip().isdigit()]
                target_ref = numeric[-1] if numeric else None
            item = next(
                (
                    value.strip().lower()
                    for value in item_args
                    if value != target_ref and not value.startswith("@")
                ),
                "",
            )
        if not item:
            await safe_reply(update, "ᴜsᴇ /gift @user ɪᴛᴇᴍ ᴏʀ ʀᴇᴘʟʏ ᴡɪᴛʜ /gift ɪᴛᴇᴍ.")
            return
        with STORE.transaction():
            owned = STORE.db.execute(
                "SELECT quantity FROM items WHERE user_id=? AND lower(item)=?",
                (update.effective_user.id, item),
            ).fetchone()
            if not owned or int(owned["quantity"]) < 1:
                await safe_reply(update, f"❌ ʏᴏᴜ ᴅᴏɴ'ᴛ ᴏᴡɴ <code>{esc(item)}</code>.")
                return
            STORE.db.execute(
                "UPDATE items SET quantity=quantity-1 WHERE user_id=? AND lower(item)=?",
                (update.effective_user.id, item),
            )
            STORE.db.execute(
                "DELETE FROM items WHERE user_id=? AND lower(item)=? AND quantity<=0",
                (update.effective_user.id, item),
            )
            STORE.db.execute(
                "INSERT INTO items(user_id,item,quantity) VALUES(?,?,1) "
                "ON CONFLICT(user_id,item) DO UPDATE SET quantity=quantity+1",
                (target_id, item),
            )
        await safe_reply(update, f"🎁 {mention(update.effective_user)} ɢɪғᴛᴇᴅ ᴀ {esc(item)} ᴛᴏ {esc(target['name'])}.")
        return
    if command in {"rank", "pfp", "detail"}:
        _, row = await target_user(update, context.args)
        if row:
            await safe_reply(update, profile_text(row, update.effective_user.id))


async def pfp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the latest Telegram profile photo for the selected user."""
    user_id, row = await target_user(update, context.args)
    if not row:
        await safe_reply(update, "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴜsᴇ ᴛʜᴇɪʀ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ ғᴏʀ /pfp.")
        return
    try:
        photos = await context.bot.get_user_profile_photos(user_id=user_id, limit=1)
        if photos.total_count and photos.photos:
            await update.effective_message.reply_photo(
                photos.photos[0][-1].file_id,
                caption=f"👤 {mention_row(row)}'s ᴘʀᴏғɪʟᴇ ᴘʜᴏᴛᴏ",
                parse_mode=ParseMode.HTML,
            )
            return
    except TelegramError as exc:
        log.info("Could not load profile photo for %s: %s", user_id, type(exc).__name__)
    await safe_reply(update, f"👤 {mention_row(row)} ʜᴀs ɴᴏ ᴘᴜʙʟɪᴄ ᴘʀᴏғɪʟᴇ ᴘʜᴏᴛᴏ.")


async def utility_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command = (
        (update.effective_message.text or "")
        .split()[0]
        .lstrip("/")
        .split("@", 1)[0]
        .lower()
    )
    if command == "setemoji":
        emoji = (update.effective_message.text or "").partition(" ")[2].strip() or "💗"
        STORE.db.execute("INSERT OR REPLACE INTO user_settings(user_id,emoji) VALUES(?,?)", (update.effective_user.id, emoji))
        STORE.db.commit()
        await safe_reply(update, f"✅ ᴄᴜsᴛᴏᴍ ᴇᴍᴏᴊɪ sᴇᴛ: {esc(emoji)}")
    elif command == "check":
        await checkprotect(update, context)
    elif command == "stupid_meter":
        target_id, target = await target_user(update, context.args)
        if target:
            value = (target_id * 13) % 101
            await safe_reply(update, f"🧠 sᴛᴜᴘɪᴅ ᴍᴇᴛᴇʀ ғᴏʀ {esc(target['name'])}: <b>{value}%</b>")
    elif command == "hack":
        await safe_reply(update, "💻 ʜᴀᴄᴋᴇʀ ɢᴀᴍᴇ: ᴜsᴇ /hack ᴛᴏ ʀᴜɴ ᴀ sᴀғᴇ ᴄʜᴀʟʟᴇɴɢᴇ. ɴᴏ ʀᴇᴀʟ ᴀᴄᴄᴏᴜɴᴛs ᴀʀᴇ ᴀᴄᴄᴇssᴇᴅ.")
    else:
        await safe_reply(update, f"✅ /{command} ɪs ᴀᴠᴀɪʟᴀʙʟᴇ.")


def _tool_reply_text(update: Update) -> str:
    message = update.effective_message
    reply = message.reply_to_message if message else None
    if not reply:
        return ""
    return (reply.text or reply.caption or "").strip()


def _tool_text(value: Any) -> str:
    return esc(styled(str(value)))


def _tool_location(place: dict[str, Any]) -> str:
    parts = [place.get("name"), place.get("admin1"), place.get("country")]
    return ", ".join(str(part) for part in parts if part) or "Unknown"


def _tool_degree(value: Any) -> str:
    try:
        return f"{round(float(value))}°"
    except (TypeError, ValueError):
        return "—"


def _tool_number(value: Any) -> str:
    try:
        return f"{round(float(value), 1):g}"
    except (TypeError, ValueError):
        return "—"


def _tool_percent(value: Any) -> str:
    try:
        return f"{round(float(value))}%"
    except (TypeError, ValueError):
        return "—"


def _tool_money(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


async def weather_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city = " ".join(context.args).strip()
    data = await zara_tools.get_weather(city)
    place = data["place"]
    current = data["current"]
    daily = data["daily"]
    icon, description = zara_tools.weather_label(current.get("weather_code"))
    lines = [
        f"🌤️ <b>{_tool_text(_tool_location(place))}</b>",
        DIVIDER,
        "<blockquote>",
        f"{icon} {styled('condition')}: <b>{_tool_text(description)}</b>",
        f"🌡️ {styled('temperature')}: <b>{_tool_text(f'{_tool_degree(current.get('temperature_2m'))}C')}</b>",
        f"🤔 {styled('feels like')}: {_tool_text(f'{_tool_degree(current.get('apparent_temperature'))}C')}",
        f"💧 {styled('humidity')}: {_tool_text(_tool_percent(current.get('relative_humidity_2m')))}",
        f"🌬️ {styled('wind')}: {_tool_text(_tool_number(current.get('wind_speed_10m')))} {_tool_text('km/h')}",
        f"☔ {styled('precipitation')}: {_tool_text(_tool_number(current.get('precipitation')))} {_tool_text('mm')}",
        "</blockquote>",
    ]
    forecast = daily[1:4] or daily[:3]
    if forecast:
        lines.append(f"📅 <b>{styled('next 3 days')}</b>")
        lines.append("<blockquote>")
        for item in forecast:
            lines.append(
                f"{item['icon']} {_tool_text(item['day'])}: "
                f"{_tool_text(_tool_degree(item['low']))}–{_tool_text(_tool_degree(item['high']))} • "
                f"{_tool_text(item['description'])} • {_tool_text(_tool_percent(item['rain']))}"
            )
        lines.append("</blockquote>")
    await safe_reply(update, "\n".join(lines))


async def time_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city = " ".join(context.args).strip()
    data = await zara_tools.get_local_time(city)
    lines = [
        f"🕒 <b>{_tool_text(_tool_location(data['place']))}</b>",
        DIVIDER,
        "<blockquote>",
        f"🕰️ {styled('local time')}: <b>{_tool_text(data['time'])}</b>",
        f"📅 {styled('date')}: {_tool_text(data['date'])}",
        f"🌍 {styled('timezone')}: {_tool_text(data['timezone'])}",
        f"⚖️ {styled('utc offset')}: {_tool_text(data['offset'])}",
        "</blockquote>",
    ]
    await safe_reply(update, "\n".join(lines))


async def define_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    word = " ".join(context.args).strip()
    data = await zara_tools.define_word(word)
    lines = [f"📖 <b>{_tool_text(data['word'])}</b>"]
    if data["phonetic"]:
        lines.append(f"<i>{_tool_text(data['phonetic'])}</i>")
    lines.append("<blockquote>")
    for item in data["meanings"][:3]:
        part = f" <i>({_tool_text(item['part'])})</i>" if item["part"] else ""
        lines.append(f"▫️{part} {_tool_text(item['definition'])}")
        if item["example"]:
            lines.append(f"💬 <i>{_tool_text(item['example'])}</i>")
    lines.append("</blockquote>")
    await safe_reply(update, "\n".join(lines))


async def wiki_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    data = await zara_tools.wiki_summary(query)
    extract = data["extract"].strip()
    if len(extract) > 900:
        extract = extract[:900].rsplit(" ", 1)[0] + "…"
    url = html.escape(data["url"], quote=True)
    text = (
        f"📚 <b>{_tool_text(data['title'])}</b>\n"
        f"{DIVIDER}\n"
        f"<blockquote>{_tool_text(extract)}</blockquote>\n"
        f"🔗 <a href=\"{url}\">{_tool_text('read on wikipedia')}</a>"
    )
    await safe_reply(update, text)


async def tts_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, language = zara_tools.parse_tts_args(context.args, _tool_reply_text(update))
    audio = await zara_tools.make_tts(text, language)
    await update.effective_message.reply_audio(
        InputFile(audio, filename="zara-tts.mp3"),
        title=styled("Zara TTS"),
        performer=styled("Zara"),
        caption=f"🎙️ <b>{styled('text to speech')}</b> • {_tool_text(language)}",
        parse_mode=ParseMode.HTML,
    )


async def qr_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip() or _tool_reply_text(update)
    image = zara_tools.make_qr(text)
    preview = text if len(text) <= 300 else text[:300] + "…"
    await update.effective_message.reply_photo(
        InputFile(image, filename="zara-qr.png"),
        caption=(
            f"🔳 <b>{styled('qr code')}</b>\n"
            f"<blockquote>{_tool_text(preview)}</blockquote>"
        ),
        parse_mode=ParseMode.HTML,
    )


async def image_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = " ".join(context.args).strip()
    image = await zara_tools.generate_image(prompt)
    await update.effective_message.reply_photo(
        InputFile(image, filename="zara-image.jpg"),
        caption=(
            f"🎨 <b>{styled('generated image')}</b>\n"
            f"<blockquote>{_tool_text(prompt)}</blockquote>"
        ),
        parse_mode=ParseMode.HTML,
    )


async def format_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = context.args[0] if context.args else "png"
    message = update.effective_message
    reply = message.reply_to_message if message else None
    if not reply:
        raise zara_tools.ToolError("Image ko reply karke /format png bhejo.")
    source = None
    if reply.photo:
        source = reply.photo[-1]
    elif reply.document and (reply.document.mime_type or "").startswith("image/"):
        source = reply.document
    if source is None:
        raise zara_tools.ToolError("Replied message me image nahi hai.")
    if source.file_size and source.file_size > zara_tools.MAX_IMAGE_BYTES:
        raise zara_tools.ToolError("Image 20 MB se badi hai.")
    try:
        file = await context.bot.get_file(source.file_id)
        data = bytes(await file.download_as_bytearray())
    except TelegramError as exc:
        raise zara_tools.ToolError("Image download nahi hui. Dobara try karo.") from exc
    converted, filename, label = await asyncio.to_thread(
        zara_tools.convert_image, data, target
    )
    await message.reply_document(
        InputFile(converted, filename=filename),
        filename=filename,
        caption=(
            f"🖼️ <b>{styled('image converted')}</b>\n"
            f"<blockquote>▫️ {styled('format')}: <b>{_tool_text(label)}</b></blockquote>"
        ),
        parse_mode=ParseMode.HTML,
    )


async def calc_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    expression = " ".join(context.args).strip() or _tool_reply_text(update)
    result = zara_tools.safe_calc(expression)
    await safe_reply(
        update,
        (
            f"🧮 <b>{styled('calculator')}</b>\n"
            f"{DIVIDER}\n"
            f"<blockquote>{_tool_text(expression)} = "
            f"<b>{_tool_text(zara_tools.format_calc_result(result))}</b></blockquote>"
        ),
    )


async def shorten_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = " ".join(context.args).strip() or _tool_reply_text(update)
    short = await zara_tools.shorten_url(url)
    safe_url = html.escape(short, quote=True)
    await safe_reply(
        update,
        (
            f"🔗 <b>{styled('short link')}</b>\n"
            f"{DIVIDER}\n"
            f"<blockquote><a href=\"{safe_url}\">{_tool_text(short)}</a></blockquote>"
        ),
    )


async def translate_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, text = zara_tools.parse_translate_args(
        context.args, _tool_reply_text(update)
    )
    translated, source = await zara_tools.translate_text(target, text)
    await safe_reply(
        update,
        (
            f"🌐 <b>{styled('translation')}</b>\n"
            f"{DIVIDER}\n"
            "<blockquote>"
            f"▫️ {styled('from')}: <b>{_tool_text(source)}</b>\n"
            f"▫️ {styled('to')}: <b>{_tool_text(target)}</b>\n\n"
            f"{_tool_text(translated)}"
            "</blockquote>"
        ),
    )


async def currency_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await zara_tools.convert_currency(context.args)
    lines = [
        f"💱 <b>{styled('currency converter')}</b>",
        DIVIDER,
        "<blockquote>",
        (
            f"▫️ {_tool_text(_tool_money(data['amount']))} {_tool_text(data['base'])} = "
            f"<b>{_tool_text(_tool_money(data['converted']))} {_tool_text(data['target'])}</b>"
        ),
        (
            f"▫️ {styled('rate')}: 1 {_tool_text(data['base'])} = "
            f"{_tool_text(_tool_money(data['rate'], 4))} {_tool_text(data['target'])}"
        ),
    ]
    if data["updated"]:
        lines.append(f"▫️ {styled('updated')}: {_tool_text(data['updated'])}")
    lines.append("</blockquote>")
    await safe_reply(update, "\n".join(lines))


async def poll_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question, options = zara_tools.parse_poll(" ".join(context.args).strip())
    await update.effective_message.reply_poll(
        question=styled(question),
        options=[styled(option) for option in options],
        is_anonymous=True,
        allows_multiple_answers=False,
    )


async def tool_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return
    command = message.text.split()[0].lstrip("/").split("@")[0].lower()
    handlers = {
        "weather": weather_tool,
        "time": time_tool,
        "define": define_tool,
        "wiki": wiki_tool,
        "tts": tts_tool,
        "qr": qr_tool,
        "image": image_tool,
        "format": format_tool,
        "calc": calc_tool,
        "shorten": shorten_tool,
        "translate": translate_tool,
        "currency": currency_tool,
        "poll": poll_tool,
    }
    handler = handlers.get(command)
    if handler is None:
        return
    try:
        await handler(update, context)
    except zara_tools.ToolError as exc:
        await safe_reply(update, f"❌ {_tool_text(str(exc))}")
    except TelegramError:
        log.exception("Telegram error in /%s", command)
        await safe_reply(update, f"⚠️ {_tool_text('Telegram request complete nahi hui. Dobara try karo.')}")
    except Exception:
        log.exception("Tool command failed: /%s", command)
        await safe_reply(update, f"⚠️ {_tool_text('Kuch galat ho gaya. Dobara try karo.')}")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id, row = await target_user(update, context.args)
    if not row:
        await safe_reply(update, "❌ ᴜsᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ. ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇɪʀ ᴍᴇssᴀɢᴇ ᴏʀ ᴜsᴇ ᴀ ᴋɴᴏᴡɴ @ᴜsᴇʀɴᴀᴍᴇ.")
        return
    await safe_reply(update, profile_text(row, update.effective_user.id))


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, row = await target_user(update, context.args)
    if row:
        await safe_reply(update, profile_text(row, update.effective_user.id))


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row = STORE.user(update.effective_user)
    if not staff(update.effective_user.id) and not is_premium(update.effective_user.id):
        if not normal_user_verified(row):
            await safe_reply(
                update,
                verification_required_text(),
                reply_markup=verification_markup(),
            )
            return
        if UPDATE_CHANNEL_CHAT_ID:
            try:
                member = await context.bot.get_chat_member(
                    UPDATE_CHANNEL_CHAT_ID, update.effective_user.id
                )
                joined = member.status in {
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                }
            except TelegramError:
                await safe_reply(
                    update,
                    "⚠️ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ɪs ᴛᴇᴍᴘᴏʀᴀʀɪʟʏ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ. ᴛʀʏ /daily ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.",
                )
                return
            if not joined:
                await safe_reply(
                    update,
                    f"🔒 ᴠᴇʀɪғʏ ʏᴏᴜʀsᴇʟғ ғɪʀsᴛ: ᴊᴏɪɴ {UPDATE_CHANNEL_URL} ᴛʜᴇɴ ᴜsᴇ /verify ᴀɴᴅ /daily.",
                )
                return
    reward = 2000
    claimed = STORE.claim_daily(update.effective_user.id, reward)
    if claimed is None:
        fresh = STORE.user(update.effective_user)
        hours = max(1, (86400 - (now() - int(fresh["last_daily"]))) // 3600)
        await safe_reply(
            update,
            pre_or_quote(f"⏳ ᴅᴀɪʟʏ ʀᴇᴡᴀʀᴅ ᴄᴏᴍᴇs ʙᴀᴄᴋ ɪɴ {hours}ʜ."),
        )
        return
    await safe_reply(
        update,
        pre_or_quote(
            f"🎁 ᴅᴀɪʟʏ ᴄʟᴀɪᴍᴇᴅ: ${reward:,}\n"
            f"🔥 sᴛʀᴇᴀᴋ: {int(claimed['streak'])}"
        ),
    )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = ranking_rows("balance")
    text, markup = leaderboard_text(rows, "ᴢᴀʀᴀ ᴛᴏᴘ 10 ʀɪᴄʜᴇsᴛ")
    await safe_reply(update, text, reply_markup=markup)


async def toprich(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        rows = group_ranking_rows(update.effective_chat.id)
        text, markup = leaderboard_text(
            rows, "ɢʀᴏᴜᴘ ᴛᴏᴘ 10 ʀɪᴄʜᴇsᴛ", include_global_button=True
        )
    else:
        rows = ranking_rows("balance")
        text, markup = leaderboard_text(rows, "ɢʟᴏʙᴀʟ ᴛᴏᴘ 10 ʀɪᴄʜᴇsᴛ")
    await safe_reply(update, text, reply_markup=markup)


async def topkill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = ranking_rows("kills")
    lines = [f"⚔️ <b>{styled('Top Killers')}</b>", DIVIDER]
    for index, row in enumerate(rows, 1):
        name = esc(styled(str(row["name"] or "Telegram user")))
        lines.append(f"{index}. {name} — ⚔️ {row['kills']}")
    text = (
        "<blockquote>" + "\n".join(lines) + "</blockquote>"
        if rows
        else f"<blockquote>{styled('no kills yet')}</blockquote>"
    )
    await safe_reply(update, text)


async def game_leaders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = ranking_rows("xp")
    lines = [f"🎮 <b>{styled('Game Leaders')}</b>", DIVIDER]
    for index, row in enumerate(rows, 1):
        name = esc(styled(str(row["name"] or "Telegram user")))
        lines.append(f"{index}. {name} — ⭐ {row['xp']} xᴘ")
    text = (
        "<blockquote>" + "\n".join(lines) + "</blockquote>"
        if rows
        else f"<blockquote>{styled('no game stats yet')}</blockquote>"
    )
    await safe_reply(update, text)


async def give(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    amount = number_arg(update)
    target_args = context.args
    if (
        not update.effective_message.reply_to_message
        and target_args
        and target_args[0].isdigit()
    ):
        target_args = target_args[1:]
    target_id, target = await target_user(update, target_args)
    sender = STORE.user(update.effective_user)
    if amount <= 0 or not target or target_id == update.effective_user.id:
        await safe_reply(update, "ᴜsᴇ /give 500 ᴡʜɪʟᴇ ʀᴇᴘʟʏɪɴɢ ᴛᴏ ᴀ ᴜsᴇʀ.")
        return
    if not staff(update.effective_user.id) and sender["balance"] < amount:
        await safe_reply(update, "❌ ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴇɴᴏᴜɢʜ ᴍᴏɴᴇʏ.")
        return
    try:
        STORE.transfer(
            update.effective_user.id,
            target_id,
            amount,
        )
    except InsufficientBalanceError:
        await safe_reply(update, "❌ ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴇɴᴏᴜɢʜ ᴍᴏɴᴇʏ.")
        return
    await safe_reply(
        update,
        f"<blockquote>✅ {mention_styled(update.effective_user)} {styled('gave')} "
        f"<b>${amount:,}</b> {styled('to')} {mention_row_styled(target)}.</blockquote>",
    )


async def protect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row = STORE.user(update.effective_user)
    if staff(update.effective_user.id):
        await safe_reply(update, "🛡️ ʏᴏᴜ ᴀʀᴇ ᴀʟᴡᴀʏs ᴘʀᴏᴛᴇᴄᴛᴇᴅ.")
        return
    premium = is_premium(update.effective_user.id)
    price, days = (2000, 4) if premium else (500, 1)
    if row["balance"] < price:
        await safe_reply(update, f"❌ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ ᴄᴏsᴛs ${price:,}.")
        return
    if not STORE.buy_protection(update.effective_user.id, price, days):
        await safe_reply(update, f"❌ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ ᴄᴏsᴛs ${price:,}.")
        return
    await safe_reply(update, f"🛡️ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ ᴀᴄᴛɪᴠᴇ ғᴏʀ {days} ᴅᴀʏs.")


async def rob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    STORE.user(update.effective_user)
    target_id, target = await target_user(update, context.args)
    if target_id == update.effective_user.id or not target:
        await safe_reply(update, "ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴜsᴇʀ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ʀᴏʙ.")
        return
    if invincible(target_id):
        await safe_reply(update, f"🛡️ {esc(target['name'])} ɪs ᴀɴ ᴏᴡɴᴇʀ/ᴀᴅᴍɪɴ — ᴛʜᴇʏ ᴀʀᴇ ɪɴᴠɪɴᴄɪʙʟᴇ!")
        return
    if target["protection_until"] > now():
        await safe_reply(update, f"🛡️ {esc(target['name'])} ɪs ᴘʀᴏᴛᴇᴄᴛᴇᴅ ғᴏʀ {protection_text(target, target_id)}.")
        return
    amount = STORE.rob(target_id, update.effective_user.id)
    if amount <= 0:
        await safe_reply(update, f"💰 {esc(target['name'])} ʜᴀs ɴᴏ ᴄᴏɪɴs ᴛᴏ ʀᴏʙ.")
        return
    await safe_reply(
        update,
        f"<blockquote>💰 {mention_styled(update.effective_user)} {styled('robbed')} "
        f"<b>${amount:,}</b> {styled('from')} {mention_row_styled(target)}.</blockquote>",
    )


async def kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    STORE.user(update.effective_user)
    target_id, target = await target_user(update, context.args)
    if not target or target_id == update.effective_user.id:
        await safe_reply(update, "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴛᴀʀɢᴇᴛ ᴜsᴇʀ.")
        return
    if invincible(target_id):
        await safe_reply(update, f"🛡️ {esc(target['name'])} ɪs ᴀɴ ᴏᴡɴᴇʀ/ᴀᴅᴍɪɴ — ᴛʜᴇʏ ᴀʀᴇ ɪɴᴠɪɴᴄɪʙʟᴇ!")
        return
    if target["dead"]:
        await safe_reply(update, f"💀 {esc(target['name'])} ɪs ᴀʟʀᴇᴀᴅʏ ᴅᴇᴀᴅ. ᴜsᴇ /revive ғɪʀsᴛ.")
        return
    if target["protection_until"] > now():
        await safe_reply(update, f"🛡️ {esc(target['name'])} ɪs ᴘʀᴏᴛᴇᴄᴛᴇᴅ ғᴏʀ {protection_text(target, target_id)}.")
        return
    STORE.kill(target_id, update.effective_user.id)
    await safe_reply(
        update,
        f"<blockquote>⚔️ {mention_styled(update.effective_user)} {styled('defeated')} "
        f"{mention_row_styled(target)}. {styled('/revive can bring them back.')}</blockquote>",
    )


async def revive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target_id, target = await target_user(update, context.args)
    if not target:
        await safe_reply(update, "ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴅᴇᴀᴅ ᴜsᴇʀ.")
        return
    if not target["dead"]:
        await safe_reply(update, f"ℹ️ {esc(target['name'])} ɪs ᴀʟʀᴇᴀᴅʏ ᴀʟɪᴠᴇ.")
        return
    cost = 0 if staff(update.effective_user.id) else 50
    sender = STORE.user(update.effective_user)
    if sender["balance"] < cost:
        await safe_reply(update, "❌ ʏᴏᴜ ɴᴇᴇᴅ $50 ᴛᴏ ʀᴇᴠɪᴠᴇ.")
        return
    if not STORE.revive(target_id, update.effective_user.id, cost):
        await safe_reply(update, "❌ ʏᴏᴜ ɴᴇᴇᴅ $50 ᴛᴏ ʀᴇᴠɪᴠᴇ.")
        return
    await safe_reply(
        update,
        f"<blockquote>💖 {mention_row_styled(target)} {styled('is back alive.')}</blockquote>",
    )


async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(update, PREMIUM_PAGE, reply_markup=category_keyboard())


async def fpay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(update, f"⭐ ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs ᴘᴀʏᴍᴇɴᴛs ᴀʀᴇ ᴍᴀɴᴀɢᴇᴅ ᴍᴀɴᴜᴀʟʟʏ ʀɪɢʜᴛ ɴᴏᴡ. sᴇɴᴅ ʏᴏᴜʀ ɪᴅ ғʀᴏᴍ /id ᴛᴏ @{SUPPORT_USERNAME}.")


async def fgems(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    purchase = f"\nᴘᴜʀᴄʜᴀsᴇ: {PAYMENT_URL}" if PAYMENT_URL else ""
    await safe_reply(
        update,
        "💎 ɢᴇᴍs ᴀʀᴇ ʀᴇsᴇʀᴠᴇᴅ ғᴏʀ ᴘʀᴇᴍɪᴜᴍ ᴜsᴇʀs.\n"
        f"1 ɢᴇᴍ = $10,000{purchase}\n"
        f"ᴍᴀɴᴜᴀʟ: @{SUPPORT_USERNAME}",
    )


async def checkprotect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, row = await target_user(update, context.args)
    if row:
        await safe_reply(update, f"🛡️ <b>{esc(row['name'])}</b> ᴘʀᴏᴛᴇᴄᴛɪᴏɴ: <b>{protection_text(row, int(row['user_id']))}</b>")


async def hide_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_premium(update.effective_user.id) and not staff(update.effective_user.id):
        await safe_reply(update, "💎 /hide ɪs ᴀ ᴘʀᴇᴍɪᴜᴍ ғᴇᴀᴛᴜʀᴇ.")
        return
    STORE.update(update.effective_user.id, hide_balance=1)
    await safe_reply(update, "🙈 ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ ɪs ɴᴏᴡ ʜɪᴅᴅᴇɴ ғʀᴏᴍ ᴘᴜʙʟɪᴄ ᴘʀᴏғɪʟᴇs.")


async def unhide_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    STORE.update(update.effective_user.id, hide_balance=0)
    await safe_reply(update, "👀 ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ ɪs ᴠɪsɪʙʟᴇ ᴀɢᴀɪɴ.")


async def quick_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command = (update.effective_message.text or "").split()[0].lstrip("/").split("@", 1)[0].lower()
    prompts = {
        "truth": ["ᴛᴜᴍʜᴀʀᴀ ᴍᴏsᴛ ᴇᴍʙᴀʀʀᴀssɪɴɢ ᴍᴏᴍᴇɴᴛ?", "ɢʀᴏᴜᴘ ᴍᴇ ᴋɪsᴋᴏ ᴍɪss ᴋᴀʀᴛᴇ ʜᴏ?", "ᴋᴏɪ sᴇᴄʀᴇᴛ ᴛʜᴀᴛ ɴᴏ ᴏɴᴇ ᴋɴᴏᴡs?"],
        "dare": ["ᴋɪsɪ ᴋᴏ ᴄᴜᴛᴇ sᴀ ᴄᴏᴍᴘʟɪᴍᴇɴᴛ ᴅᴏ.", "ᴀᴘɴᴀ ᴘʀᴏғɪʟᴇ ᴘɪᴄ ᴄʜᴀɴɢᴇ ᴋᴀʀᴏ ғᴏʀ 10 ᴍɪɴᴜᴛᴇs.", "ᴛʜᴇ ɢʀᴏᴜᴘ ᴍᴇ ᴇᴋ ғᴜɴɴʏ ᴛʜɪɴɢ ʟɪᴋʜᴏ."],
        "roast": ["ᴛᴜᴍ ᴡᴀɪғɪ ᴋᴇ ᴍᴏʙɪʟᴇ ᴊᴀɪsᴇ ʜᴏ — ʜᴀᴍᴇsʜᴀ ᴄʜᴀʀɢᴇ ᴍᴇ, ᴘᴀʀ ᴋᴀᴍ ᴋᴜᴄʜ ɴᴀʜɪ 💗", "ᴛᴜᴍʜᴀʀᴇ ᴊᴏᴋᴇs ᴋᴇ ʟɪʏᴇ ᴛᴀʟɪʏᴀɴ ɴᴀʜɪ, ᴜᴘᴅᴀᴛᴇ ᴄʜᴀʜɪʏᴇ 😭"],
        "bluff": ["🎭 ʙʟᴜғғ: ᴍᴀɪɴ ᴋᴀʟ ʟᴏᴛᴛᴇʀʏ ᴊᴇᴇᴛɪ ᴛʜɪ.", "🎭 ʙʟᴜғғ: ᴍᴇʀᴇ ᴘᴀs ᴇᴋ sᴇᴄʀᴇᴛ ᴘʟᴀɴ ʜᴀɪ."],
    }
    sender = update.effective_user
    target_id, target = await target_user(update, context.args)
    if target_id == context.bot.id or not target:
        target_id, target = sender.id, STORE.user(sender)
    prompt = random.choice(prompts.get(command, prompts["truth"]))
    labels = {
        "truth": ("💭", "ᴛʀᴜᴛʜ"),
        "dare": ("🎯", "ᴅᴀʀᴇ"),
        "roast": ("🔥", "ʀᴏᴀsᴛ"),
        "bluff": ("🎭", "ʙʟᴜғғ"),
    }
    icon, label = labels.get(command, ("🎲", command.upper()))
    lines = [
        f"{icon} <b>/{command}</b>",
        f"<b>{label} ғᴏʀ {mention_row(target)}</b>",
    ]
    if target_id != sender.id:
        lines.append(f"<b>ғʀᴏᴍ {mention(sender)}</b>")
    lines.append("")
    lines.append(prompt)
    await safe_reply(update, "\n".join(lines))


async def roulette(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP} and not games_are_open(chat.id):
        await safe_reply(update, "🔒 ɢᴀᴍᴇs ᴀʀᴇ ᴄʟᴏsᴇᴅ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.")
        return
    amount = number_arg(update, 0)
    result = random.randint(0, 36)
    parity = "ᴇᴠᴇɴ" if result % 2 == 0 else "ᴏᴅᴅ"
    win = amount > 0 and result % 2 == 0 and result != 0
    if amount > 0 and not staff(update.effective_user.id):
        if not STORE.settle_roulette(update.effective_user.id, amount, win, False):
            await safe_reply(update, "❌ ɪɴsᴜғғɪᴄɪᴇɴᴛ ʙᴀʟᴀɴᴄᴇ.")
            return
    await safe_reply(
        update,
        f"🎰 ʀᴏᴜʟᴇᴛᴛᴇ: <b>{result}</b> ({parity})\n"
        + ("🎉 ʏᴏᴜ ᴡᴏɴ!" if win else "ᴛʀʏ ᴀɢᴀɪɴ — ᴘʟᴀʏ ʀᴇsᴘᴏɴsɪʙʟʏ."),
    )


async def bomb_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type in {ChatType.GROUP, ChatType.SUPERGROUP} and not games_are_open(chat.id):
        await safe_reply(update, "🔒 ɢᴀᴍᴇs ᴀʀᴇ ᴄʟᴏsᴇᴅ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.")
        return
    chat_id = chat.id
    with STORE.transaction():
        state = STORE.bomb_game(chat_id) or {"players": [], "turn": 0, "bomb": None}
        if not state["players"]:
            state["bomb"] = random.randint(0, 5)
        if update.effective_user.id not in state["players"]:
            state["players"].append(update.effective_user.id)
        STORE.save_bomb_game(chat_id, state)
        player_count = len(state["players"])
    await safe_reply(update, f"💣 ʙᴏᴍʙ ɢᴀᴍᴇ ʟᴏʙʙʏ\n👥 ᴘʟᴀʏᴇʀs: {player_count}\nᴜsᴇ /join ᴛᴏ ᴊᴏɪɴ, /pass ᴛᴏ ᴘᴀss ᴛʜᴇ ʙᴏᴍʙ.")


async def bomb_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type in {ChatType.GROUP, ChatType.SUPERGROUP} and not games_are_open(chat.id):
        await safe_reply(update, "🔒 ɢᴀᴍᴇs ᴀʀᴇ ᴄʟᴏsᴇᴅ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.")
        return
    chat_id = chat.id
    with STORE.transaction():
        state = STORE.bomb_game(chat_id) or {
            "players": [],
            "turn": 0,
            "bomb": random.randint(0, 5),
        }
        if update.effective_user.id not in state["players"]:
            state["players"].append(update.effective_user.id)
        STORE.save_bomb_game(chat_id, state)
        player_count = len(state["players"])
    await safe_reply(update, f"✅ {mention(update.effective_user)} ᴊᴏɪɴᴇᴅ ᴛʜᴇ ʙᴏᴍʙ ɢᴀᴍᴇ. ᴘʟᴀʏᴇʀs: {player_count}")


async def bomb_pass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    outcome = "no_game"
    with STORE.transaction():
        state = STORE.bomb_game(chat_id)
        if not state or not state["players"]:
            outcome = "no_game"
        elif update.effective_user.id != state["players"][state["turn"] % len(state["players"])]:
            outcome = "not_turn"
        else:
            state["turn"] += 1
            if state["turn"] % 6 == state["bomb"]:
                outcome = "exploded"
                STORE.save_bomb_game(chat_id, None)
            else:
                outcome = "passed"
                STORE.save_bomb_game(chat_id, state)
    if outcome == "no_game":
        await safe_reply(update, "ᴘᴇʜʟᴇ /bomb sᴛᴀʀᴛ ᴋᴀʀᴏ.")
    elif outcome == "not_turn":
        await safe_reply(update, "⏳ ᴋɪsɪ ᴀᴜʀ ᴋɪ ᴛᴜʀɴ ʜᴀɪ.")
    elif outcome == "exploded":
        await safe_reply(update, f"💥 {mention(update.effective_user)} ᴋᴇ ᴘᴀs ʙᴏᴍʙ ᴘʜᴀᴛ ɢᴀʏᴀ! ɢᴀᴍᴇ ᴏᴠᴇʀ.")
    else:
        await safe_reply(update, "✅ ʙᴏᴍʙ ᴘᴀssᴇᴅ. ɴᴇxᴛ ᴘʟᴀʏᴇʀ!")

async def owner_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not owner(update.effective_user.id):
        await safe_reply(update, "❌ ᴏɴʟʏ ᴛʜᴇ ᴏᴡɴᴇʀ ᴄᴀɴ ᴜsᴇ ᴛʜɪs.")
        return
    command = (
        (update.effective_message.text or "")
        .split()[0]
        .lstrip("/")
        .split("@", 1)[0]
        .lower()
    )
    raw_args = list(context.args)
    target_args = raw_args
    amount = 0
    if update.effective_message.reply_to_message:
        amount = int(raw_args[0]) if raw_args and raw_args[0].isdigit() else 0
    elif len(raw_args) >= 2 and raw_args[0].isdigit():
        target_args = raw_args[:1]
        amount = int(raw_args[1]) if raw_args[1].isdigit() else 0
    elif raw_args and raw_args[0].startswith("@"):
        target_args = raw_args[:1]
        amount = int(raw_args[1]) if len(raw_args) > 1 and raw_args[1].isdigit() else 0
    target_id, target = await target_user(update, target_args)
    if not target:
        await safe_reply(update, "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴜsᴇ ᴛʜᴇɪʀ ɪᴅ/@ᴜsᴇʀɴᴀᴍᴇ.")
        return
    if "addmoney" in command:
        if amount <= 0:
            await safe_reply(update, "ᴜsᴇ /maddmoney @user 500 ᴏʀ ʀᴇᴘʟʏ ᴡɪᴛʜ /maddmoney 500.")
            return
        STORE.grant(target_id, coins=amount)
        await safe_reply(update, f"✅ ᴀᴅᴅᴇᴅ ${amount:,} ᴛᴏ {esc(target['name'])}.")
    elif "addgems" in command:
        if amount <= 0:
            await safe_reply(update, "ᴜsᴇ /maddgems @user 10 ᴏʀ ʀᴇᴘʟʏ ᴡɪᴛʜ /maddgems 10.")
            return
        STORE.grant(target_id, gems=amount)
        await safe_reply(update, f"✅ ᴀᴅᴅᴇᴅ {amount:,} ɢᴇᴍs ᴛᴏ {esc(target['name'])}.")
    elif "premium" in command:
        STORE.grant(target_id, premium_days=max(1, amount or 30))
        await safe_reply(update, f"✅ ᴘʀᴇᴍɪᴜᴍ ɢʀᴀɴᴛᴇᴅ ᴛᴏ {esc(target['name'])}.")


async def owner_center(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not owner(update.effective_user.id):
        await safe_reply(update, "❌ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪs ʜɪᴅᴅᴇɴ.")
        return
    await safe_reply(update, OWNER_PAGE, reply_markup=category_keyboard())


async def owner_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not owner(update.effective_user.id):
        return
    users = STORE.db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    chats = STORE.db.execute("SELECT COUNT(*) AS n FROM chats").fetchone()["n"]
    month = STORE.db.execute("SELECT COUNT(*) AS n FROM users WHERE created_at >= ?", (now() - 30 * 86400,)).fetchone()["n"]
    await safe_reply(update, f"📊 <b>ᴢᴀʀᴀ sᴛᴀᴛs</b>\n👥 ᴜsᴇʀs: {users}\n💬 ᴄʜᴀᴛs: {chats}\n📅 ᴍᴏɴᴛʜʟʏ ᴜsᴇʀs: {month}")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not owner(update.effective_user.id):
        return
    text = (update.effective_message.text or "").partition(" ")[2].strip()
    if not text:
        await safe_reply(update, "ᴜsᴇ /mbroadcast ᴍᴇssᴀɢᴇ")
        return
    sent = 0
    for row in STORE.db.execute("SELECT user_id FROM users").fetchall():
        try:
            await context.bot.send_message(row["user_id"], text)
            sent += 1
        except TelegramError:
            continue
    await safe_reply(update, f"✅ ʙʀᴏᴀᴅᴄᴀsᴛ sᴇɴᴛ ᴛᴏ {sent} ᴜsᴇʀs.")


GIF_ENDPOINTS: dict[str, tuple[str, ...]] = {
    # waifu.pics returns a fresh, action-specific animation on every request.
    # Keeping the endpoint list local avoids a paid GIF API and broken hard-coded IDs.
    "kiss": ("kiss", "kiss", "kiss", "kiss"),
    "hug": ("hug", "hug", "cuddle", "hug"),
    "slap": ("slap", "slap", "bonk", "slap"),
    "punch": ("punch", "punch", "kick", "bonk"),
    "murder": ("kill", "bonk", "yeet", "kick"),
    "bite": ("bite", "bite", "nom", "bite"),
    "crush": ("blush", "smile", "happy", "blush"),
    "love": ("hug", "kiss", "cuddle", "blush"),
    "pat": ("pat", "cuddle", "hug"),
    "cuddle": ("cuddle", "hug", "cuddle"),
    "dance": ("dance", "happy", "smile"),
    "highfive": ("highfive", "happy", "hug"),
    "bonk": ("bonk", "slap", "pat"),
}

GIF_ASSET_NAMES = (
    "kiss", "hug", "slap", "punch", "murder", "bite", "crush", "love",
    "pat", "cuddle", "dance", "highfive", "bonk",
)
GIF_ASSETS: dict[str, tuple[Path, ...]] = {
    name: tuple(sorted((ROOT / "assets" / "gifs").glob(f"{name}-*.gif")))
    for name in GIF_ASSET_NAMES
}
CAPTURE_DIR = Path(os.getenv("ZARA_CAPTURE_DIR", "/tmp/zara_gif_capture"))


GIPHY_QUERY: dict[str, str] = {
    "kiss": "anime kiss reaction",
    "hug": "anime hug reaction",
    "slap": "anime slap reaction",
    "punch": "anime punch reaction",
    "murder": "anime dramatic reaction",
    "bite": "anime bite reaction",
    "crush": "anime crush reaction",
    "love": "anime love reaction",
    "pat": "anime headpat reaction",
    "cuddle": "anime cuddle reaction",
    "dance": "anime dance reaction",
    "highfive": "anime high five reaction",
    "bonk": "anime bonk reaction",
}

GIPHY_QUERY_VARIANTS: dict[str, tuple[str, ...]] = {
    "kiss": ("anime kiss reaction", "anime kissing", "anime chu"),
    "hug": ("anime hug reaction", "anime hugging", "anime embrace"),
    "slap": ("anime slap reaction", "anime slapping", "anime smack"),
    "punch": ("anime punch reaction", "anime punching", "anime punch anime"),
    "murder": ("anime kill reaction", "anime death reaction", "anime dramatic kill"),
    "bite": (
        "anime bite reaction",
        "anime biting",
        "anime nom anime",
        "anime chomp reaction",
        "anime nibble reaction",
        "anime gnaw reaction",
    ),
    "crush": ("anime crush reaction", "anime crushing", "anime secret crush"),
    "love": ("anime love reaction", "anime loving", "anime romance"),
    "pat": ("anime headpat reaction", "anime patting", "anime pat anime"),
    "cuddle": ("anime cuddle reaction", "anime cuddling", "anime snuggle"),
    "dance": ("anime dance reaction", "anime dancing", "anime dance anime"),
    "highfive": ("anime high five reaction", "anime highfive", "anime high five anime"),
    "bonk": ("anime bonk reaction", "anime bonking", "anime bonk anime"),
}

ACTION_GIF_KEYWORDS: dict[str, tuple[str, ...]] = {
    "kiss": ("kiss", "kissing", "chu"),
    "hug": ("hug", "embrace"),
    "slap": ("slap", "smack"),
    "punch": ("punch", "punching"),
    "murder": ("murder", "kill", "killing", "death"),
    "bite": ("bite", "biting", "nom", "chomp", "chomping", "nibble", "nibbling"),
    "crush": ("crush", "crushing"),
    "love": ("love", "loving", "romance"),
    "pat": ("pat", "headpat", "patting"),
    "cuddle": ("cuddle", "snuggle"),
    "dance": ("dance", "dancing"),
    "highfive": ("highfive", "high five"),
    "bonk": ("bonk",),
}

GIF_BLOCKLIST = (
    "porn",
    "nsfw",
    "nude",
    "sex",
    "sexual",
    "fetish",
    "onlyfans",
    "celebrity",
    "politician",
    "news",
    "sports interview",
    "real housewives",
    "live action",
    "irl",
)


GIPHY_POOL_LIMIT = 50
GIPHY_CACHE_SECONDS = 900
GIPHY_POOL_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
GIPHY_RECENT_IDS: dict[str, deque[str]] = {}


ACTION_NAMES = tuple(GIPHY_QUERY)


async def giphy_search(command: str, limit: int = 8) -> list[dict[str, Any]]:
    """Return usable GIPHY image records for a command or inline search."""
    api_key = os.getenv("GIPHY_API_KEY")
    if not api_key:
        return []
    cached = GIPHY_POOL_CACHE.get(command)
    if cached and time.time() - cached[0] < GIPHY_CACHE_SECONDS and len(cached[1]) >= min(limit, 20):
        return cached[1][:limit]
    try:
        queries = GIPHY_QUERY_VARIANTS.get(
            command,
            (GIPHY_QUERY.get(command, command or "anime reaction"),),
        )
        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        async with ipv4_async_client(timeout=20, follow_redirects=True) as client:
            for raw_query in queries:
                query = raw_query
                if "anime" not in query.lower():
                    query = f"anime {query}"
                if "gif" not in query.lower():
                    query = f"{query} gif"
                response = await client.get(
                    "https://api.giphy.com/v1/gifs/search",
                    params={
                        "api_key": api_key,
                        "q": query,
                        "limit": max(limit, GIPHY_POOL_LIMIT),
                        "rating": "pg-13",
                        "lang": "en",
                    },
                )
                response.raise_for_status()
                for item in response.json().get("data", []):
                    item_id = str(item.get("id") or "")
                    if item_id and item_id in seen_ids:
                        continue
                    metadata = " ".join(
                        str(item.get(field) or "")
                        for field in ("title", "slug", "username")
                    ).lower()
                    tags = item.get("tags", [])
                    metadata += " " + " ".join(
                        str(tag.get("name") if isinstance(tag, dict) else tag or "")
                        for tag in tags
                    ).lower()
                    # Require an exact action match for known bot actions. This
                    # prevents /kiss, /hug, /slap, etc. from receiving a merely
                    # anime-related but unrelated reaction.
                    action_keywords = ACTION_GIF_KEYWORDS.get(command)
                    if action_keywords and not any(
                        re.search(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", metadata)
                        for keyword in action_keywords
                    ):
                        continue
                    if any(term in metadata for term in GIF_BLOCKLIST):
                        continue
                    images = item.get("images", {})
                    gif = images.get("fixed_height") or images.get("original") or {}
                    thumbnail = images.get("fixed_height_small") or gif
                    gif_url = gif.get("url")
                    thumbnail_url = thumbnail.get("url") or gif_url
                    if gif_url and thumbnail_url:
                        if item_id:
                            seen_ids.add(item_id)
                        records.append(
                            {
                                "id": item_id or str(len(records)),
                                "gif_url": gif_url,
                                "thumbnail_url": thumbnail_url,
                                "width": int(gif.get("width") or 0) or None,
                                "height": int(gif.get("height") or 0) or None,
                            }
                        )
                if len(records) >= GIPHY_POOL_LIMIT:
                    break
        if records:
            GIPHY_POOL_CACHE[command] = (time.time(), records)
        return records[:limit]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        log.info("GIPHY search unavailable for %s: %s", command, type(exc).__name__)
        return []


async def giphy_animation(command: str) -> io.BytesIO | None:
    """Fetch one GIF from GIPHY without ever logging or returning the API key."""
    records = await giphy_search(command, limit=GIPHY_POOL_LIMIT)
    if not records:
        return None
    recent = GIPHY_RECENT_IDS.setdefault(command, deque(maxlen=20))
    available = [record for record in records if record["id"] not in recent] or records
    random.shuffle(available)
    try:
        async with ipv4_async_client(timeout=20, follow_redirects=True) as client:
            for record in available[:8]:
                try:
                    media = await client.get(record["gif_url"])
                    media.raise_for_status()
                    recent.append(record["id"])
                    animation = io.BytesIO(media.content)
                    animation.name = f"zara-{command}.gif"
                    return animation
                except (httpx.HTTPError, TypeError, ValueError):
                    continue
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        log.info("GIPHY request unavailable for %s: %s", command, type(exc).__name__)
    return None


async def capture_owner_gif(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Temporary DM capture for replacing bundled action animations."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    media = (
        getattr(message, "animation", None)
        or getattr(message, "video", None)
        or getattr(message, "document", None)
    )
    caption = (getattr(message, "caption", None) or "").strip().lower()
    words = re.findall(r"[a-z]+", caption)
    command = next((word for word in words if word in ACTION_NAMES), "")
    log.info(
        "Private DM user=%s text=%s caption=%r command=%s animation=%s video=%s document=%s sticker=%s",
        user.id,
        bool(getattr(message, "text", None)),
        caption,
        command,
        bool(getattr(message, "animation", None)),
        bool(getattr(message, "video", None)),
        bool(getattr(message, "document", None)),
        bool(getattr(message, "sticker", None)),
    )
    if not media:
        return
    if not command and not owner(user.id):
        return
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    if len(list(CAPTURE_DIR.iterdir())) >= 20:
        return
    mime = (getattr(media, "mime_type", "") or "").lower()
    extension = ".gif" if "gif" in mime else ".mp4"
    name = command or f"incoming-{user.id}-{message.message_id}"
    path = CAPTURE_DIR / f"{name}{extension}"
    try:
        telegram_file = await context.bot.get_file(media.file_id)
        await telegram_file.download_to_drive(custom_path=path)
    except TelegramError as exc:
        log.warning("GIF capture failed for %s: %s", name, type(exc).__name__)
        await safe_reply(update, f"❌ ғᴀɪʟᴇᴅ ᴛᴏ sᴀᴠᴇ {name}.")
        return
    await safe_reply(
        update,
        f"✅ sᴀᴠᴇᴅ {name}{extension} ({path.stat().st_size:,} ʙʏᴛᴇs).",
    )


async def sticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    packs: dict[str, tuple[str, ...]] = context.application.bot_data.get(
        "sticker_packs", {}
    )
    if not packs:
        await safe_reply(
            update,
            "🙃 ᴀʙʜɪ ᴋᴏɪ sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋ ʟᴏᴀᴅ ɴᴀʜɪ ʜᴜᴀ. ᴛʜᴏᴅɪ ᴅᴇʀ ʙᴀᴀᴅ /sticker ᴛʀʏ ᴋᴀʀᴏ.",
        )
        return
    requested = context.args[0].lstrip("@") if context.args else ""
    if requested:
        selected = next(
            ((name, stickers) for name, stickers in packs.items() if name.lower() == requested.lower()),
            None,
        )
        if not selected:
            await safe_reply(update, "❌ ᴘᴀᴄᴋ ɴᴏᴛ ғᴏᴜɴᴅ. ᴜsᴇ /stickerpack ᴛᴏ sᴇᴇ ᴛʜᴇ ʟɪsᴛ.")
            return
        pack_name, stickers = selected
    else:
        pack_name, stickers = random.choice(list(packs.items()))
    await send_sticker(update.effective_message, pack_name, stickers)


async def send_sticker(message: Any, pack_name: str, stickers: tuple[str, ...]) -> bool:
    if not stickers:
        return False
    try:
        await message.reply_sticker(random.choice(stickers))
        log.debug("Sent sticker from pack %s", pack_name)
        return True
    except TelegramError as exc:
        log.info("Sticker send failed for %s: %s", pack_name, type(exc).__name__)
        return False


async def automatic_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Send an occasional sticker when a normal message clearly asks for one."""
    text = (update.message.text or "").lower()
    if not any(trigger in text for trigger in AUTO_STICKER_TRIGGERS):
        return False
    chat_id = update.effective_chat.id if update.effective_chat else update.effective_user.id
    cooldowns = context.application.bot_data.setdefault("sticker_cooldowns", {})
    last_sent = int(cooldowns.get(chat_id, 0))
    if now() - last_sent < AUTO_STICKER_COOLDOWN:
        return True
    packs: dict[str, tuple[str, ...]] = context.application.bot_data.get("sticker_packs", {})
    available = [(name, stickers) for name, stickers in packs.items() if stickers]
    if not available:
        return True
    pack_name, stickers = random.choice(available)
    if await send_sticker(update.message, pack_name, stickers):
        cooldowns[chat_id] = now()
    return True


async def sticker_packs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    packs: dict[str, tuple[str, ...]] = context.application.bot_data.get(
        "sticker_packs", {}
    )
    failed = context.application.bot_data.get("sticker_failed", ())
    lines = ["🎨 <b>ᴢᴀʀᴀ sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋs</b>", "ᴜsᴇ /sticker ᴏʀ /sticker pack_name", ""]
    for index, (name, stickers) in enumerate(packs.items(), 1):
        lines.append(f"{index}. <code>{esc(name)}</code> — {len(stickers)} stickers")
    if failed:
        lines.append(f"\n⚠️ {len(failed)} ᴘᴀᴄᴋ(s) ᴡᴇʀᴇ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ᴛᴏ ᴛʜᴇ ʙᴏᴛ.")
    await safe_reply(
        update,
        "\n".join(lines) if packs else "🎨 ɴᴏ sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋs ᴀʀᴇ ᴀᴠᴀɪʟᴀʙʟᴇ ʏᴇᴛ.",
    )


async def action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw_command = (update.effective_message.text or "/action").split()[0].lstrip("/")
    command = raw_command.split("@", 1)[0].lower()
    if command not in ACTION_NAMES:
        command = "love"
    if not update.effective_message.reply_to_message and not context.args:
        await safe_reply(update, f"ʀᴇᴘʟʏ ᴛᴏ sᴏᴍᴇᴏɴᴇ ᴛᴏ {command} ᴛʜᴇᴍ.")
        return
    target_id, target = await target_user(update, context.args)
    if not target:
        await safe_reply(update, f"ʀᴇᴘʟʏ ᴛᴏ sᴏᴍᴇᴏɴᴇ ᴛᴏ {command} ᴛʜᴇᴍ.")
        return
    actor = mention_styled(update.effective_user)
    target_name = mention_row_styled(target)
    level = pair_value(update.effective_user.id, target_id)
    phrases = {
        "kiss": f"💋 {actor} {styled('kissed')} {target_name}. {styled('cuteee!')}",
        "hug": f"🤗 {actor} {styled('gave')} {target_name} {styled('a warm hug.')}",
        "slap": f"🫲 {actor} {styled('slapped')} {target_name}.",
        "punch": f"🥊 {actor} {styled('punched')} {target_name}.",
        "murder": f"🔪 {actor} {styled('did a dramatic murder scene with')} {target_name}.",
        "bite": f"🧛 {actor} {styled('bit')} {target_name}.",
        "crush": (
            f"💘 {target_name}{styled(chr(39) + 's secret crush is')} {actor} 🫀🧿🎀\n"
            f"{styled('crush level:')} {level}% ❤️"
        ),
        "love": (
            f"❤️ {styled('love meter report')} ❤️\n"
            f"{actor} ❤️ {target_name}\n"
            f"{styled('love compatibility:')} {level}% ❤️"
        ),
        "pat": f"🌸 {actor} {styled('patted')} {target_name}.",
        "cuddle": f"🫂 {actor} {styled('cuddled')} {target_name}.",
        "dance": f"💃 {actor} {styled('danced with')} {target_name}.",
        "highfive": f"🙌 {actor} {styled('gave')} {target_name} {styled('a high five.')}",
        "bonk": f"🔨 {actor} {styled('bonked')} {target_name}.",
    }
    caption = (
        "<blockquote>"
        + phrases.get(command, f"💗 {actor} {styled('with')} {target_name}.")
        + "</blockquote>"
    )

    # Prefer the configured GIPHY source, then use the bundled animations.
    giphy = await giphy_animation(command)
    if giphy:
        try:
            await update.effective_message.reply_animation(
                giphy, caption=caption, parse_mode=ParseMode.HTML
            )
            STORE.db.execute(
                "INSERT INTO action_counters(action,count) VALUES(?,1) "
                "ON CONFLICT(action) DO UPDATE SET count=count+1",
                (command,),
            )
            STORE.db.commit()
            return
        except TelegramError:
            pass

    # Bundled animations are the primary path. This keeps the main feature
    # reliable when GIPHY is slow, rate-limited, or unavailable.
    local_candidates = GIF_ASSETS.get(command, GIF_ASSETS["love"])
    local_path = random.choice(local_candidates)
    if local_path.exists():
        try:
            with local_path.open("rb") as gif_file:
                await update.effective_message.reply_animation(
                    gif_file, caption=caption, parse_mode=ParseMode.HTML
                )
            STORE.db.execute(
                "INSERT INTO action_counters(action,count) VALUES(?,1) "
                "ON CONFLICT(action) DO UPDATE SET count=count+1",
                (command,),
            )
            STORE.db.commit()
            return
        except TelegramError as exc:
            log.warning("Bundled GIF failed for %s: %s", command, exc)

    # Do not use generic remote endpoints here: Zara promises anime-only GIFs.
    # If GIPHY and the bundled anime animation both fail, send the caption only.
    await safe_reply(update, caption)


async def ai_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: str = "",
    is_sticker: bool = False,
) -> None:
    """Reply to a normal message or sticker using the Zara persona."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or user.is_bot:
        return

    if is_sticker:
        packs: dict[str, tuple[str, ...]] = context.application.bot_data.get(
            "sticker_packs", {}
        )
        sticker = zara_ai.pick_sticker(packs)
        if not sticker:
            return
        try:
            await context.bot.send_chat_action(
                chat_id=chat.id, action=ChatAction.CHOOSE_STICKER
            )
        except TelegramError:
            pass
        try:
            await message.reply_sticker(sticker)
        except TelegramError as exc:
            log.info("Sticker reply failed: %s", type(exc).__name__)
        return

    if not text:
        return
    if not zara_ai.is_telegram_safe(text):
        await safe_reply(
            update, zara_ai.safe_refusal(zara_ai.detect_language(text))
        )
        return

    if not zara_ai.has_ai_key():
        if chat.type == ChatType.PRIVATE:
            await safe_reply(
                update,
                "AI key abhi set nahi hai, thoda owner ko tag karo 💗",
            )
        return

    replied_message = message.reply_to_message
    replied_user = getattr(replied_message, "from_user", None)
    replied_sender = ""
    replied_text = ""
    if replied_message and replied_user:
        replied_text = (replied_message.text or replied_message.caption or "").strip()
        if replied_user.id == context.bot.id:
            replied_sender = "Zara"
            replied_text = replied_text[:200]
        elif replied_text:
            replied_sender = (
                replied_user.first_name or replied_user.username or "someone"
            ).strip()[:30]
            replied_text = replied_text[:300]

    user_name = (user.first_name or user.username or "").strip()[:30] or "User"
    zara_ai.MEMORY.store(chat.id, user.id, "user", text)
    try:
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
    except TelegramError:
        pass

    result = await zara_ai.generate_reply(
        text=text,
        chat_id=chat.id,
        user_id=user.id,
        user_name=user_name,
        user_gender=zara_ai.detect_user_gender(text),
        replied_sender=replied_sender,
        replied_text=replied_text,
        length="short",
        max_tokens=120,
    )
    if not result:
        return

    zara_ai.MEMORY.store(chat.id, user.id, "assistant", result.text)
    try:
        await message.reply_text(styled(result.text))
    except TelegramError as exc:
        log.info("AI reply failed: %s", type(exc).__name__)


def parse_cards(text: str) -> int | None:
    letters = {"a": 0, "b": 1, "c": 2, "d": 3, "e": 4}
    for token in re.split(r"[\s,;/]+", text.lower()):
        if token in letters:
            return letters[token]
    return None


async def send_private_cards(context: ContextTypes.DEFAULT_TYPE, player_id: int, cards: list[int]) -> None:
    try:
        await context.bot.send_message(player_id, f"🎴 ʏᴏᴜʀ ᴢᴀʀᴀ ᴄᴀʀᴅs:\n" + "\n".join(f"{chr(65+i)}: {value}" for i, value in enumerate(cards)) + "\n\nᴜsᴇ /flip a/b/c/d/e ɪɴ ᴛʜᴇ ɢᴀᴍᴇ ᴄʜᴀᴛ.")
    except Forbidden:
        username = context.application.bot_data.get("username", "ZaraBot")
        log.info("Could not DM cards to %s; user has not started the bot", player_id)
        try:
            await context.bot.send_message(
                player_id,
                f"🎴 ᴘʟᴇᴀsᴇ sᴛᴀʀᴛ @{username} ғɪʀsᴛ sᴏ ᴢᴀʀᴀ ᴄᴀɴ sᴇɴᴅ ʏᴏᴜʀ ᴘʀɪᴠᴀᴛᴇ ᴄᴀʀᴅs.",
            )
        except TelegramError:
            pass


async def bet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_private(update):
        await safe_reply(update, "🎴 ᴀᴅᴅ ᴍᴇ ᴛᴏ ᴀ ɢʀᴏᴜᴘ ғᴏʀ ᴍᴜʟᴛɪᴘʟᴀʏᴇʀ ᴄᴀʀᴅs.")
        return
    chat_id = update.effective_chat.id
    if not games_are_open(chat_id):
        await safe_reply(update, "🔒 ɢᴀᴍᴇs ᴀʀᴇ ᴄʟᴏsᴇᴅ. ᴀɴ ᴀᴅᴍɪɴ ᴍᴜsᴛ ᴜsᴇ /open.")
        return
    amount = number_arg(update, 0)
    row = STORE.user(update.effective_user)
    if amount < 1:
        await safe_reply(update, "ᴜsᴇ /bet 2000 ᴛᴏ sᴛᴀʀᴛ ᴛʜᴇ ᴄᴀʀᴅ ʟᴏʙʙʏ.")
        return
    if not staff(update.effective_user.id) and row["balance"] < amount:
        await safe_reply(update, "❌ ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴇɴᴏᴜɢʜ ᴄᴏɪɴs.")
        return
    if STORE.game(chat_id):
        await safe_reply(update, "🎴 ᴀ ɢᴀᴍᴇ ɪs ᴀʟʀᴇᴀᴅʏ ᴏᴘᴇɴ. ᴜsᴇ /card ᴛᴏ ᴊᴏɪɴ.")
        return
    state = {"phase": "lobby", "bet": amount, "players": {}, "round": 0, "turn": 0, "pot": 0}
    state["players"][str(update.effective_user.id)] = {
        "name": update.effective_user.full_name,
        "cards": [],
        "used": [],
        "score": 0,
        "choice": None,
    }
    STORE.save_game(chat_id, state)
    await safe_reply(update, f"💰 <b>ʙᴇᴛ: ${amount:,}</b>\n🎴 ᴄᴀʀᴅ ɢᴀᴍᴇ ʟᴏʙʙʏ sᴛᴀʀᴛᴇᴅ ʙʏ {mention(update.effective_user)}.\n\nᴏᴛʜᴇʀs: ᴜsᴇ /card ᴛᴏ ᴊᴏɪɴ.\nᴍɪɴɪᴍᴜᴍ 2 ᴘʟᴀʏᴇʀs.")


async def card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_private(update):
        await safe_reply(update, "🎴 ᴊᴏɪɴ ᴀ ɢʀᴏᴜᴘ ᴄᴀʀᴅ ɢᴀᴍᴇ ᴜsɪɴɢ /card ɪɴ ᴛʜᴇ ɢʀᴏᴜᴘ.")
        return
    chat_id = update.effective_chat.id
    state = STORE.game(chat_id)
    if not state:
        await safe_reply(update, "ᴘᴇʜʟᴇ /bet 2000 ᴋᴀʀᴏ, ᴘʜɪʀ /card.")
        return
    uid = str(update.effective_user.id)
    if state["phase"] != "lobby":
        await safe_reply(update, "🎴 ɢᴀᴍᴇ ᴄʜᴀʟ ʀᴀʜᴀ ʜᴀɪ — ᴛᴜʀɴ ᴘᴇ /flip a ᴋᴀʀᴏ.")
        return
    if uid not in state["players"]:
        row = STORE.user(update.effective_user)
        if not staff(update.effective_user.id) and row["balance"] < state["bet"]:
            await safe_reply(update, f"❌ ᴊᴏɪɴ ᴋᴀʀɴᴇ ᴋᴇ ʟɪʏᴇ ${state['bet']:,} ᴄʜᴀʜɪʏᴇ.")
            return
        state["players"][uid] = {
            "name": update.effective_user.full_name,
            "cards": [],
            "used": [],
            "score": 0,
            "choice": None,
        }
    if len(state["players"]) < 2:
        STORE.save_game(chat_id, state)
        await safe_reply(update, "✅ ᴛᴜᴍ ʟᴏʙʙʏ ᴍᴇ ʜᴏ. ᴋɪsɪ ᴀᴜʀ ᴋᴇ /card ᴋᴀ ᴡᴀɪᴛ ᴋᴀʀᴏ.")
        return
    player_ids = list(state["players"].keys())
    if state["phase"] == "lobby":
        # Validate every player before touching any balance. This prevents a
        # late joiner from making the whole pot inconsistent.
        for player_id in player_ids:
            player_row = STORE.db.execute(
                "SELECT balance FROM users WHERE user_id=?", (int(player_id),)
            ).fetchone()
            if not staff(int(player_id)) and (
                not player_row or int(player_row["balance"]) < state["bet"]
            ):
                removed_name = state["players"][player_id]["name"]
                state["players"].pop(player_id, None)
                STORE.save_game(chat_id, state)
                await safe_reply(
                    update,
                    f"❌ {esc(removed_name)} "
                    f"ɴᴇᴇᴅs ${state['bet']:,} ᴛᴏ ᴊᴏɪɴ. ᴛʜᴇʏ ᴡᴇʀᴇ ʀᴇᴍᴏᴠᴇᴅ ғʀᴏᴍ ᴛʜᴇ ʟᴏʙʙʏ."
                )
                return
        state["phase"] = "playing"
        state["round"] = 1
        state["turn"] = 0
        try:
            with STORE.transaction():
                for player_id, player in state["players"].items():
                    player["cards"] = [random.randint(1, 9) for _ in range(5)]
                    player["used"] = []
                    player["choice"] = None
                    if staff(int(player_id)):
                        continue
                    if not STORE.debit(int(player_id), state["bet"]):
                        raise InsufficientBalanceError(
                            "player balance changed while starting the card game"
                        )
                    state["pot"] += state["bet"]
                STORE.save_game(chat_id, state)
        except InsufficientBalanceError:
            await safe_reply(
                update,
                "❌ ᴀ ᴘʟᴀʏᴇʀ's ʙᴀʟᴀɴᴄᴇ ᴄʜᴀɴɢᴇᴅ. ᴄᴀʀᴅ ɢᴀᴍᴇ ɴᴏᴛ sᴛᴀʀᴛᴇᴅ.",
            )
            return
        for player_id, player in state["players"].items():
            await send_private_cards(context, int(player_id), player["cards"])
    current_id = player_ids[state["turn"] % len(player_ids)]
    await safe_reply(update, f"✅ <b>ʀᴏᴜɴᴅ {state['round']} sᴛᴀʀᴛᴇᴅ</b>\n👉 ᴛᴜʀɴ: <a href=\"tg://user?id={current_id}\">{esc(state['players'][current_id]['name'])}</a>\n🎴 ᴅᴍ ᴍᴇ ᴍɪʟᴇ ᴄᴀʀᴅs ᴍᴇ sᴇ /flip a/b/c/d/e")


async def flip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_private(update):
        await safe_reply(update, "ᴊᴏɪɴ ᴛʜᴇ ɢʀᴏᴜᴘ ɢᴀᴍᴇ ғɪʀsᴛ.")
        return
    chat_id = update.effective_chat.id
    state = STORE.game(chat_id)
    callback_data = update.callback_query.data if update.callback_query else ""
    card_text = callback_data.split(":", 1)[1] if callback_data.startswith("flip:") else (update.effective_message.text or "")[5:]
    index = parse_cards(card_text)
    if not state or state.get("phase") != "playing" or index is None:
        await safe_reply(update, "ᴜsᴇ /flip a, b, c, d ᴏʀ e ᴅᴜʀɪɴɢ ᴀ ɢᴀᴍᴇ.")
        return
    ids = list(state["players"])
    current_id = ids[state["turn"] % len(ids)]
    if str(update.effective_user.id) != current_id:
        await safe_reply(update, f"⏳ ᴛᴜʀɴ ᴋᴀ ᴡᴀɪᴛ ᴋᴀʀᴏ: {esc(state['players'][current_id]['name'])}.")
        return
    player = state["players"][current_id]
    if player["choice"] is not None:
        await safe_reply(update, "ᴛᴜᴍ ɪs ʀᴏᴜɴᴅ ᴍᴇ ᴄᴀʀᴅ ᴄʜᴏᴏsᴇ ᴋᴀʀ ᴄʜᴜᴋᴇ ʜᴏ.")
        return
    if index in player.get("used", []):
        await safe_reply(update, "ᴛʜɪs ᴄᴀʀᴅ ᴘᴇʜʟᴇ ʜɪ ᴜsᴇ ʜᴏ ᴄᴜᴋᴀ ʜᴀɪ. ᴋᴏɪ ᴀᴜʀ ᴄᴀʀᴅ ᴄʜᴏᴏsᴇ ᴋᴀʀᴏ.")
        return
    player["choice"] = index
    player.setdefault("used", []).append(index)
    value = player["cards"][index]
    state["turn"] += 1
    await safe_reply(update, f"🎴 {mention(update.effective_user)} ➜ <b>{value}</b>")
    if any(p["choice"] is None for p in state["players"].values()):
        STORE.save_game(chat_id, state)
        next_id = ids[state["turn"] % len(ids)]
        await update.effective_message.reply_text(f"👉 ᴀʙ {mention(type('U', (), {'id': int(next_id), 'full_name': state['players'][next_id]['name']})())} ɪᴛ's ʏᴏᴜʀ ᴛᴜʀɴ.\n🎴 /flip a/b/c/d/e", parse_mode=ParseMode.HTML)
        return
    values = [p["cards"][p["choice"]] for p in state["players"].values()]
    high = max(values)
    winners = [uid for uid, p in state["players"].items() if p["cards"][p["choice"]] == high]
    points = high * len(winners)
    for uid in winners:
        state["players"][uid]["score"] += points
    if state["round"] >= 5:
        winner_score = max(p["score"] for p in state["players"].values())
        final_winners = [uid for uid, p in state["players"].items() if p["score"] == winner_score]
        share = state["pot"] // len(final_winners)
        lines = ["👑 <b>ғɪɴᴀʟ ᴡɪɴɴᴇʀ(s)</b> 👑"]
        with STORE.transaction():
            for uid in final_winners:
                if not staff(int(uid)):
                    STORE.add_money(int(uid), share)
                synthetic_user = type(
                    "SyntheticUser",
                    (),
                    {"id": int(uid), "username": None, "full_name": state["players"][uid]["name"]},
                )()
                current_row = STORE.user(synthetic_user)
                STORE.update(int(uid), xp=int(current_row["xp"]) + 100)
                lines.append(f"👤 {esc(state['players'][uid]['name'])} — 🎯 {state['players'][uid]['score']} • 💰 ${share:,}")
            STORE.save_game(chat_id, None)
        await safe_reply(update, "\n".join(lines))
        return
    state["round"] += 1
    for p in state["players"].values():
        p["choice"] = None
    state["turn"] = 0
    STORE.save_game(chat_id, state)
    await safe_reply(update, f"✅ ʀᴏᴜɴᴅ {state['round']}\n🏆 ʀᴏᴜɴᴅ ᴡɪɴɴᴇʀ(s): {', '.join(esc(state['players'][uid]['name']) for uid in winners)}\n🎴 ɴᴇxᴛ ʀᴏᴜɴᴅ — {esc(state['players'][ids[0]]['name'])} ɪs ғɪʀsᴛ.")


def hack_chat_id(update: Update) -> int | None:
    if not update.effective_chat or update.effective_chat.type == ChatType.PRIVATE:
        return None
    return int(update.effective_chat.id)


def hack_usage() -> str:
    return (
        "🔐 <b>ᴢᴀʀᴀ ʜᴀᴄᴋ ɢᴀᴍᴇ</b>\n\n"
        f"{DIVIDER}\n"
        "<blockquote>"
        "▫️ /hack [reward] [digits] — start a 3–6 digit game\n"
        "🔹 /register [amount] coins|gems — join the game\n"
        "▪️ /guess [password] — check your guess\n"
        "🔸 /end — host only, end and refund the game"
        "</blockquote>\n"
        f"{DIVIDER}\n"
        "ᴜsᴇ ᴜɴɪǫᴜᴇ ᴅɪɢɪᴛs. <b>ʜᴀᴄᴋs</b> ᴀʀᴇ ʀɪɢʜᴛ ᴘᴏsɪᴛɪᴏɴs; "
        "<b>ɢʟɪᴛᴄʜᴇs</b> ᴀʀᴇ ᴅɪɢɪᴛs ɪɴ ᴛʜᴇ ᴡʀᴏɴɢ ᴘᴏsɪᴛɪᴏɴ."
    )


def hack_public_state(state: dict[str, Any]) -> str:
    players = state.get("players", {})
    return (
        f"🎯 <b>ʀᴇᴡᴀʀᴅ:</b> ${int(state['reward']):,}\n"
        f"🔢 <b>ᴘᴀssᴡᴏʀᴅ ʟᴇɴɢᴛʜ:</b> {int(state['length'])}\n"
        f"👥 <b>ʀᴇɢɪsᴛᴇʀᴇᴅ:</b> {len(players)}\n"
        "ᴜsᴇ /guess [pass] ᴛᴏ ᴛʀʏ ʏᴏᴜʀ ʟᴜᴄᴋ."
    )


def hack_score(password: str, guess: str) -> tuple[int, int]:
    hacks = sum(expected == actual for expected, actual in zip(password, guess))
    remaining_password = [digit for index, digit in enumerate(password) if password[index] != guess[index]]
    remaining_guess = [digit for index, digit in enumerate(guess) if password[index] != guess[index]]
    glitches = sum(
        min(remaining_password.count(digit), remaining_guess.count(digit))
        for digit in set(remaining_guess)
    )
    return hacks, glitches


async def hack_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = hack_chat_id(update)
    if chat_id is None:
        await safe_reply(update, "🔐 ʜᴀᴄᴋ ɢᴀᴍᴇs ᴡᴏʀᴋ ɪɴ ɢʀᴏᴜᴘs. ᴀᴅᴅ ᴢᴀʀᴀ ᴛᴏ ᴀ ɢʀᴏᴜᴘ ᴛᴏ ᴘʟᴀʏ.")
        return
    if not games_are_open(chat_id):
        await safe_reply(update, "🔒 ɢᴀᴍᴇs ᴀʀᴇ ᴄʟᴏsᴇᴅ ɪɴ ᴛʜɪs ᴄʜᴀᴛ.")
        return
    if STORE.hack_game(chat_id):
        await safe_reply(update, "⚠️ ᴀ ʜᴀᴄᴋ ɢᴀᴍᴇ ɪs ᴀʟʀᴇᴀᴅʏ ʀᴜɴɴɪɴɢ.\n\n" + hack_usage())
        return
    if len(context.args) != 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await safe_reply(update, hack_usage(), reply_markup=category_keyboard())
        return
    reward = int(context.args[0])
    length = int(context.args[1])
    if reward < 1 or not 3 <= length <= 6:
        await safe_reply(update, "❌ ʀᴇᴡᴀʀᴅ ᴍᴜsᴛ ʙᴇ ᴘᴏsɪᴛɪᴠᴇ ᴀɴᴅ ᴘᴀssᴡᴏʀᴅ ʟᴇɴɢᴛʜ ᴍᴜsᴛ ʙᴇ 3–6.")
        return
    host = STORE.user(update.effective_user)
    if int(host["balance"]) < reward:
        await safe_reply(update, f"❌ ʏᴏᴜ ɴᴇᴇᴅ ${reward:,} ᴛᴏ ʜᴏsᴛ ᴛʜɪs ɢᴀᴍᴇ.")
        return
    password = "".join(random.sample("0123456789", length))
    state = {
        "host_id": int(update.effective_user.id),
        "host_name": display_name(update.effective_user),
        "reward": reward,
        "length": length,
        "password": password,
        "players": {},
        "entries": [],
    }
    with STORE.transaction():
        funded = STORE.debit(update.effective_user.id, reward)
        if funded:
            STORE.save_hack_game(chat_id, state)
    if not funded:
        await safe_reply(update, f"❌ ʏᴏᴜ ɴᴇᴇᴅ ${reward:,} ᴛᴏ ʜᴏsᴛ ᴛʜɪs ɢᴀᴍᴇ.")
        return
    await safe_reply(
        update,
        "🔐 <b>ʜᴀᴄᴋ ɢᴀᴍᴇ sᴛᴀʀᴛᴇᴅ!</b>\n\n"
        f"👑 ʜᴏsᴛ: {esc(state['host_name'])}\n"
        f"🎯 ʀᴇᴡᴀʀᴅ: <b>${reward:,}</b>\n"
        f"🔢 ᴘᴀssᴡᴏʀᴅ ʟᴇɴɢᴛʜ: <b>{length}</b>\n\n"
        f"ᴜsᴇ <code>/register {reward} coins</code> ᴏʀ <code>/register {reward} gems</code> ᴛᴏ ᴊᴏɪɴ.\n"
        "ᴛʜᴇ ᴘᴀssᴡᴏʀᴅ ɪs ʜɪᴅᴅᴇɴ. ɢᴏᴏᴅ ʟᴜᴄᴋ! 💜",
    )


async def hack_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = hack_chat_id(update)
    if chat_id is None:
        await safe_reply(update, "🔐 ᴜsᴇ /register ɪɴ ᴀ ɢʀᴏᴜᴘ ᴡɪᴛʜ ᴀ ʜᴀᴄᴋ ɢᴀᴍᴇ.")
        return
    state = STORE.hack_game(chat_id)
    if not state:
        await safe_reply(update, "❌ ɴᴏ ʜᴀᴄᴋ ɢᴀᴍᴇ ɪs ʀᴜɴɴɪɴɢ. ᴜsᴇ /hack [reward] [digits] ғɪʀsᴛ.")
        return
    if len(context.args) != 2 or not context.args[0].isdigit() or context.args[1].lower() not in {"coins", "coin", "gems", "gem"}:
        await safe_reply(update, "ᴜsᴇ: <code>/register amount coins</code> ᴏʀ <code>/register amount gems</code>.")
        return
    user_id = int(update.effective_user.id)
    if str(user_id) in state["players"]:
        await safe_reply(update, "✅ ʏᴏᴜ ᴀʀᴇ ᴀʟʀᴇᴀᴅʏ ʀᴇɢɪsᴛᴇʀᴇᴅ.")
        return
    amount = int(context.args[0])
    currency = "gems" if context.args[1].lower().startswith("gem") else "coins"
    if amount != int(state["reward"]):
        await safe_reply(
            update,
            f"❌ ᴛʜɪs ɢᴀᴍᴇ ʀᴇǫᴜɪʀᴇs ᴇxᴀᴄᴛʟʏ {int(state['reward']):,} {currency}.",
        )
        return
    row = STORE.user(update.effective_user)
    balance = int(row["gems"] if currency == "gems" else row["balance"])
    if not staff(user_id) and balance < amount:
        await safe_reply(update, f"❌ ʏᴏᴜ ɴᴇᴇᴅ {amount:,} {currency}.")
        return
    outcome = "ok"
    with STORE.transaction():
        latest = STORE.hack_game(chat_id)
        if latest is None:
            outcome = "no_game"
        else:
            state = latest
            if not staff(user_id):
                if currency == "gems":
                    paid = STORE.debit_gems(user_id, amount)
                else:
                    paid = STORE.debit(user_id, amount)
            else:
                paid = True
            if not paid:
                outcome = "insufficient"
            else:
                state["players"][str(user_id)] = {
                    "name": display_name(update.effective_user),
                    "amount": amount,
                    "currency": currency,
                }
                STORE.save_hack_game(chat_id, state)
    if outcome == "no_game":
        await safe_reply(update, "❌ ɴᴏ ʜᴀᴄᴋ ɢᴀᴍᴇ ɪs ʀᴜɴɴɪɴɢ. ᴜsᴇ /hack [reward] [digits] ғɪʀsᴛ.")
        return
    if outcome == "insufficient":
        await safe_reply(update, f"❌ ʏᴏᴜ ɴᴇᴇᴅ {amount:,} {currency}.")
        return
    await safe_reply(
        update,
        f"✅ <b>{esc(display_name(update.effective_user))}</b> ᴊᴏɪɴᴇᴅ ᴛʜᴇ ʜᴀᴄᴋ ɢᴀᴍᴇ!\n"
        f"💳 ᴇɴᴛʀʏ: {amount:,} {currency}\n\n{hack_public_state(state)}",
    )


async def hack_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = hack_chat_id(update)
    state = STORE.hack_game(chat_id) if chat_id is not None else None
    if not state:
        await safe_reply(update, "❌ ɴᴏ ʜᴀᴄᴋ ɢᴀᴍᴇ ɪs ʀᴜɴɴɪɴɢ.")
        return
    user_id = str(update.effective_user.id)
    if user_id not in state["players"]:
        await safe_reply(update, "🔒 ʀᴇɢɪsᴛᴇʀ ғɪʀsᴛ ᴡɪᴛʜ /register.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await safe_reply(update, "ᴜsᴇ: <code>/guess 12345</code>.")
        return
    guess = context.args[0]
    if len(guess) != int(state["length"]) or len(set(guess)) != len(guess):
        await safe_reply(update, f"❌ ᴇɴᴛᴇʀ ᴇxᴀᴄᴛʟʏ {state['length']} ᴜɴɪǫᴜᴇ ᴅɪɢɪᴛs.")
        return
    hacks, glitches = hack_score(str(state["password"]), guess)
    if hacks == int(state["length"]):
        with STORE.transaction():
            latest = STORE.hack_game(chat_id)
            if latest is None:
                won = False
            else:
                won = True
                state = latest
                if not staff(update.effective_user.id):
                    STORE.add_money(update.effective_user.id, int(state["reward"]))
                STORE.save_hack_game(chat_id, None)
        if not won:
            await safe_reply(update, "❌ ᴛʜɪs ʜᴀᴄᴋ ɢᴀᴍᴇ ʜᴀs ᴀʟʀᴇᴀᴅʏ ᴇɴᴅᴇᴅ.")
            return
        await safe_reply(
            update,
            f"🎉 <b>{esc(display_name(update.effective_user))} ᴄʀᴀᴄᴋᴇᴅ ᴛʜᴇ ᴘᴀssᴡᴏʀᴅ!</b>\n"
            f"🔑 ᴛʜᴇ ᴘᴀssᴡᴏʀᴅ ᴡᴀs <code>{esc(state['password'])}</code>\n"
            f"💰 ʏᴏᴜ ᴡᴏɴ <b>${int(state['reward']):,}</b>! 🏆",
        )
        return
    await safe_reply(
        update,
        f"🔎 <b>ʜᴀᴄᴋs:</b> {hacks}\n"
        f"✨ <b>ɢʟɪᴛᴄʜᴇs:</b> {glitches}\n"
        "ᴋᴇᴇᴘ ɢᴜᴇssɪɴɢ — ʏᴏᴜ ᴄᴀɴ ᴄʀᴀᴄᴋ ɪᴛ!",
    )


async def hack_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = hack_chat_id(update)
    state = STORE.hack_game(chat_id) if chat_id is not None else None
    if not state:
        await safe_reply(update, "❌ ɴᴏ ʜᴀᴄᴋ ɢᴀᴍᴇ ɪs ʀᴜɴɴɪɴɢ.")
        return
    if int(state["host_id"]) != update.effective_user.id and not staff(update.effective_user.id):
        await safe_reply(update, "🔒 ᴏɴʟʏ ᴛʜᴇ ɢᴀᴍᴇ ʜᴏsᴛ ᴄᴀɴ ᴜsᴇ /end.")
        return
    with STORE.transaction():
        latest = STORE.hack_game(chat_id)
        if latest is None:
            ended = False
        else:
            ended = True
            state = latest
            STORE.add_money(int(state["host_id"]), int(state["reward"]))
            for player_id, entry in state.get("players", {}).items():
                if not staff(int(player_id)):
                    if entry["currency"] == "gems":
                        STORE.add_gems(int(player_id), int(entry["amount"]))
                    else:
                        STORE.add_money(int(player_id), int(entry["amount"]))
            STORE.save_hack_game(chat_id, None)
    if not ended:
        await safe_reply(update, "❌ ᴛʜɪs ʜᴀᴄᴋ ɢᴀᴍᴇ ʜᴀs ᴀʟʀᴇᴀᴅʏ ᴇɴᴅᴇᴅ.")
        return
    await safe_reply(update, "🛑 ʜᴀᴄᴋ ɢᴀᴍᴇ ᴇɴᴅᴇᴅ. ʀᴇᴡᴀʀᴅs ᴀɴᴅ ᴇɴᴛʀɪᴇs ᴡᴇʀᴇ ʀᴇғᴜɴᴅᴇᴅ.")


async def close_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_group_admin(update, context):
        await safe_reply(update, "❌ ᴏɴʟʏ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs.")
        return
    chat = STORE.chat(update.effective_chat.id, update.effective_chat.title or "")
    command = (update.effective_message.text or "").split()[0].lower()
    is_open = command.endswith("open")
    STORE.db.execute("UPDATE chats SET games_open=? WHERE chat_id=?", (1 if is_open else 0, update.effective_chat.id))
    STORE.db.commit()
    await safe_reply(update, "✅ ɢᴀᴍᴇs ᴀʀᴇ ᴏᴘᴇɴ." if is_open else "🔒 ɢᴀᴍᴇs ᴀʀᴇ ᴄʟᴏsᴇᴅ.")


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    buttons = verification_markup()
    if not UPDATE_CHANNEL_CHAT_ID:
        STORE.update(update.effective_user.id, verified_at=now())
        await safe_reply(
            update,
            "✅ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ɪs ɴᴏᴛ ʀᴇǫᴜɪʀᴇᴅ ʀɪɢʜᴛ ɴᴏᴡ. 🎁 ᴜsᴇ /daily ᴛᴏ ᴄʟᴀɪᴍ 2,000 ᴄᴏɪɴs.",
        )
        return
    try:
        member = await context.bot.get_chat_member(
            UPDATE_CHANNEL_CHAT_ID, update.effective_user.id
        )
        joined = member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        }
    except TelegramError:
        await safe_reply(
            update,
            "⚠️ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ɪs ᴛᴇᴍᴘᴏʀᴀʀɪʟʏ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ. ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.",
            reply_markup=buttons,
        )
        return
    if not joined:
        await safe_reply(
            update,
            f"🔒 ᴊᴏɪɴ {UPDATE_CHANNEL_URL} ᴛʜᴇɴ ᴛᴀᴘ <b>Open verification</b>.",
            reply_markup=buttons,
        )
        return
    STORE.update(update.effective_user.id, verified_at=now())
    if VERIFY_WEBAPP_URL:
        await safe_reply(
            update,
            "✅ ᴄʜᴀɴɴᴇʟ ᴍᴇᴍʙᴇʀsʜɪᴘ ᴄᴏɴғɪʀᴍᴇᴅ ᴀɴᴅ ᴠᴇʀɪғɪᴇᴅ.\n"
            "🎁 ᴜsᴇ /daily ᴛᴏ ᴄʟᴀɪᴍ 2,000 ᴄᴏɪɴs.",
            reply_markup=buttons,
        )
        return
    await safe_reply(
        update,
        "✅ ᴄʜᴀɴɴᴇʟ ᴠᴇʀɪғɪᴇᴅ!\n🎁 ᴜsᴇ /daily ᴛᴏ ᴄʟᴀɪᴍ 2,000 ᴄᴏɪɴs.",
    )


async def web_app_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the Mini App's request, then verify membership server-side."""
    web_app_data = getattr(update.effective_message, "web_app_data", None)
    if not web_app_data:
        return
    try:
        payload = json.loads(web_app_data.data or "{}")
    except (TypeError, ValueError):
        await safe_reply(update, "❌ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ᴅᴀᴛᴀ ɪs ɴᴏᴛ ᴠᴀʟɪᴅ.")
        return
    if payload.get("action") != "verify":
        await safe_reply(update, "❌ ᴜɴᴋɴᴏᴡɴ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ᴀᴄᴛɪᴏɴ.")
        return
    token = os.getenv("ZARA_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    signed_user_id = telegram_web_app_user_id(
        str(payload.get("init_data") or ""),
        token or "",
    )
    if not signed_user_id or signed_user_id != update.effective_user.id:
        await safe_reply(
            update,
            "❌ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ᴄᴏᴜʟᴅ ɴᴏᴛ ʙᴇ ᴛʀᴜsᴛᴇᴅ. ᴏᴘᴇɴ ᴛʜᴇ ᴍɪɴɪ ᴀᴘᴘ ғʀᴏᴍ ᴛʜᴇ ᴢᴀʀᴀ ʙᴏᴛ ᴀɢᴀɪɴ.",
        )
        return
    if not UPDATE_CHANNEL_CHAT_ID:
        joined = True
    else:
        try:
            member = await context.bot.get_chat_member(
                UPDATE_CHANNEL_CHAT_ID, update.effective_user.id
            )
            joined = member.status in {
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            }
        except TelegramError:
            await safe_reply(update, "⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴄʜᴇᴄᴋ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ. ᴛʀʏ ᴀɢᴀɪɴ.")
            return
    await safe_reply(
        update,
        "✅ ᴠᴇʀɪғɪᴇᴅ! ᴜsᴇ /daily ᴛᴏ ᴄʟᴀɪᴍ 2,000 ᴄᴏɪɴs."
        if joined
        else f"❌ ᴘʟᴇᴀsᴇ ᴊᴏɪɴ {UPDATE_CHANNEL_URL} ғɪʀsᴛ, ᴛʜᴇɴ ᴛᴀᴘ ᴠᴇʀɪғʏ ᴀɢᴀɪɴ.",
    )
    if joined:
        STORE.user(update.effective_user)
        STORE.update(update.effective_user.id, verified_at=now())


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id, row = await target_user(update, context.args)
    if user_id <= 0:
        await safe_reply(update, "❌ ᴜsᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ. ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇɪʀ ᴍᴇssᴀɢᴇ ᴏʀ ᴜsᴇ ᴀ ᴋɴᴏᴡɴ @ᴜsᴇʀɴᴀᴍᴇ.")
        return
    await safe_reply(update, f"🪪 ɪᴅ: <code>{user_id}</code>\n👤 ɴᴀᴍᴇ: {esc(row['name'] if row else str(user_id))}")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    if not await is_group_admin(update, context):
        await safe_reply(update, "❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏɴʟʏ.")
        return
    message = update.effective_message
    command = (
        (message.text or "").split()[0].lstrip("/").split("@", 1)[0].lower()
        if message and message.text
        else ""
    )
    args = list(context.args)
    reply = message.reply_to_message if message else None

    if command == "admins":
        try:
            admins = await chat.get_administrators()
            await safe_reply(
                update,
                "🛡️ <b>ᴀᴅᴍɪɴs</b>\n"
                + "\n".join(f"• {mention(admin.user)}" for admin in admins),
            )
        except TelegramError:
            await safe_reply(update, "❌ ɪ ᴄᴏᴜʟᴅ ɴᴏᴛ ʟᴏᴀᴅ ᴛʜᴇ ᴀᴅᴍɪɴ ʟɪsᴛ.")
        return

    if command in {"purge", "del"}:
        allowed, error = await bot_admin_check(context, chat, "can_delete_messages")
        if not allowed:
            await safe_reply(update, error)
            return
        if not reply:
            await safe_reply(update, "❌ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴍᴇssᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴅᴇʟᴇᴛᴇ.")
            return
        try:
            if command == "del":
                await reply.delete()
                await message.delete()
            else:
                start = min(reply.message_id, message.message_id)
                end = max(reply.message_id, message.message_id)
                if end - start > 1000:
                    await safe_reply(update, "❌ ᴛʜᴀᴛ ʀᴀɴɢᴇ ɪs ᴛᴏᴏ ʟᴀʀɢᴇ. ʀᴇᴘʟʏ ᴛᴏ ᴀ ɴᴇᴀʀᴇʀ ᴍᴇssᴀɢᴇ.")
                    return
                message_ids = list(range(start, end + 1))
                for offset in range(0, len(message_ids), 100):
                    chunk = message_ids[offset : offset + 100]
                    try:
                        await context.bot.delete_messages(chat.id, chunk)
                    except TelegramError:
                        for message_id in chunk:
                            try:
                                await context.bot.delete_message(chat.id, message_id)
                            except TelegramError:
                                pass
        except TelegramError:
            await safe_reply(update, "❌ ɪ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴅᴇʟᴇᴛᴇ ᴛʜᴏsᴇ ᴍᴇssᴀɢᴇs.")
        return

    if command == "pin":
        allowed, error = await bot_admin_check(context, chat, "can_pin_messages")
        if not allowed:
            await safe_reply(update, error)
            return
        if not reply:
            await safe_reply(update, "❌ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴍᴇssᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴘɪɴ.")
            return
        try:
            await reply.pin()
            await safe_reply(update, f"📌 ᴘɪɴɴᴇᴅ ᴍᴇssᴀɢᴇ <code>{reply.message_id}</code>.")
        except TelegramError as exc:
            log.warning("Pin failed: %s", type(exc).__name__)
            await safe_reply(update, "❌ ɪ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴘɪɴ ᴛʜᴀᴛ ᴍᴇssᴀɢᴇ.")
        return

    if command == "unpin":
        allowed, error = await bot_admin_check(context, chat, "can_pin_messages")
        if not allowed:
            await safe_reply(update, error)
            return
        try:
            if reply:
                await context.bot.unpin_chat_message(chat.id, message_id=reply.message_id)
                await safe_reply(update, f"📌 ᴜɴᴘɪɴɴᴇᴅ ᴍᴇssᴀɢᴇ <code>{reply.message_id}</code>.")
            else:
                await context.bot.unpin_chat_message(chat.id)
                await safe_reply(update, "📌 ᴜɴᴘɪɴɴᴇᴅ ᴛʜᴇ ʟᴀᴛᴇsᴛ ᴘɪɴɴᴇᴅ ᴍᴇssᴀɢᴇ.")
        except TelegramError:
            await safe_reply(update, "❌ ɪ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴜɴᴘɪɴ ᴛʜᴀᴛ ᴍᴇssᴀɢᴇ.")
        return

    if command in {"lock", "unlock"}:
        allowed, error = await bot_admin_check(context, chat, "can_restrict_members")
        if not allowed:
            await safe_reply(update, error)
            return
        try:
            permissions = (
                locked_permissions() if command == "lock" else chat_default_permissions(chat)
            )
            await context.bot.set_chat_permissions(chat.id, permissions)
            await safe_reply(
                update,
                "🔒 ɢʀᴏᴜᴘ ʟᴏᴄᴋᴇᴅ." if command == "lock" else "🔓 ɢʀᴏᴜᴘ ᴜɴʟᴏᴄᴋᴇᴅ.",
            )
        except TelegramError:
            await safe_reply(update, "❌ ɪ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴄʜᴀɴɢᴇ ᴛʜᴇ ɢʀᴏᴜᴘ ᴘᴇʀᴍɪssɪᴏɴs.")
        return

    duration: int | None = None
    target_args = args
    if command in {"tban", "tmute"}:
        duration, target_args, duration_error = split_duration_args(args, bool(reply))
        if duration_error:
            await safe_reply(update, duration_error)
            return

    if not reply and not target_args:
        await safe_reply(update, "❌ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴜsᴇ ᴛʜᴇɪʀ @ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.")
        return
    target_id, target = await target_user(update, target_args, allow_synthetic=True)
    if target_id <= 0 or not target:
        await safe_reply(update, "❌ ᴜsᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ. ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇɪʀ ᴍᴇssᴀɢᴇ ᴏʀ ᴜsᴇ ᴀ ᴠᴀʟɪᴅ @ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.")
        return

    member, target_error = await validate_moderation_target(
        context, chat, target_id, command, update.effective_user.id
    )
    if not member:
        await safe_reply(update, target_error)
        return
    status = member.status
    target_name = getattr(getattr(member, "user", None), "full_name", None) or target["name"]

    if command in {"ban", "tban", "kick", "mute", "tmute"} and status == ChatMemberStatus.BANNED:
        await safe_reply(update, f"ℹ️ {esc(target_name)} ɪs ᴀʟʀᴇᴀᴅʏ ʙᴀɴɴᴇᴅ.")
        return
    if command == "promote" and status == ChatMemberStatus.BANNED:
        await safe_reply(update, f"ℹ️ ᴜɴʙᴀɴ {esc(target_name)} ʙᴇғᴏʀᴇ ᴘʀᴏᴍᴏᴛɪɴɢ ᴛʜᴇᴍ.")
        return
    if command in {"ban", "tban", "kick", "mute", "tmute", "warn", "promote"} and status == ChatMemberStatus.LEFT:
        await safe_reply(update, f"ℹ️ {esc(target_name)} ɪs ɴᴏᴛ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.")
        return
    if command in {"mute", "tmute"} and status == ChatMemberStatus.RESTRICTED and not getattr(member, "can_send_messages", True):
        await safe_reply(update, f"ℹ️ {esc(target_name)} ɪs ᴀʟʀᴇᴀᴅʏ ᴍᴜᴛᴇᴅ.")
        return
    if command == "unban" and status != ChatMemberStatus.BANNED:
        await safe_reply(update, f"ℹ️ {esc(target_name)} ɪs ɴᴏᴛ ʙᴀɴɴᴇᴅ.")
        return
    if command == "unmute" and (
        status != ChatMemberStatus.RESTRICTED
        or bool(getattr(member, "can_send_messages", True))
    ):
        await safe_reply(update, f"ℹ️ {esc(target_name)} ɪs ɴᴏᴛ ᴍᴜᴛᴇᴅ.")
        return
    if command == "demote" and status != ChatMemberStatus.ADMINISTRATOR:
        await safe_reply(update, f"ℹ️ {esc(target_name)} ɪs ɴᴏᴛ ᴀ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ.")
        return

    permission = None
    if command in {"ban", "tban", "unban", "kick", "mute", "tmute", "unmute"}:
        permission = "can_restrict_members"
    elif command in {"promote", "demote"}:
        permission = "can_promote_members"
    if permission:
        allowed, error = await bot_admin_check(context, chat, permission)
        if not allowed:
            await safe_reply(update, error)
            return

    if command == "warn":
        with STORE.transaction():
            STORE.db.execute(
                "INSERT INTO warnings(chat_id,user_id,count) VALUES(?,?,1) "
                "ON CONFLICT(chat_id,user_id) DO UPDATE SET count=count+1",
                (chat.id, target_id),
            )
            warning = STORE.db.execute(
                "SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
                (chat.id, target_id),
            ).fetchone()
        chat_row = STORE.chat(chat.id, chat.title or "")
        settings = modcfg.load_settings(chat_row)
        limit = int(settings.get("warn_limit", 3))
        count = int(warning["count"])
        if count < limit:
            await safe_reply(
                update,
                f"<blockquote>⚠️ {styled('warned')} {mention_row_styled(target)}. "
                f"{styled('warnings:')} <b>{count}/{limit}</b>.</blockquote>",
            )
            return
        action = str(settings.get("warn_action", "mute"))
        if action not in {"mute", "kick", "ban"}:
            action = "mute"
        allowed, error = await bot_admin_check(context, chat, "can_restrict_members")
        if not allowed:
            await safe_reply(
                update,
                f"⚠️ ᴡᴀʀɴɪɴɢ ʀᴇᴄᴏʀᴅᴇᴅ ({count}/{limit}), ʙᴜᴛ ᴛʜᴇ {action} ᴀᴄᴛɪᴏɴ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴜɴ: {error}",
            )
            return
        try:
            if action == "ban":
                await context.bot.ban_chat_member(chat.id, target_id)
            elif action == "kick":
                await context.bot.ban_chat_member(chat.id, target_id)
                try:
                    await context.bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
                except TelegramError:
                    await safe_reply(
                        update,
                        f"⚠️ {esc(target_name)} ᴡᴀs ʙᴀɴɴᴇᴅ, ʙᴜᴛ ᴛʜᴇ ᴋɪᴄᴋ ᴜɴʙᴀɴ ғᴀɪʟᴇᴅ. ᴜsᴇ /unban {target_id}.",
                    )
                    return
            else:
                await context.bot.restrict_chat_member(
                    chat.id,
                    target_id,
                    ChatPermissions(can_send_messages=False),
                    until_date=now() + 600,
                )
        except TelegramError as exc:
            log.warning("Warning action failed: %s", type(exc).__name__)
            await safe_reply(
                update,
                f"⚠️ ᴡᴀʀɴɪɴɢ ʀᴇᴄᴏʀᴅᴇᴅ ({count}/{limit}), ʙᴜᴛ ᴛʜᴇ {action} ᴀᴄᴛɪᴏɴ ғᴀɪʟᴇᴅ.",
            )
            return
        STORE.db.execute(
            "DELETE FROM warnings WHERE chat_id=? AND user_id=?",
            (chat.id, target_id),
        )
        STORE.db.commit()
        await send_mod_log(
            context,
            chat,
            f"⚠️ {mention(member.user)} ʀᴇᴀᴄʜᴇᴅ {limit} ᴡᴀʀɴɪɴɢs → {action}.",
            settings,
        )
        await safe_reply(
            update,
            f"⚠️ {esc(target_name)} ʀᴇᴀᴄʜᴇᴅ {limit} ᴡᴀʀɴɪɴɢs. ᴀᴄᴛɪᴏɴ: <b>{action}</b>.",
        )
        return

    try:
        if command in {"ban", "tban"}:
            if duration:
                await context.bot.ban_chat_member(
                    chat.id, target_id, until_date=now() + duration
                )
            else:
                await context.bot.ban_chat_member(chat.id, target_id)
        elif command == "unban":
            await context.bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
        elif command == "kick":
            await context.bot.ban_chat_member(chat.id, target_id)
            try:
                await context.bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
            except TelegramError:
                await safe_reply(
                    update,
                    f"⚠️ {esc(target_name)} ᴡᴀs ʙᴀɴɴᴇᴅ, ʙᴜᴛ ᴛʜᴇ ᴋɪᴄᴋ ᴜɴʙᴀɴ ғᴀɪʟᴇᴅ. ᴜsᴇ /unban {target_id}.",
                )
                return
        elif command in {"mute", "tmute"}:
            kwargs = {"until_date": now() + duration} if duration else {}
            await context.bot.restrict_chat_member(
                chat.id,
                target_id,
                ChatPermissions(can_send_messages=False),
                **kwargs,
            )
        elif command == "unmute":
            await context.bot.restrict_chat_member(
                chat.id, target_id, permissions=chat_default_permissions(chat)
            )
        elif command == "promote":
            await context.bot.promote_chat_member(
                chat.id,
                target_id,
                can_manage_chat=True,
                can_delete_messages=True,
                can_restrict_members=True,
                can_invite_users=True,
                can_pin_messages=True,
            )
        elif command == "demote":
            await context.bot.promote_chat_member(
                chat.id,
                target_id,
                can_manage_chat=False,
                can_delete_messages=False,
                can_restrict_members=False,
                can_invite_users=False,
                can_pin_messages=False,
                can_change_info=False,
                can_promote_members=False,
                can_manage_topics=False,
                can_manage_video_chats=False,
            )
        else:
            await safe_reply(update, "❌ ᴜɴᴋɴᴏᴡɴ ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅ.")
            return
    except TelegramError as exc:
        log.warning("Moderation command failed: %s", type(exc).__name__)
        await safe_reply(
            update,
            f"❌ /{command} ғᴀɪʟᴇᴅ. ᴄʜᴇᴄᴋ ᴍʏ ᴀᴅᴍɪɴ ʀɪɢʜᴛs ᴀɴᴅ ᴛʜᴇ ᴛᴀʀɢᴇᴛ.",
        )
        return

    suffix = f" {styled('for')} {duration_text(duration)}" if duration else ""
    await safe_reply(
        update,
        f"<blockquote>✅ {styled('/' + command)} {styled('done for')} "
        f"{mention_row_styled(target)}{suffix}.</blockquote>",
    )


async def warnings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await safe_reply(update, "ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ᴡᴏʀᴋs ɪɴ ɢʀᴏᴜᴘs.")
        return
    target_id, target = await target_user(update, context.args)
    if target_id <= 0 or not target:
        await safe_reply(update, "❌ ᴜsᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ. ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇɪʀ ᴍᴇssᴀɢᴇ ᴏʀ ᴜsᴇ ᴀ ᴋɴᴏᴡɴ @ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.")
        return
    row = STORE.db.execute(
        "SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
        (update.effective_chat.id, target_id),
    ).fetchone()
    await safe_reply(
        update,
        f"⚠️ <b>ᴡᴀʀɴɪɴɢs ғᴏʀ {esc(target['name'])}</b>: {row['count'] if row else 0}",
    )


async def reset_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await safe_reply(update, "ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ᴡᴏʀᴋs ɪɴ ɢʀᴏᴜᴘs.")
        return
    if not await is_group_admin(update, context):
        await safe_reply(update, "❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏɴʟʏ.")
        return
    target_id, target = await target_user(update, context.args)
    if target_id <= 0 or not target:
        await safe_reply(update, "❌ ᴜsᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ. ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇɪʀ ᴍᴇssᴀɢᴇ ᴏʀ ᴜsᴇ ᴀ ᴋɴᴏᴡɴ @ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.")
        return
    STORE.db.execute(
        "DELETE FROM warnings WHERE chat_id=? AND user_id=?",
        (update.effective_chat.id, target_id),
    )
    STORE.db.commit()
    await safe_reply(update, f"✅ ᴡᴀʀɴɪɴɢs ʀᴇsᴇᴛ ғᴏʀ {esc(target['name'])}.")


async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id, row = await target_user(update, context.args)
    if not row:
        await safe_reply(update, "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴜsᴇ ᴀ ᴋɴᴏᴡɴ @ᴜsᴇʀɴᴀᴍᴇ.")
        return
    username = f"@{row['username']}" if row["username"] else "ɴᴏ ᴜsᴇʀɴᴀᴍᴇ"
    await safe_reply(
        update,
        f"🪪 <b>ᴜsᴇʀ ɪɴғᴏ</b>\n"
        f"👤 {esc(row['name'])}\n"
        f"🔗 {esc(username)}\n"
        f"🆔 <code>{user_id}</code>\n"
        f"🎭 ʀᴏʟᴇ: <b>{role(user_id)}</b>\n"
        f"📈 xᴘ: {row['xp']} • ⚔️ ᴋɪʟʟs: {row['kills']}",
    )


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = (update.inline_query.query or "").strip().lower()
    username = context.application.bot_data.get("username", "ZaraBot")
    requested = query.split()[0] if query else ""
    action_name = requested if requested in ACTION_NAMES else ""
    action_results: list[Any] = []
    if action_name:
        records = await giphy_search(action_name, limit=8)
        for index, record in enumerate(records):
            action_results.append(
                InlineQueryResultGif(
                    id=f"zara-{action_name}-{record['id']}-{index}",
                    gif_url=record["gif_url"],
                    thumbnail_url=record["thumbnail_url"],
                    gif_width=record["width"],
                    gif_height=record["height"],
                    title=f"{action_name.title()} GIF",
                    caption=(
                        f"💗 <b>{action_name.title()} reaction</b>\n"
                        f"Use <code>/{action_name} @username</code> in a group."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(
                        [[
                            InlineKeyboardButton(
                                f"🔁 More {action_name}",
                                switch_inline_query_current_chat=action_name,
                            ),
                            InlineKeyboardButton(
                                "📚 Help",
                                url=f"https://t.me/{username}?start=help",
                            ),
                        ]]
                    ),
                )
            )
    if action_results:
        results = action_results
    elif action_name:
        results = [
            InlineQueryResultArticle(
                id=f"zara-{action_name}-instructions",
                title=f"💗 {action_name.title()} action",
                description=f"Use /{action_name} by replying to a user's message",
                input_message_content=InputTextMessageContent(
                    f"💗 Use <code>/{action_name} @username</code> in a group, "
                    "or reply to a user's message with the command.",
                    parse_mode=ParseMode.HTML,
                ),
            )
        ]
    else:
        results = [
            InlineQueryResultArticle(
                id="zara-actions",
                title="💗 ᴄʜᴏᴏsᴇ ᴀ ɢɪғ ᴀᴄᴛɪᴏɴ",
                description="Try: hug, kiss, love, slap, punch, dance, or bonk",
                input_message_content=InputTextMessageContent(
                    "💗 <b>Zara GIF actions</b>\n\n"
                    "Type an inline search such as "
                    f"<code>@{context.application.bot_data.get('username', 'Meee_Zarabot')} hug</code>, "
                    "<code>kiss</code>, or <code>love</code>.",
                    parse_mode=ParseMode.HTML,
                ),
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("🤗 Hug", switch_inline_query_current_chat="hug"),
                        InlineKeyboardButton("💋 Kiss", switch_inline_query_current_chat="kiss"),
                        InlineKeyboardButton("💗 Love", switch_inline_query_current_chat="love"),
                    ], [
                        InlineKeyboardButton("🫲 Slap", switch_inline_query_current_chat="slap"),
                        InlineKeyboardButton("💃 Dance", switch_inline_query_current_chat="dance"),
                        InlineKeyboardButton("🔨 Bonk", switch_inline_query_current_chat="bonk"),
                    ]]
                ),
            ),
            InlineQueryResultArticle(
                id="zara-game",
                title="🎴 ᴏᴘᴇɴ ᴢᴀʀᴀ ᴄᴀʀᴅ ɢᴀᴍᴇ",
                description="Share the multiplayer game instructions",
                input_message_content=InputTextMessageContent(
                    "🎴 <b>ᴢᴀʀᴀ ᴄᴀʀᴅ ɢᴀᴍᴇ</b>\n\n"
                    "1. ᴜsᴇ /bet 2000 ᴛᴏ sᴛᴀʀᴛ ᴀ ʟᴏʙʙʏ\n"
                    "2. ғʀɪᴇɴᴅs ᴜsᴇ /card ᴛᴏ ᴊᴏɪɴ\n"
                    "3. ᴜsᴇ /flip a, /flip b, /flip c, /flip d ᴏʀ /flip e",
                    parse_mode=ParseMode.HTML,
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "➕ ᴀᴅᴅ ᴢᴀʀᴀ ᴛᴏ ɢʀᴏᴜᴘ",
                        url=f"https://t.me/{username}?startgroup=true",
                    ),
                    InlineKeyboardButton("📚 ʜᴇʟᴘ", url=f"https://t.me/{username}?start=help"),
                ]]),
            ),
        ]
    await update.inline_query.answer(results, cache_time=3, is_personal=True)


async def _callback_inner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.data == "explore":
        await edit_callback_message(
            query,
            EXPLORE_TEXT,
            image_path=MENU_IMAGES.get("explore"),
            parse_mode=ParseMode.HTML,
            reply_markup=explore_keyboard(owner(query.from_user.id)),
        )
    elif query.data == "nav:explore":
        await edit_callback_message(
            query,
            EXPLORE_TEXT,
            image_path=MENU_IMAGES.get("nav:explore"),
            parse_mode=ParseMode.HTML,
            reply_markup=explore_keyboard(owner(query.from_user.id)),
        )
    elif query.data == "nav:home":
        user = query.from_user
        row = STORE.user(user)
        await edit_callback_message(
            query,
            start_message(user, row),
            image_path=MENU_IMAGES.get("nav:home"),
            parse_mode=ParseMode.HTML,
            reply_markup=start_keyboard(
                context.application.bot_data.get("username", "ZaraBot")
            ),
        )
    elif query.data == "mod:home":
        await edit_callback_message(
            query,
            MODERATION_PAGE,
            image_path=MENU_IMAGES.get("mod:home"),
            parse_mode=ParseMode.HTML,
            reply_markup=modular_moderation_keyboard(),
        )
    elif query.data in MODERATION_CATEGORY_PAGES:
        await edit_callback_message(
            query,
            MODERATION_CATEGORY_PAGES[query.data],
            parse_mode=ParseMode.HTML,
            reply_markup=modular_moderation_category_keyboard(),
        )
    elif query.data == "mod:quick":
        if not query.message or query.message.chat.type == ChatType.PRIVATE:
            await edit_callback_message(
                query,
                QUICK_SETTINGS_INFO,
                image_path=MENU_IMAGES.get("mod:quick"),
                parse_mode=ParseMode.HTML,
                reply_markup=modular_moderation_category_keyboard(),
            )
            return
        if not await is_group_admin(update, context):
            await answer_callback(query, "❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
            return
        row = STORE.chat(query.message.chat.id, query.message.chat.title or "")
        settings = modcfg.load_settings(row)
        await edit_config_message(
            query,
            modcfg.quick_settings_text(settings, 0),
            parse_mode=ParseMode.HTML,
            reply_markup=modular_quick_settings_keyboard(settings, 0),
        )
    elif query.data.startswith("qs:toggle:"):
        if not query.message or query.message.chat.type == ChatType.PRIVATE:
            await answer_callback(query, "ɢʀᴏᴜᴘs ᴏɴʟʏ.", show_alert=True)
            return
        if not await is_group_admin(update, context):
            await answer_callback(query, "❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
            return
        key = query.data.split(":", 2)[2]
        page = modcfg.quick_settings_page_for_key(key)
        row = STORE.chat(query.message.chat.id, query.message.chat.title or "")
        settings = modcfg.load_settings(row)
        try:
            enabled = modcfg.toggle_setting(settings, key)
        except KeyError:
            await answer_callback(query, "ᴜɴᴋɴᴏᴡɴ sᴇᴛᴛɪɴɢ.", show_alert=True)
            return
        modcfg.save_settings(STORE, query.message.chat.id, settings)
        await edit_config_message(
            query,
            modcfg.quick_settings_text(settings, page),
            parse_mode=ParseMode.HTML,
            reply_markup=modular_quick_settings_keyboard(settings, page),
        )
        await answer_callback(query, f"{key}: {'on' if enabled else 'off'}")
    elif query.data.startswith("qs:config:"):
        if not query.message or query.message.chat.type == ChatType.PRIVATE:
            await answer_callback(query, "ɢʀᴏᴜᴘs ᴏɴʟʏ.", show_alert=True)
            return
        if not await is_group_admin(update, context):
            await answer_callback(query, "❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
            return
        key = query.data.split(":", 2)[2]
        if key not in modcfg.CONFIG_KEYS:
            await answer_callback(query, "ɴᴏ ᴄᴏɴғɪɢ ғᴏʀ ᴛʜɪs sᴇᴛᴛɪɴɢ.")
            return
        page = modcfg.quick_settings_page_for_key(key)
        row = STORE.chat(query.message.chat.id, query.message.chat.title or "")
        settings = modcfg.load_settings(row)
        penalty = modcfg.cycle_penalty(settings, key)
        modcfg.save_settings(STORE, query.message.chat.id, settings)
        await edit_config_message(
            query,
            modcfg.quick_settings_text(settings, page),
            parse_mode=ParseMode.HTML,
            reply_markup=modular_quick_settings_keyboard(settings, page),
        )
        await answer_callback(query, f"{key}: {modcfg.PENALTY_LABELS[penalty]}")
    elif query.data.startswith("qs:page:"):
        if not query.message or query.message.chat.type == ChatType.PRIVATE:
            await answer_callback(query, "ɢʀᴏᴜᴘs ᴏɴʟʏ.", show_alert=True)
            return
        if not await is_group_admin(update, context):
            await answer_callback(query, "❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
            return
        try:
            page = int(query.data.split(":", 2)[2])
        except (IndexError, ValueError):
            page = 0
        row = STORE.chat(query.message.chat.id, query.message.chat.title or "")
        settings = modcfg.load_settings(row)
        await edit_config_message(
            query,
            modcfg.quick_settings_text(settings, page),
            parse_mode=ParseMode.HTML,
            reply_markup=modular_quick_settings_keyboard(settings, page),
        )
    elif query.data == "qs:noop":
        await answer_callback(query, "ᴜsᴇ ᴛʜᴇ sᴛᴀᴛᴜs ᴏʀ ᴘᴇɴᴀʟᴛʏ ʙᴜᴛᴛᴏɴ.")
    elif query.data.startswith("qs:refresh"):
        if not query.message or query.message.chat.type == ChatType.PRIVATE:
            await answer_callback(query, "ɢʀᴏᴜᴘs ᴏɴʟʏ.", show_alert=True)
            return
        parts = query.data.split(":", 2)
        try:
            page = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            page = 0
        row = STORE.chat(query.message.chat.id, query.message.chat.title or "")
        settings = modcfg.load_settings(row)
        await edit_config_message(
            query,
            modcfg.quick_settings_text(settings, page),
            parse_mode=ParseMode.HTML,
            reply_markup=modular_quick_settings_keyboard(settings, page),
        )
    elif query.data.startswith("prot:toggle:"):
        if not query.message or query.message.chat.type == ChatType.PRIVATE:
            await answer_callback(query, "ɢʀᴏᴜᴘs ᴏɴʟʏ.", show_alert=True)
            return
        if not await is_group_admin(update, context):
            await answer_callback(query, "❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
            return
        key = query.data.split(":", 2)[2]
        if key not in modcfg.PROTECTION_LABELS:
            await answer_callback(query, "ᴜɴᴋɴᴏᴡɴ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ sᴇᴛᴛɪɴɢ.", show_alert=True)
            return
        row = STORE.chat(query.message.chat.id, query.message.chat.title or "")
        settings = modcfg.load_settings(row)
        try:
            enabled = modcfg.toggle_setting(settings, key)
        except KeyError:
            await answer_callback(query, "ᴜɴᴋɴᴏᴡɴ sᴇᴛᴛɪɴɢ.", show_alert=True)
            return
        modcfg.save_settings(STORE, query.message.chat.id, settings)
        await edit_config_message(
            query,
            modcfg.protection_status_text(key, settings),
            parse_mode=ParseMode.HTML,
            reply_markup=modular_protection_keyboard(key, settings),
        )
        await answer_callback(query, f"{key}: {'on' if enabled else 'off'}")
    elif query.data.startswith("prot:config:"):
        if not query.message or query.message.chat.type == ChatType.PRIVATE:
            await answer_callback(query, "ɢʀᴏᴜᴘs ᴏɴʟʏ.", show_alert=True)
            return
        if not await is_group_admin(update, context):
            await answer_callback(query, "❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
            return
        key = query.data.split(":", 2)[2]
        if key not in modcfg.CONFIG_KEYS:
            await answer_callback(query, "ɴᴏ ᴄᴏɴғɪɢ ғᴏʀ ᴛʜɪs sᴇᴛᴛɪɴɢ.")
            return
        row = STORE.chat(query.message.chat.id, query.message.chat.title or "")
        settings = modcfg.load_settings(row)
        penalty = modcfg.cycle_penalty(settings, key)
        modcfg.save_settings(STORE, query.message.chat.id, settings)
        await edit_config_message(
            query,
            modcfg.protection_status_text(key, settings),
            parse_mode=ParseMode.HTML,
            reply_markup=modular_protection_keyboard(key, settings),
        )
        await answer_callback(query, f"{key}: {modcfg.PENALTY_LABELS[penalty]}")
    elif query.data == "prot:noop":
        await answer_callback(query, "ɴᴏ ᴄᴏɴғɪɢ ғᴏʀ ᴛʜɪs sᴇᴛᴛɪɴɢ.")
    elif query.data.startswith("captcha:verify:"):
        try:
            target_id = int(query.data.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            return
        if query.from_user.id != target_id:
            await answer_callback(query, "ᴛʜɪs ᴄᴀᴘᴛᴄʜᴀ ɪs ɴᴏᴛ ғᴏʀ ʏᴏᴜ.", show_alert=True)
            return
        chat = query.message.chat if query.message else None
        if not chat:
            return
        try:
            await context.bot.restrict_chat_member(
                chat.id,
                target_id,
                chat_default_permissions(chat),
            )
        except TelegramError:
            await answer_callback(
                query, "ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ғᴀɪʟᴇᴅ. ᴀsᴋ ᴀɴ ᴀᴅᴍɪɴ.", show_alert=True
            )
            return
        try:
            await edit_callback_message(
                query,
                f"✅ {mention(query.from_user)} ᴠᴇʀɪғɪᴇᴅ. ᴡᴇʟᴄᴏᴍᴇ!",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            await answer_callback(
                query,
                "✅ ʏᴏᴜ ᴀʀᴇ ᴠᴇʀɪғɪᴇᴅ, ʙᴜᴛ ɪ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴇᴅɪᴛ ᴛʜɪs ᴍᴇssᴀɢᴇ.",
                show_alert=True,
            )
    elif query.data == "cat:moderation":
        await edit_callback_message(
            query,
            MODERATION_PAGE,
            image_path=MENU_IMAGES.get("cat:moderation"),
            parse_mode=ParseMode.HTML,
            reply_markup=modular_moderation_keyboard(),
        )
    elif query.data == "cat:games":
        await edit_callback_message(
            query,
            GAMES_PAGE,
            image_path=MENU_IMAGES.get("cat:games"),
            parse_mode=ParseMode.HTML,
            reply_markup=games_page_keyboard(),
        )
    elif query.data in CATEGORY_PAGES:
        if query.data == "cat:owner" and not owner(query.from_user.id):
            await edit_callback_message(
                query,
                "❌ <b>ᴏᴡɴᴇʀ ᴏɴʟʏ</b>",
                image_path=MENU_IMAGES.get("cat:owner"),
                parse_mode=ParseMode.HTML,
                reply_markup=category_keyboard(),
            )
            return
        await edit_callback_message(
            query,
            CATEGORY_PAGES[query.data],
            image_path=MENU_IMAGES.get(query.data),
            parse_mode=ParseMode.HTML,
            reply_markup=category_keyboard(),
        )
    elif query.data == "games":
        await edit_callback_message(
            query,
            GAMES_PAGE,
            image_path=MENU_IMAGES.get("games"),
            parse_mode=ParseMode.HTML,
            reply_markup=games_page_keyboard(),
        )
    elif query.data == "commands":
        await edit_callback_message(
            query,
            EXPLORE_TEXT,
            image_path=MENU_IMAGES.get("commands"),
            parse_mode=ParseMode.HTML,
            reply_markup=explore_keyboard(owner(query.from_user.id)),
        )
    elif query.data == "economy":
        await edit_callback_message(
            query,
            ECONOMY_PAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=category_keyboard(),
        )
    elif query.data == "premium":
        await edit_callback_message(
            query,
            PREMIUM_PAGE,
            image_path=MENU_IMAGES.get("premium"),
            parse_mode=ParseMode.HTML,
            reply_markup=category_keyboard(),
        )
    elif query.data == "game_card":
        await edit_callback_message(
            query,
            GAME_CARD_PAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=game_detail_keyboard(),
        )
    elif query.data == "game_bomb":
        await edit_callback_message(
            query,
            GAME_BOMB_PAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=game_detail_keyboard(),
        )
    elif query.data == "game_roulette":
        await edit_callback_message(
            query,
            GAME_ROULETTE_PAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=game_detail_keyboard(),
        )
    elif query.data == "game_bluff":
        await edit_callback_message(
            query,
            GAME_BLUFF_PAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=game_detail_keyboard(),
        )
    elif query.data == "game_hack":
        await edit_callback_message(
            query,
            GAME_HACK_PAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=game_detail_keyboard(),
        )
    elif query.data == "game_prompts":
        await edit_callback_message(
            query,
            GAME_PROMPTS_PAGE,
            image_path=MENU_IMAGES.get("game_prompts"),
            parse_mode=ParseMode.HTML,
            reply_markup=game_detail_keyboard(),
        )
    elif query.data == "card_bet":
        await edit_callback_message(query, "💰 ᴜsᴇ /bet 2000 ɪɴ ᴀ ɢʀᴏᴜᴘ ᴛᴏ sᴛᴀʀᴛ ᴛʜᴇ ᴄᴀʀᴅ ʟᴏʙʙʏ.")
    elif query.data == "card_join":
        await edit_callback_message(query, "🎴 ᴜsᴇ /card ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ ᴛᴏ ᴊᴏɪɴ ᴛʜᴇ ᴄᴀʀᴅ ɢᴀᴍᴇ.")
    elif query.data.startswith("flip:"):
        await flip(update, context)
    elif query.data == "leaderboard":
        rows = ranking_rows("balance")
        text, markup = leaderboard_text(rows, "ᴢᴀʀᴀ ᴛᴏᴘ 10 ʀɪᴄʜᴇsᴛ")
        await edit_callback_message(
            query,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    elif query.data == "global_toprich":
        rows = ranking_rows("balance")
        text, markup = leaderboard_text(rows, "ɢʟᴏʙᴀʟ ᴛᴏᴘ 10 ʀɪᴄʜᴇsᴛ")
        await edit_callback_message(
            query,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await _callback_inner(update, context)
    finally:
        if query and str(query.id) not in _ANSWERED_CALLBACKS:
            await answer_callback(query)


async def text_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user or user.is_bot:
        return
    STORE.user(user)
    chat = update.effective_chat
    text = message.text or message.caption or ""
    is_command = bool(message.text and message.text.startswith("/"))

    if chat and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        if is_command:
            return
        row = STORE.chat(chat.id, chat.title or "")
        settings = modcfg.load_settings(row)
        violation = ""

        if settings.get("antibio") and await user_bio_has_link(context, user.id):
            if not await is_group_admin(update, context):
                violation = "antibio"

        if not violation:
            if settings.get("antichannel") and (
                getattr(message, "sender_chat", None)
                or getattr(message, "is_automatic_forward", False)
            ):
                violation = "antichannel"
            elif settings.get("antilink") and LINK_PATTERN.search(text):
                violation = "antilink"
            elif settings.get("antiforward") and (
                getattr(message, "forward_date", None)
                or getattr(message, "forward_from", None)
                or getattr(message, "forward_from_chat", None)
                or getattr(message, "forward_origin", None)
            ):
                violation = "antiforward"
            elif settings.get("antiinline") and getattr(message, "reply_markup", None):
                violation = "antiinline"
            elif settings.get("antimedia") and (
                getattr(message, "photo", None)
                or getattr(message, "video", None)
                or getattr(message, "video_note", None)
                or getattr(message, "animation", None)
            ):
                violation = "antimedia"
            elif settings.get("antisticker") and (
                getattr(message, "sticker", None) or getattr(message, "animation", None)
            ):
                violation = "antisticker"
            elif settings.get("antivoice") and (
                getattr(message, "voice", None)
                or getattr(message, "audio", None)
                or getattr(message, "document", None)
            ):
                violation = "antivoice"
            elif settings.get("antipoll") and (
                getattr(message, "poll", None)
                or getattr(message, "location", None)
                or getattr(message, "venue", None)
                or getattr(message, "contact", None)
                or getattr(message, "dice", None)
            ):
                violation = "antipoll"
            elif settings.get("antimassmention"):
                entities = getattr(message, "entities", None) or getattr(
                    message, "caption_entities", None
                ) or ()
                mentions = sum(
                    1
                    for entity in entities
                    if getattr(entity, "type", "") in {"mention", "text_mention"}
                )
                if mentions > MASS_MENTION_LIMIT:
                    violation = "antimassmention"
            elif settings.get("anticaps") and text:
                letters = [character for character in text if character.isalpha()]
                if len(letters) >= 10 and (
                    sum(character.isupper() for character in letters) / len(letters)
                ) > 0.7:
                    violation = "anticaps"
            elif settings.get("antilong") and len(text) > LONG_MESSAGE_LIMIT:
                violation = "antilong"
            elif settings.get("antispam") and text and not is_command:
                key = (chat.id, user.id)
                previous = SPAM_TRACKER.get(key)
                current = time.monotonic()
                if previous and previous[0] == text and current - previous[2] < 10:
                    count = previous[1] + 1
                    SPAM_TRACKER[key] = (text, count, current)
                    if count >= 3:
                        violation = "antispam"
                else:
                    SPAM_TRACKER[key] = (text, 1, current)
            if not violation and settings.get("antiflood") and not is_command:
                key = (chat.id, user.id)
                current = time.monotonic()
                bucket = FLOOD_TRACKER.setdefault(key, deque())
                while bucket and current - bucket[0] > 10:
                    bucket.popleft()
                bucket.append(current)
                if len(bucket) > int(settings.get("flood_limit", 5)):
                    violation = "antiflood"

        if violation and not await is_group_admin(update, context):
            removed = True
            try:
                await message.delete()
            except TelegramError:
                removed = False
            penalty = modcfg.penalty_for(settings, violation)
            try:
                outcome = await apply_penalty(
                    context,
                    chat,
                    user.id,
                    penalty,
                    settings,
                    modcfg.PROTECTION_LABELS.get(violation, violation),
                    text,
                )
            except Exception:
                log.exception("Penalty failed for %s in %s", violation, chat.id)
                outcome = "action failed"
            await send_chat_notice(
                context,
                chat,
                violation_warning(
                    violation,
                    user,
                    removed,
                    action=penalty,
                    outcome=outcome,
                ),
            )
            return

    is_sticker = bool(message.sticker)
    if not text and not is_sticker:
        return

    if text and chat and chat.type != ChatType.PRIVATE:
        blacklisted = STORE.db.execute(
            "SELECT word FROM blacklists WHERE chat_id=?",
            (update.effective_chat.id,),
        ).fetchall()
        for row in blacklisted:
            if matches_filter_word(text, row["word"]):
                removed = True
                try:
                    await update.message.delete()
                except TelegramError:
                    removed = False
                await send_chat_notice(
                    context,
                    chat,
                    violation_warning("blacklist", user, removed),
                )
                return
    if text:
        for row in STORE.db.execute(
            "SELECT word,response FROM filters WHERE chat_id=?",
            (update.effective_chat.id,),
        ).fetchall():
            if matches_filter_word(text, row["word"]):
                await update.message.reply_text(row["response"])
                return

    replied_message = update.message.reply_to_message
    replied_user = getattr(replied_message, "from_user", None)
    is_reply_to_bot = bool(
        replied_message and replied_user and replied_user.id == context.bot.id
    )
    chat_type = chat.type if chat else ""
    if is_sticker:
        if zara_ai.should_respond(
            "",
            chat_type=chat_type,
            is_reply_to_bot=is_reply_to_bot,
            is_sticker=True,
        ):
            await ai_reply(update, context, is_sticker=True)
        return
    if not zara_ai.should_respond(
        text,
        chat_type=chat_type,
        is_reply_to_bot=is_reply_to_bot,
        bot_username=context.bot.username or "",
    ):
        return
    await ai_reply(update, context, text=text)


async def track_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record users seen in groups so /toprich can show a group ranking."""
    chat = update.effective_chat
    user = update.effective_user
    if (
        chat
        and user
        and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
    ):
        STORE.mark_chat_member(int(chat.id), int(user.id))


async def error_handler(
    update: Update | None, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Log every unhandled handler failure instead of failing silently."""
    error = context.error
    if isinstance(error, RetryAfter):
        log.warning("Telegram flood control in handler: retry in %ss", error.retry_after)
        return
    log.error(
        "Unhandled handler error",
        exc_info=(type(error), error, error.__traceback__) if error else None,
    )
    if (
        update
        and update.effective_chat
        and update.effective_chat.type == ChatType.PRIVATE
    ):
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ Kuch technical problem aa gayi. Thodi der baad try karo.",
            )
        except TelegramError:
            pass


def add_handlers(app: Application) -> None:
    runtime = globals()
    command_map = build_commands(runtime)
    admin_map = build_admin_commands(runtime)
    overlap = command_map.keys() & admin_map.keys()
    if overlap:
        raise RuntimeError(f"Command group collision: {sorted(overlap)}")
    for name, handler in command_map.items():
        app.add_handler(CommandHandler(name, handler))
    for name, handler in admin_map.items():
        app.add_handler(CommandHandler(name, handler))
    log.info("Registered %s public and %s admin commands", len(command_map), len(admin_map))
    app.add_handler(TypeHandler(Update, track_chat_member, block=False), group=-1)
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_verify))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_members))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, goodbye_members))
    app.add_handler(
        ChatMemberHandler(chat_member_update, ChatMemberHandler.CHAT_MEMBER)
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & (filters.ANIMATION | filters.VIDEO | filters.Document.ALL),
            capture_owner_gif,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
            text_filter,
        )
    )
    app.add_error_handler(error_handler)


def main() -> None:
    token = os.getenv("ZARA_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("ZARA_BOT_TOKEN secret is required.")
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
        httpx_kwargs={
            "transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        },
    )
    application = (
        Application.builder()
        .token(token)
        .request(request)
        .post_init(post_init)
        .build()
    )
    add_handlers(application)
    log.info("Starting Zara polling service; database=%s", STORE.config.safe_label())
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()