"""Nekos.best v2 anime GIF client.

The API is public, needs no authentication, and returns one random GIF URL per
request. GIF metadata is cached briefly so a busy chat does not hit the API for
every command, while recent URLs are avoided per action.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections import deque
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

BASE_URL = os.getenv("ZARA_NEKOS_BASE_URL", "https://nekos.best/api/v2").rstrip("/")
USER_AGENT = os.getenv(
    "ZARA_NEKOS_USER_AGENT",
    "ZaraOfficialBot (https://github.com/sujalkumar48065-star/ZaraOfficialBot)",
)
try:
    TIMEOUT_SECONDS = float(os.getenv("ZARA_NEKOS_TIMEOUT", "6"))
except ValueError:
    TIMEOUT_SECONDS = 6.0
TIMEOUT_SECONDS = max(1.0, TIMEOUT_SECONDS)
CACHE_SECONDS = 600
EMPTY_CACHE_SECONDS = 30
POOL_SIZE = 8
RECENT_LIMIT = 6

GIF_CATEGORIES = frozenset(
    {
        "angry",
        "baka",
        "bite",
        "bleh",
        "blowkiss",
        "blush",
        "bonk",
        "bored",
        "carry",
        "clap",
        "confused",
        "cry",
        "cuddle",
        "dance",
        "facepalm",
        "feed",
        "handhold",
        "handshake",
        "happy",
        "highfive",
        "hug",
        "kabedon",
        "kick",
        "kiss",
        "lappillow",
        "laugh",
        "lurk",
        "nod",
        "nom",
        "nope",
        "nya",
        "pat",
        "peck",
        "poke",
        "pout",
        "punch",
        "run",
        "salute",
        "shake",
        "shoot",
        "shocked",
        "shrug",
        "sip",
        "slap",
        "sleep",
        "smile",
        "smug",
        "spin",
        "stare",
        "tableflip",
        "teehee",
        "think",
        "thumbsup",
        "tickle",
        "wag",
        "wave",
        "wink",
        "yawn",
        "yeet",
    }
)

ACTION_CATEGORIES: dict[str, str] = {
    "kiss": "kiss",
    "hug": "hug",
    "slap": "slap",
    "punch": "punch",
    "murder": "kick",
    "bite": "bite",
    "crush": "blush",
    "love": "blowkiss",
    "pat": "pat",
    "cuddle": "cuddle",
    "dance": "dance",
    "highfive": "highfive",
    "bonk": "bonk",
}


@dataclass(frozen=True, slots=True)
class AnimeGif:
    category: str
    url: str
    anime_name: str
    width: int | None = None
    height: int | None = None


_CACHE: dict[str, tuple[float, list[AnimeGif]]] = {}
_RECENT: dict[str, deque[str]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


def enabled() -> bool:
    return os.getenv("ZARA_NEKOS_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def reset_cache() -> None:
    _CACHE.clear()
    _RECENT.clear()
    _LOCKS.clear()


def category_for(action: str) -> str | None:
    category = ACTION_CATEGORIES.get((action or "").strip().lower())
    return category if category in GIF_CATEGORIES else None


def _parse_results(category: str, payload: object) -> list[AnimeGif]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("results")
    if not isinstance(rows, list):
        return []
    parsed: list[AnimeGif] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url.startswith("https://") or not url.split("?", 1)[0].lower().endswith(".gif"):
            continue
        if url in seen:
            continue
        seen.add(url)
        dimensions = row.get("dimensions")
        width = height = None
        if isinstance(dimensions, dict):
            try:
                width = int(dimensions.get("width")) if dimensions.get("width") else None
                height = int(dimensions.get("height")) if dimensions.get("height") else None
            except (TypeError, ValueError):
                width = height = None
        parsed.append(
            AnimeGif(
                category=category,
                url=url,
                anime_name=str(row.get("anime_name") or "").strip(),
                width=width,
                height=height,
            )
        )
    return parsed


async def fetch_gifs(
    category: str,
    amount: int = POOL_SIZE,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[AnimeGif]:
    if not enabled() or category not in GIF_CATEGORIES:
        return []
    amount = max(1, min(20, int(amount)))
    now = time.monotonic()
    cached = _CACHE.get(category)
    if cached and cached[0] > now and (not cached[1] or len(cached[1]) >= amount):
        return cached[1][:amount]

    lock = _LOCKS.get(category)
    if lock is None:
        lock = _LOCKS[category] = asyncio.Lock()
    async with lock:
        now = time.monotonic()
        cached = _CACHE.get(category)
        if cached and cached[0] > now and (not cached[1] or len(cached[1]) >= amount):
            return cached[1][:amount]

        owns_client = client is None
        http = client or httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=min(3.0, TIMEOUT_SECONDS)),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        try:
            response = await http.get(
                f"{BASE_URL}/{category}",
                params={"amount": max(amount, POOL_SIZE)},
            )
            response.raise_for_status()
            gifs = _parse_results(category, response.json())
        except (httpx.HTTPError, TypeError, ValueError, KeyError) as exc:
            log.info("Nekos.best unavailable for %s: %s", category, type(exc).__name__)
            gifs = []
        finally:
            if owns_client:
                await http.aclose()

        ttl = CACHE_SECONDS if gifs else EMPTY_CACHE_SECONDS
        _CACHE[category] = (time.monotonic() + ttl, gifs)
        return gifs[:amount]


async def get_animation(
    action: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> AnimeGif | None:
    category = category_for(action)
    if not category:
        return None
    gifs = await fetch_gifs(category, POOL_SIZE, client=client)
    if not gifs:
        return None
    recent = _RECENT.setdefault(action, deque(maxlen=RECENT_LIMIT))
    available = [gif for gif in gifs if gif.url not in recent] or gifs
    selected = random.choice(available)
    recent.append(selected.url)
    return selected


async def search(
    action: str,
    amount: int = 8,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[AnimeGif]:
    category = category_for(action)
    if not category:
        return []
    gifs = await fetch_gifs(category, amount, client=client)
    if len(gifs) <= amount:
        return gifs
    return random.sample(gifs, amount)
