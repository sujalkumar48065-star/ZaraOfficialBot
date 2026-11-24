from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import InlineQueryResultGif

import bot as zara_bot
from zara import nekos


@pytest.fixture
def store(tmp_path, monkeypatch):
    instance = zara_bot.Store(tmp_path / "action.sqlite3")
    monkeypatch.setattr(zara_bot, "STORE", instance)
    yield instance
    instance.db.close()


def make_action_update(command: str):
    reply = AsyncMock()
    message = SimpleNamespace(
        text=f"/{command}",
        reply_to_message=SimpleNamespace(),
        reply_animation=reply,
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1001),
        effective_user=SimpleNamespace(id=1, full_name="Actor", username="actor"),
        effective_message=message,
    )
    return update, SimpleNamespace(args=[]), reply


def patch_action_target(monkeypatch):
    monkeypatch.setattr(
        zara_bot,
        "target_user",
        AsyncMock(return_value=(2, {"user_id": 2, "name": "Target"})),
    )
    monkeypatch.setattr(zara_bot, "mention_styled", lambda user: "Actor")
    monkeypatch.setattr(zara_bot, "mention_row_styled", lambda row: "Target")
    monkeypatch.setattr(zara_bot, "pair_value", lambda first, second: 77)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", zara_bot.ACTION_NAMES)
async def test_every_action_requests_its_nekos_category(store, monkeypatch, command):
    patch_action_target(monkeypatch)
    gif = nekos.AnimeGif(
        nekos.ACTION_CATEGORIES[command],
        f"https://nekos.best/api/v2/{nekos.ACTION_CATEGORIES[command]}/x.gif",
        "Test Anime",
        480,
        270,
    )
    get_animation = AsyncMock(return_value=gif)
    monkeypatch.setattr(zara_bot.zara_nekos, "get_animation", get_animation)
    giphy = AsyncMock()
    monkeypatch.setattr(zara_bot, "giphy_animation", giphy)
    update, context, reply = make_action_update(command)

    await zara_bot.action(update, context)

    get_animation.assert_awaited_once_with(command)
    giphy.assert_not_awaited()
    reply.assert_awaited_once()
    assert reply.await_args.args[0] == gif.url
    assert reply.await_args.kwargs["parse_mode"] == zara_bot.ParseMode.HTML
    counter = store.db.execute(
        "SELECT count FROM action_counters WHERE action=?", (command,)
    ).fetchone()
    assert counter["count"] == 1


@pytest.mark.asyncio
async def test_action_falls_back_to_giphy_when_nekos_is_empty(store, monkeypatch):
    patch_action_target(monkeypatch)
    monkeypatch.setattr(
        zara_bot.zara_nekos, "get_animation", AsyncMock(return_value=None)
    )
    fallback = io.BytesIO(b"gif-bytes")
    giphy = AsyncMock(return_value=fallback)
    monkeypatch.setattr(zara_bot, "giphy_animation", giphy)
    update, context, reply = make_action_update("hug")

    await zara_bot.action(update, context)

    giphy.assert_awaited_once_with("hug")
    reply.assert_awaited_once()
    assert reply.await_args.args[0] is fallback


@pytest.mark.asyncio
async def test_inline_query_prefers_nekos_gifs(monkeypatch):
    gif = nekos.AnimeGif(
        "hug",
        "https://nekos.best/api/v2/hug/inline.gif",
        "Test Anime",
        480,
        270,
    )
    monkeypatch.setattr(
        zara_bot.zara_nekos, "search", AsyncMock(return_value=[gif])
    )
    giphy = AsyncMock()
    monkeypatch.setattr(zara_bot, "giphy_search", giphy)
    answer = AsyncMock()
    update = SimpleNamespace(
        inline_query=SimpleNamespace(query="hug", answer=answer)
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"username": "Meee_Zarabot"})
    )

    await zara_bot.inline_query(update, context)

    giphy.assert_not_awaited()
    answer.assert_awaited_once()
    results = answer.await_args.args[0]
    assert len(results) == 1
    assert isinstance(results[0], InlineQueryResultGif)
    assert results[0].gif_url == gif.url
    assert results[0].title == "Hug · Test Anime"
