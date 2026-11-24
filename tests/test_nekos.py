from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

import bot as zara_bot
from zara import nekos
from zara.handlers import actions


@pytest.fixture(autouse=True)
def clear_cache():
    nekos.reset_cache()
    yield
    nekos.reset_cache()


def payload(category: str, count: int = 2) -> dict:
    return {
        "results": [
            {
                "anime_name": f"Anime {index}",
                "url": f"https://nekos.best/api/v2/{category}/gif-{index}.gif",
                "dimensions": {"width": 480 + index, "height": 270 + index},
            }
            for index in range(count)
        ]
    }


def test_every_action_has_a_verified_gif_category():
    registered = set(actions.NAMES)
    assert set(nekos.ACTION_CATEGORIES) == set(zara_bot.ACTION_NAMES) == registered
    assert set(registered) == set(zara_bot.GIF_ASSET_NAMES)
    assert len(set(nekos.ACTION_CATEGORIES.values())) == len(nekos.ACTION_CATEGORIES)
    assert set(nekos.ACTION_CATEGORIES.values()) <= nekos.GIF_CATEGORIES
    assert all(zara_bot.GIF_ASSETS[action] for action in registered)


@pytest.mark.asyncio
async def test_fetch_parses_and_caches_gifs():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/v2/hug"
        assert request.url.params["amount"] == "8"
        return httpx.Response(200, json=payload("hug", 3))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await nekos.fetch_gifs("hug", 2, client=client)
        second = await nekos.fetch_gifs("hug", 2, client=client)

    assert calls == 1
    assert [gif.url for gif in first] == [gif.url for gif in second]
    assert first[0].category == "hug"
    assert first[0].anime_name == "Anime 0"
    assert first[0].width == 480
    assert first[0].height == 270


@pytest.mark.asyncio
async def test_fetch_rejects_non_gif_and_invalid_categories():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://nekos.best/api/v2/neko/image.png",
                        "dimensions": {"width": 1, "height": 1},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await nekos.fetch_gifs("hug", client=client) == []
        assert await nekos.fetch_gifs("neko", client=client) == []


@pytest.mark.asyncio
async def test_network_failure_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await nekos.fetch_gifs("hug", client=client) == []


@pytest.mark.asyncio
async def test_get_animation_avoids_recent_urls(monkeypatch):
    gifs = [
        nekos.AnimeGif("hug", f"https://nekos.best/api/v2/hug/{index}.gif", "Anime")
        for index in range(3)
    ]
    monkeypatch.setattr(nekos, "fetch_gifs", AsyncMock(return_value=gifs))

    seen = []
    for _ in range(3):
        selected = await nekos.get_animation("hug")
        assert selected is not None
        seen.append(selected.url)

    assert len(set(seen)) == 3


@pytest.mark.asyncio
async def test_search_respects_amount_and_is_deterministic_when_small(monkeypatch):
    gifs = [
        nekos.AnimeGif("hug", f"https://nekos.best/api/v2/hug/{index}.gif", "Anime")
        for index in range(2)
    ]
    monkeypatch.setattr(nekos, "fetch_gifs", AsyncMock(return_value=gifs))

    assert await nekos.search("hug", amount=5) == gifs
    assert await nekos.search("unknown", amount=5) == []


@pytest.mark.asyncio
async def test_disabled_api_makes_no_request(monkeypatch):
    monkeypatch.setenv("ZARA_NEKOS_ENABLED", "0")
    assert await nekos.fetch_gifs("hug") == []


@pytest.mark.asyncio
async def test_timeout_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await nekos.fetch_gifs("hug", client=client) == []


@pytest.mark.asyncio
async def test_server_error_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await nekos.fetch_gifs("hug", client=client) == []


@pytest.mark.asyncio
async def test_concurrent_fetches_share_one_request():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=payload("hug", 3))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await asyncio.gather(
            *(nekos.fetch_gifs("hug", 2, client=client) for _ in range(5))
        )

    assert calls == 1
    assert all(result == results[0] for result in results)
