from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import TelegramError

import bot as zara_bot
from zara import moderation as modcfg
from zara.handlers import stickers


def make_update(
    text: str,
    args: list[str] | None = None,
    *,
    reply=None,
    user_id: int = 1,
):
    message = SimpleNamespace(
        text=text,
        message_id=20,
        reply_to_message=reply,
        delete=AsyncMock(),
    )
    chat = SimpleNamespace(
        id=-1001,
        type=ChatType.SUPERGROUP,
        title="Test Group",
        permissions=None,
    )
    user = SimpleNamespace(
        id=user_id,
        full_name="Admin User",
        username="admin",
        is_bot=False,
    )
    update = SimpleNamespace(
        effective_chat=chat,
        effective_user=user,
        effective_message=message,
    )
    context = SimpleNamespace(bot=FakeBot(), args=args or [])
    return update, context, message


class FakeBot:
    id = 999

    def __init__(self) -> None:
        self.ban_chat_member = AsyncMock()
        self.unban_chat_member = AsyncMock()
        self.restrict_chat_member = AsyncMock()
        self.promote_chat_member = AsyncMock()
        self.set_chat_permissions = AsyncMock()
        self.unpin_chat_message = AsyncMock()
        self.delete_messages = AsyncMock()
        self.delete_message = AsyncMock()
        self.get_chat_member = AsyncMock()
        self.get_chat = AsyncMock()
        self.send_message = AsyncMock()


@pytest.fixture
def store(tmp_path, monkeypatch):
    instance = zara_bot.Store(tmp_path / "test.sqlite3")
    monkeypatch.setattr(zara_bot, "STORE", instance)
    instance.chat(-1001, "Test Group")
    yield instance
    instance.db.close()


def patch_admin(monkeypatch, *, status=ChatMemberStatus.MEMBER):
    monkeypatch.setattr(zara_bot, "is_group_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(
        zara_bot, "bot_admin_check", AsyncMock(return_value=(True, ""))
    )
    member = SimpleNamespace(
        status=status,
        user=SimpleNamespace(full_name="Target User"),
        can_send_messages=True,
    )
    monkeypatch.setattr(
        zara_bot, "target_member", AsyncMock(return_value=(member, ""))
    )
    reply = AsyncMock()
    monkeypatch.setattr(zara_bot, "safe_reply", reply)
    monkeypatch.setattr(
        zara_bot, "send_mod_log", AsyncMock(return_value="sent")
    )
    return reply


@pytest.mark.asyncio
async def test_ban_with_bot_suffix_really_bans(store, monkeypatch):
    reply = patch_admin(monkeypatch)
    update, context, _ = make_update("/ban@ZaraBot 12345", ["12345"])

    await zara_bot.admin_command(update, context)

    context.bot.ban_chat_member.assert_awaited_once()
    assert reply.await_count == 1


@pytest.mark.asyncio
async def test_ban_without_target_does_not_target_sender(store, monkeypatch):
    reply = patch_admin(monkeypatch)
    update, context, _ = make_update("/ban")

    await zara_bot.admin_command(update, context)

    context.bot.ban_chat_member.assert_not_awaited()
    assert reply.await_count == 1


@pytest.mark.asyncio
async def test_pin_without_reply_is_not_fake_success(store, monkeypatch):
    reply = patch_admin(monkeypatch)
    update, context, _ = make_update("/pin")

    await zara_bot.admin_command(update, context)

    assert reply.await_count == 1
    assert "ᴘɪɴ" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_unban_non_banned_is_not_reported_as_done(store, monkeypatch):
    reply = patch_admin(monkeypatch, status=ChatMemberStatus.MEMBER)
    update, context, _ = make_update("/unban 12345", ["12345"])

    await zara_bot.admin_command(update, context)

    context.bot.unban_chat_member.assert_not_awaited()
    assert "ɴᴏᴛ ʙᴀɴɴᴇᴅ" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_kick_already_banned_does_not_unban(store, monkeypatch):
    patch_admin(monkeypatch, status=ChatMemberStatus.BANNED)
    update, context, _ = make_update("/kick 12345", ["12345"])

    await zara_bot.admin_command(update, context)

    context.bot.ban_chat_member.assert_not_awaited()
    context.bot.unban_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_tban_requires_duration(store, monkeypatch):
    patch_admin(monkeypatch)
    update, context, _ = make_update("/tban 12345", ["12345"])

    await zara_bot.admin_command(update, context)

    context.bot.ban_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_tban_passes_duration_to_telegram(store, monkeypatch):
    patch_admin(monkeypatch)
    update, context, _ = make_update("/tban 1h 12345", ["1h", "12345"])

    await zara_bot.admin_command(update, context)

    context.bot.ban_chat_member.assert_awaited_once()
    kwargs = context.bot.ban_chat_member.await_args.kwargs
    assert kwargs["until_date"] > zara_bot.now()


@pytest.mark.asyncio
async def test_warn_action_failure_keeps_warnings(store, monkeypatch):
    reply = patch_admin(monkeypatch)
    settings = modcfg.load_settings(store.chat(-1001, "Test Group"))
    settings["warn_limit"] = 1
    settings["warn_action"] = "ban"
    modcfg.save_settings(store, -1001, settings)
    update, context, _ = make_update("/warn 12345", ["12345"])
    context.bot.ban_chat_member.side_effect = TelegramError("ban failed")

    await zara_bot.admin_command(update, context)

    row = store.db.execute(
        "SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
        (-1001, 12345),
    ).fetchone()
    assert row["count"] == 1
    assert "ғᴀɪʟᴇᴅ" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_report_without_log_channel_is_not_fake_success(store, monkeypatch):
    reply = AsyncMock()
    monkeypatch.setattr(zara_bot, "safe_reply", reply)
    monkeypatch.setattr(
        zara_bot, "send_mod_log", AsyncMock(return_value="not_configured")
    )
    settings = modcfg.load_settings(store.chat(-1001, "Test Group"))
    settings["reports"] = True
    modcfg.save_settings(store, -1001, settings)
    reported = SimpleNamespace(
        from_user=SimpleNamespace(id=2, full_name="Bad User"),
        text="bad message",
        caption=None,
    )
    update, context, _ = make_update("/report", reply=reported)

    await zara_bot.group_settings(update, context)

    assert "ɴᴏ ʟᴏɢ ᴄʜᴀɴɴᴇʟ" in reply.await_args.args[1]
    assert "sᴇɴᴛ" not in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_approve_unknown_user_does_not_crash_or_write(store, monkeypatch):
    reply = AsyncMock()
    monkeypatch.setattr(zara_bot, "safe_reply", reply)
    update, context, _ = make_update("/approve @nobody", ["@nobody"])

    await zara_bot.group_settings(update, context)

    count = store.db.execute(
        "SELECT COUNT(*) AS n FROM approvals WHERE chat_id=?", (-1001,)
    ).fetchone()["n"]
    assert count == 0
    assert reply.await_count == 1


@pytest.mark.asyncio
async def test_target_user_does_not_create_ghost_account(store):
    update, _, _ = make_update("/bal 999999", ["999999"])

    user_id, row = await zara_bot.target_user(update, ["999999"])

    assert (user_id, row) == (0, None)
    count = store.db.execute(
        "SELECT COUNT(*) AS n FROM users WHERE user_id=?", (999999,)
    ).fetchone()["n"]
    assert count == 0


@pytest.mark.asyncio
async def test_target_user_synthetic_id_does_not_insert(store):
    update, _, _ = make_update("/ban 999999", ["999999"])

    user_id, row = await zara_bot.target_user(
        update, ["999999"], allow_synthetic=True
    )

    assert user_id == 999999
    assert row["name"] == "Telegram user 999999"
    count = store.db.execute(
        "SELECT COUNT(*) AS n FROM users WHERE user_id=?", (999999,)
    ).fetchone()["n"]
    assert count == 0


def test_parse_duration_and_split():
    assert zara_bot.parse_duration("10m") == 600
    assert zara_bot.parse_duration("2h") == 7200
    assert zara_bot.parse_duration("1d") == 86400
    assert zara_bot.parse_duration("0") is None
    assert zara_bot.parse_duration("nope") is None
    assert zara_bot.split_duration_args(["1h", "@user"], False) == (
        3600,
        ["@user"],
        "",
    )
    assert zara_bot.split_duration_args(["@user"], False)[0] is None
    assert zara_bot.split_duration_args(["1h", "@user"], True)[0] is None


def test_matches_filter_word_uses_word_boundaries():
    assert zara_bot.matches_filter_word("hello there", "hello")
    assert not zara_bot.matches_filter_word("shelloworld", "hello")
    assert zara_bot.matches_filter_word("bad phrase here", "bad phrase")


def test_moderation_defaults_are_not_shared():
    first = modcfg.load_settings(None)
    second = modcfg.load_settings(None)
    modcfg.set_penalty(first, "antilink", "ban")
    assert modcfg.penalty_for(second, "antilink") == "nothing"


def test_sticker_commands_are_registered():
    assert "sticker" in stickers.NAMES
    assert "stickerpack" in stickers.NAMES


@pytest.mark.asyncio
async def test_daily_streak_resets_after_break(store):
    user = SimpleNamespace(id=777, username="daily", full_name="Daily User")
    store.user(user)
    store.db.execute(
        "UPDATE users SET streak=5,last_daily=? WHERE user_id=?",
        (zara_bot.now() - 3 * 86400, 777),
    )
    store.db.commit()

    row = store.claim_daily(777, 2000)

    assert row["streak"] == 1


@pytest.mark.asyncio
async def test_staff_give_does_not_mint_coins(store, monkeypatch):
    reply = AsyncMock()
    monkeypatch.setattr(zara_bot, "safe_reply", reply)
    sender = SimpleNamespace(
        id=zara_bot.OWNER_ID,
        username="owner",
        full_name="Owner",
        is_bot=False,
    )
    target = SimpleNamespace(id=2, username="target", full_name="Target")
    store.user(sender)
    store.user(target)
    update, context, _ = make_update(
        "/give 500 @target", ["500", "@target"], user_id=sender.id
    )

    await zara_bot.give(update, context)

    sender_row = store.db.execute(
        "SELECT balance FROM users WHERE user_id=?", (sender.id,)
    ).fetchone()
    target_row = store.db.execute(
        "SELECT balance FROM users WHERE user_id=?", (target.id,)
    ).fetchone()
    assert sender_row["balance"] == 500
    assert target_row["balance"] == 1500


@pytest.mark.asyncio
async def test_gift_requires_inventory_and_transfers_item(store, monkeypatch):
    reply = AsyncMock()
    monkeypatch.setattr(zara_bot, "safe_reply", reply)
    sender = SimpleNamespace(
        id=501, username="sender", full_name="Sender", is_bot=False
    )
    target = SimpleNamespace(
        id=502, username="target2", full_name="Target Two", is_bot=False
    )
    store.user(sender)
    store.user(target)
    store.db.execute(
        "INSERT INTO items(user_id,item,quantity) VALUES(?,?,1)", (sender.id, "rose")
    )
    store.db.commit()
    update, context, _ = make_update(
        "/gift @target2 rose", ["@target2", "rose"], user_id=sender.id
    )

    await zara_bot.item_command(update, context)

    sender_items = store.db.execute(
        "SELECT item,quantity FROM items WHERE user_id=?", (sender.id,)
    ).fetchall()
    target_items = store.db.execute(
        "SELECT item,quantity FROM items WHERE user_id=?", (target.id,)
    ).fetchall()
    assert sender_items == []
    assert target_items[0]["item"] == "rose"
    assert target_items[0]["quantity"] == 1


@pytest.mark.asyncio
async def test_gift_without_item_does_not_create_item(store, monkeypatch):
    reply = AsyncMock()
    monkeypatch.setattr(zara_bot, "safe_reply", reply)
    sender = SimpleNamespace(
        id=601, username="sender2", full_name="Sender Two", is_bot=False
    )
    target = SimpleNamespace(
        id=602, username="target3", full_name="Target Three", is_bot=False
    )
    store.user(sender)
    store.user(target)
    update, context, _ = make_update(
        "/gift @target3 rose", ["@target3", "rose"], user_id=sender.id
    )

    await zara_bot.item_command(update, context)

    target_items = store.db.execute(
        "SELECT COUNT(*) AS n FROM items WHERE user_id=?", (target.id,)
    ).fetchone()["n"]
    assert target_items == 0
    assert "ᴅᴏɴ'ᴛ ᴏᴡɴ" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_marry_rejects_polygamy_and_divorce_requires_partner(store, monkeypatch):
    reply = AsyncMock()
    monkeypatch.setattr(zara_bot, "safe_reply", reply)
    first = SimpleNamespace(id=701, username="first", full_name="First", is_bot=False)
    second = SimpleNamespace(id=702, username="second", full_name="Second", is_bot=False)
    third = SimpleNamespace(id=703, username="third", full_name="Third", is_bot=False)
    for user in (first, second, third):
        store.user(user)

    update, context, _ = make_update(
        "/marry @second", ["@second"], user_id=first.id
    )
    await zara_bot.relationship(update, context)

    couples = store.db.execute(
        "SELECT user_id,partner_id FROM couples ORDER BY user_id"
    ).fetchall()
    assert [(row["user_id"], row["partner_id"]) for row in couples] == [
        (first.id, second.id),
        (second.id, first.id),
    ]

    update, context, _ = make_update(
        "/marry @third", ["@third"], user_id=first.id
    )
    await zara_bot.relationship(update, context)
    assert "ᴀʟʀᴇᴀᴅʏ ᴍᴀʀʀɪᴇᴅ" in reply.await_args.args[1]

    update, context, _ = make_update(
        "/divorce @third", ["@third"], user_id=first.id
    )
    await zara_bot.relationship(update, context)
    couples = store.db.execute("SELECT COUNT(*) AS n FROM couples").fetchone()["n"]
    assert couples == 2
    assert "ɴᴏᴛ ᴍᴀʀʀɪᴇᴅ" in reply.await_args.args[1]


def test_verification_is_optional_when_channel_not_configured(monkeypatch):
    monkeypatch.setattr(zara_bot, "UPDATE_CHANNEL_CHAT_ID", "")
    monkeypatch.setattr(zara_bot, "UPDATE_CHANNEL_URL", "")

    assert zara_bot.normal_user_verified(None) is True
    assert not zara_bot.verification_markup().inline_keyboard
