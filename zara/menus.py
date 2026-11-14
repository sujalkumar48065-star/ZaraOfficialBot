"""Inline keyboards used by the start, game, and card menus."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .config import (
    DEVELOPER_URL,
    SUPPORT_USERNAME,
    UPDATE_CHANNEL,
    UPDATE_CHANNEL_URL,
)
from .moderation import (
    CONFIG_KEYS,
    PENALTY_LABELS,
    penalty_for,
    quick_settings_page_count,
    quick_settings_page_items,
    styled,
)


def start_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    updates_url = UPDATE_CHANNEL_URL or f"https://t.me/{UPDATE_CHANNEL}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔍 Explore", callback_data="explore")],
            [
                InlineKeyboardButton("📢 Updates", url=updates_url),
                InlineKeyboardButton(
                    "💬 Support", url=f"https://t.me/{SUPPORT_USERNAME}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "➕ Add Zara in your group",
                    url=f"https://t.me/{bot_username}?startgroup=true",
                )
            ],
            [InlineKeyboardButton("👨‍💻 Developer", url=DEVELOPER_URL)],
        ]
    )


def explore_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    # The developer/owner contact now lives on the main start menu.
    rows = [
        [
            InlineKeyboardButton("🎮 Games", callback_data="cat:games"),
            InlineKeyboardButton("💰 Economy", callback_data="cat:economy"),
        ],
        [
            InlineKeyboardButton("🎭 Actions & Social", callback_data="cat:actions"),
            InlineKeyboardButton("🛠️ Tools", callback_data="cat:tools"),
        ],
        [
            InlineKeyboardButton("🛡️ Moderation", callback_data="cat:moderation"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="nav:home")],
    ]
    return InlineKeyboardMarkup(rows)


def category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️ Back", callback_data="nav:explore"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
            ]
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
                InlineKeyboardButton("🔐 ʜᴀᴄᴋ", callback_data="game_hack"),
                InlineKeyboardButton("🎲 ᴛʀᴜᴛʜ / ᴅᴀʀᴇ", callback_data="game_prompts"),
            ],
            [
                InlineKeyboardButton(
                    "➕ ᴀᴅᴅ ᴢᴀʀᴀ", url=f"https://t.me/{bot_username}?startgroup=true"
                )
            ],
        ]
    )


def games_page_keyboard() -> InlineKeyboardMarkup:
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
                InlineKeyboardButton("🔐 ʜᴀᴄᴋ", callback_data="game_hack"),
                InlineKeyboardButton("🎲 ᴛʀᴜᴛʜ / ᴅᴀʀᴇ", callback_data="game_prompts"),
            ],
            [
                InlineKeyboardButton("⬅️ ʙᴀᴄᴋ", callback_data="nav:explore"),
                InlineKeyboardButton("🏠 ᴍᴀɪɴ ᴍᴇɴᴜ", callback_data="nav:home"),
            ],
        ]
    )


def game_detail_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️ ʙᴀᴄᴋ ᴛᴏ ɢᴀᴍᴇs", callback_data="cat:games"),
                InlineKeyboardButton("🏠 ᴍᴀɪɴ ᴍᴇɴᴜ", callback_data="nav:home"),
            ]
        ]
    )


def moderation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👋 ᴡᴇʟᴄᴏᴍᴇ & ɢᴏᴏᴅʙʏᴇ", callback_data="mod:cat:welcome"),
                InlineKeyboardButton("📜 ʀᴜʟᴇs & ғɪʟᴛᴇʀs", callback_data="mod:cat:rules"),
            ],
            [
                InlineKeyboardButton("🛡️ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ", callback_data="mod:cat:protection"),
                InlineKeyboardButton("⚖️ ᴡᴀʀɴ & ᴘᴜɴɪsʜ", callback_data="mod:cat:punish"),
            ],
            [
                InlineKeyboardButton("🛠️ ᴀᴅᴍɪɴ ᴛᴏᴏʟs", callback_data="mod:cat:admin"),
                InlineKeyboardButton("📊 ʟᴏɢs & ʀᴇᴘᴏʀᴛs", callback_data="mod:cat:logs"),
            ],
            [
                InlineKeyboardButton("⚙️ ǫᴜɪᴄᴋ sᴇᴛᴛɪɴɢs", callback_data="mod:quick"),
            ],
            [
                InlineKeyboardButton("⬅️ ʙᴀᴄᴋ", callback_data="nav:explore"),
                InlineKeyboardButton("🏠 ᴍᴀɪɴ ᴍᴇɴᴜ", callback_data="nav:home"),
            ],
        ]
    )


def moderation_category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ", callback_data="mod:home"),
                InlineKeyboardButton("🏠 ᴍᴀɪɴ ᴍᴇɴᴜ", callback_data="nav:home"),
            ]
        ]
    )


def quick_settings_keyboard(
    settings: dict[str, object], page: int = 0
) -> InlineKeyboardMarkup:
    pages = quick_settings_page_count()
    page = max(0, min(page, pages - 1))
    rows = []
    for key, label in quick_settings_page_items(page):
        enabled = bool(settings.get(key))
        feature = InlineKeyboardButton(
            styled(label), callback_data="qs:noop"
        )
        status = InlineKeyboardButton(
            f"{'✅' if enabled else '❌'} {styled('on' if enabled else 'off')}",
            callback_data=f"qs:toggle:{key}",
        )
        if key in CONFIG_KEYS:
            penalty = InlineKeyboardButton(
                f"⚙️ {styled(PENALTY_LABELS[penalty_for(settings, key)])}",
                callback_data=f"qs:config:{key}",
            )
        else:
            penalty = InlineKeyboardButton("—", callback_data="qs:noop")
        rows.append([feature, status, penalty])
    previous = (
        InlineKeyboardButton("⬅️", callback_data=f"qs:page:{page - 1}")
        if page > 0
        else InlineKeyboardButton("⬅️", callback_data="qs:noop")
    )
    following = (
        InlineKeyboardButton("➡️", callback_data=f"qs:page:{page + 1}")
        if page < pages - 1
        else InlineKeyboardButton("➡️", callback_data="qs:noop")
    )
    rows.append(
        [
            previous,
            InlineKeyboardButton(
                styled(f"{page + 1}/{pages}"), callback_data="qs:noop"
            ),
            following,
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                f"🔄 {styled('refresh')}", callback_data=f"qs:refresh:{page}"
            ),
            InlineKeyboardButton(
                f"⬅️ {styled('moderation')}", callback_data="mod:home"
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


def protection_keyboard(key: str, settings: dict[str, object]) -> InlineKeyboardMarkup:
    enabled = bool(settings.get(key))
    row = [
        InlineKeyboardButton(
            f"{'✅' if enabled else '❌'} {styled('on' if enabled else 'off')}",
            callback_data=f"prot:toggle:{key}",
        )
    ]
    if key in CONFIG_KEYS:
        row.append(
            InlineKeyboardButton(
                f"⚙️ {styled(PENALTY_LABELS[penalty_for(settings, key)])}",
                callback_data=f"prot:config:{key}",
            )
        )
    return InlineKeyboardMarkup([row])


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