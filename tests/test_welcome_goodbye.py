from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import TelegramError

import bot as zara_bot
from zara import moderation as modcfg


class FakeBot:
    id = 999
    username = "Meee_Zarabot"

    def __init__(self) -> None:
        for name in (
            "send_message",
            "send_photo",
            "send_video",
            "send_animation",
            "send_document",
            "send_sticker",
            "send_chat_action",
            "get_chat_member",
        ):
            setattr(self, name, AsyncMock())


@pytest.fixture
def store(tmp_path, monkeypatch):
    instance = zara_bot.Store(tmp_path / "test.sqlite3")
    monkeypatch.setattr(zara_bot, "STORE", instance)
    instance.chat(-1001, "Test Group")
    zara_bot._MEMBER_EVENTS.clear()
    yield instance
    instance.db.close()


def make_context() -> SimpleNamespace:
    return SimpleNamespace(
        bot=FakeBot(),
        application=SimpleNamespace(bot_data={"username": "Meee_Zarabot"}),
    )


def make_chat() -> SimpleNamespace:
    return SimpleNamespace(
        id=-1001,
        title="Test Group",
        username="testgroup",
        type=ChatType.SUPERGROUP,
        permissions=None,
    )


def make_user(user_id: int = 2, name: str = "New User") -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        full_name=name,
        first_name=name,
        last_name="",
        username="newuser",
        is_bot=False,
    )


def make_settings_update(reply, *, text: str = "/setwelcome"):
    message = SimpleNamespace(
        text=text,
        message_id=30,
        reply_to_message=reply,
        delete=AsyncMock(),
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_chat=make_chat(),
        effective_user=SimpleNamespace(id=1, full_name="Admin User", username="admin"),
        effective_message=message,
    )
    return update


@pytest.mark.asyncio
async def test_setwelcome_reply_to_text_saves_exact_content(store, monkeypatch):
    monkeypatch.setattr(zara_bot, "is_group_admin", AsyncMock(return_value=True))
    reply = AsyncMock()
    monkeypatch.setattr(zara_bot, "safe_reply", reply)
    target = SimpleNamespace(
        text="Welcome to our group",
        text_html="<b>Welcome</b> to our group",
        caption=None,
        caption_html=None,
        photo=None,
        video=None,
        animation=None,
        document=None,
        sticker=None,
    )
    update = make_settings_update(target)

    await zara_bot.group_settings(update, make_context())

    row = store.chat(-1001, "Test Group")
    settings = modcfg.load_settings(row)
    assert row["welcome"] == "<b>Welcome</b> to our group"
    assert settings["welcome_enabled"] is True
    assert settings["welcome_media"] is None
    assert reply.await_count == 1


@pytest.mark.asyncio
async def test_setwelcome_reply_to_photo_saves_media_and_caption(store, monkeypatch):
    monkeypatch.setattr(zara_bot, "is_group_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(zara_bot, "safe_reply", AsyncMock())
    target = SimpleNamespace(
        text=None,
        text_html=None,
        caption="Hi {name}",
        caption_html="Hi {name}",
        photo=[SimpleNamespace(file_id="photo-123")],
        video=None,
        animation=None,
        document=None,
        sticker=None,
    )
    update = make_settings_update(target)

    await zara_bot.group_settings(update, make_context())

    row = store.chat(-1001, "Test Group")
    settings = modcfg.load_settings(row)
    assert row["welcome"] == "Hi {name}"
    assert settings["welcome_media"] == {"type": "photo", "file_id": "photo-123"}

    context = make_context()
    await zara_bot.process_join(context, make_chat(), make_user(name="Riya"))

    context.bot.send_photo.assert_awaited_once()
    args, kwargs = context.bot.send_photo.await_args
    assert args == (-1001, "photo-123")
    assert kwargs["caption"] == "Hi Riya"
    context.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_goodbye_is_exact_without_default_extras(store, monkeypatch):
    monkeypatch.setattr(zara_bot, "is_group_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(zara_bot, "safe_reply", AsyncMock())
    target = SimpleNamespace(
        text="goodbye",
        text_html="goodbye",
        caption=None,
        caption_html=None,
        photo=None,
        video=None,
        animation=None,
        document=None,
        sticker=None,
    )
    update = make_settings_update(target, text="/setgoodbye")

    await zara_bot.group_settings(update, make_context())

    context = make_context()
    await zara_bot.process_leave(context, make_chat(), make_user())

    context.bot.send_message.assert_awaited_once()
    text = context.bot.send_message.await_args.args[1]
    assert text == "goodbye"
    assert "will miss you" not in text


@pytest.mark.asyncio
async def test_bot_added_message_is_short(store):
    context = make_context()
    bot_user = SimpleNamespace(
        id=999,
        full_name="Zara",
        first_name="Zara",
        last_name="",
        username="Meee_Zarabot",
        is_bot=True,
    )

    await zara_bot.process_join(context, make_chat(), bot_user)

    text = context.bot.send_message.await_args.args[1]
    assert "ᴛʜᴀɴᴋ ʏᴏᴜ ғᴏʀ ᴀᴅᴅɪɴɢ ᴢᴀʀᴀ" in text
    assert "/help" in text
    assert "ᴜᴘᴅᴀᴛᴇs" not in text
    assert "sᴜᴘᴘᴏʀᴛ" not in text
    assert "Card game" not in text
    assert "reply_markup" not in context.bot.send_message.await_args.kwargs


@pytest.mark.asyncio
async def test_direct_add_and_chat_member_welcome_once(store):
    context = make_context()
    user = make_user()
    chat = make_chat()
    update = SimpleNamespace(
        effective_chat=chat,
        effective_user=SimpleNamespace(id=1, full_name="Admin", username="admin"),
        message=SimpleNamespace(new_chat_members=[user]),
    )

    await zara_bot.new_members(update, context)

    event = SimpleNamespace(
        chat=chat,
        old_chat_member=SimpleNamespace(status=ChatMemberStatus.LEFT, user=user),
        new_chat_member=SimpleNamespace(status=ChatMemberStatus.MEMBER, user=user),
    )
    await zara_bot.chat_member_update(
        SimpleNamespace(chat_member=event, effective_chat=chat), context
    )

    context.bot.send_message.assert_awaited_once()
    assert "Welcome in Test Group" in context.bot.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_admin_join_also_gets_welcome(store):
    context = make_context()
    user = make_user()

    await zara_bot.process_join(context, make_chat(), user)

    context.bot.send_message.assert_awaited_once()
    assert "Welcome in Test Group" in context.bot.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_media_welcome_falls_back_to_text_when_photo_fails(store, monkeypatch):
    store.db.execute(
        "UPDATE chats SET welcome=? WHERE chat_id=?", ("custom welcome", -1001)
    )
    store.db.commit()
    settings = modcfg.load_settings(store.chat(-1001, "Test Group"))
    settings["welcome_media"] = {"type": "photo", "file_id": "bad-photo"}
    modcfg.save_settings(store, -1001, settings)
    context = make_context()
    context.bot.send_photo.side_effect = TelegramError("bad file")

    await zara_bot.process_join(context, make_chat(), make_user())

    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.await_args.args[1] == "custom welcome"
