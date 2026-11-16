"""Useful, key-free tool backends for Zara's command menu."""

from __future__ import annotations

import ast
import asyncio
import io
import math
import operator
import random
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import httpx
import qrcode
from gtts import gTTS
from gtts.tts import gTTSError
from PIL import Image, ImageOps, UnidentifiedImageError
from qrcode.constants import ERROR_CORRECT_M

HTTP_TIMEOUT = 15.0
IMAGE_TIMEOUT = 60.0
MAX_TEXT = 1000
MAX_TTS = 500
MAX_QR = 500
MAX_IMAGE_PROMPT = 300
MAX_IMAGE_BYTES = 20 * 1024 * 1024
SUPPORTED_IMAGE_FORMATS = {
    "png": ("PNG", "png", "PNG"),
    "jpg": ("JPEG", "jpg", "JPG"),
    "jpeg": ("JPEG", "jpg", "JPG"),
    "webp": ("WEBP", "webp", "WEBP"),
}

WEATHER_CODES = {
    0: ("☀️", "Clear sky"),
    1: ("🌤️", "Mainly clear"),
    2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"),
    48: ("🌫️", "Depositing rime fog"),
    51: ("🌦️", "Light drizzle"),
    53: ("🌦️", "Moderate drizzle"),
    55: ("🌦️", "Dense drizzle"),
    56: ("🌧️", "Light freezing drizzle"),
    57: ("🌧️", "Dense freezing drizzle"),
    61: ("🌧️", "Slight rain"),
    63: ("🌧️", "Moderate rain"),
    65: ("🌧️", "Heavy rain"),
    66: ("🌧️", "Light freezing rain"),
    67: ("🌧️", "Heavy freezing rain"),
    71: ("🌨️", "Slight snow"),
    73: ("🌨️", "Moderate snow"),
    75: ("🌨️", "Heavy snow"),
    77: ("🌨️", "Snow grains"),
    80: ("🌦️", "Slight rain showers"),
    81: ("🌦️", "Moderate rain showers"),
    82: ("⛈️", "Violent rain showers"),
    85: ("🌨️", "Slight snow showers"),
    86: ("🌨️", "Heavy snow showers"),
    95: ("⛈️", "Thunderstorm"),
    96: ("⛈️", "Thunderstorm with slight hail"),
    99: ("⛈️", "Thunderstorm with heavy hail"),
}

BLOCKED_IMAGE_WORDS = {
    "nude",
    "nudes",
    "naked",
    "nsfw",
    "porn",
    "porno",
    "sex",
    "sexual",
    "xxx",
    "child",
    "children",
    "minor",
    "underage",
    "gore",
    "blood",
}


class ToolError(Exception):
    """A user-facing tool failure."""


def _client(timeout: float = HTTP_TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "ZaraBot/1.0 (+https://t.me/Meee_Zarabot)"},
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
    )


async def _request(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> httpx.Response:
    try:
        async with _client(timeout) as client:
            return await client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise ToolError("Service abhi available nahi hai. Thodi der baad try karo.") from exc


async def _get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = HTTP_TIMEOUT,
    allow_404: bool = False,
) -> Any:
    response = await _request(url, params=params, timeout=timeout)
    if allow_404 and response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise ToolError("Service ne request accept nahi ki. Thodi der baad try karo.")
    try:
        return response.json()
    except ValueError as exc:
        raise ToolError("Service ne galat response bheja. Dobara try karo.") from exc


def weather_label(code: Any) -> tuple[str, str]:
    try:
        return WEATHER_CODES.get(int(code), ("🌡️", "Unknown"))
    except (TypeError, ValueError):
        return ("🌡️", "Unknown")


async def geocode(city: str) -> dict[str, Any]:
    city = city.strip()
    if not city:
        raise ToolError("City ka naam likho. Example: /weather Delhi")
    if len(city) > 80:
        raise ToolError("City ka naam bahut lamba hai.")
    data = await _get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "en", "format": "json"},
    )
    results = (data or {}).get("results") or []
    if not results:
        raise ToolError(f"'{city}' nahi mila. Spelling check karke dobara try karo.")
    item = results[0]
    return {
        "name": item.get("name") or city,
        "country": item.get("country") or "",
        "admin1": item.get("admin1") or "",
        "latitude": item.get("latitude"),
        "longitude": item.get("longitude"),
        "timezone": item.get("timezone") or "UTC",
    }


async def get_weather(city: str) -> dict[str, Any]:
    place = await geocode(city)
    data = await _get_json(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "is_day,precipitation,weather_code,wind_speed_10m"
            ),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max"
            ),
            "timezone": "auto",
            "forecast_days": 4,
        },
    )
    current = (data or {}).get("current") or {}
    daily = (data or {}).get("daily") or {}
    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    rains = daily.get("precipitation_probability_max") or []
    forecast = []
    for index, date in enumerate(dates[:4]):
        try:
            day = datetime.fromisoformat(str(date)).strftime("%a")
        except ValueError:
            day = str(date)
        code = codes[index] if index < len(codes) else None
        icon, description = weather_label(code)
        forecast.append(
            {
                "day": day,
                "icon": icon,
                "description": description,
                "high": highs[index] if index < len(highs) else None,
                "low": lows[index] if index < len(lows) else None,
                "rain": rains[index] if index < len(rains) else None,
            }
        )
    return {"place": place, "current": current, "daily": forecast}


async def get_local_time(city: str) -> dict[str, Any]:
    place = await geocode(city)
    try:
        zone = ZoneInfo(place["timezone"])
    except Exception:
        zone = timezone.utc
    current = datetime.now(zone)
    offset = current.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return {
        "place": place,
        "time": current.strftime("%I:%M %p").lstrip("0"),
        "date": current.strftime("%a, %d %b %Y"),
        "timezone": place["timezone"],
        "offset": offset,
    }


async def define_word(word: str) -> dict[str, Any]:
    word = word.strip()
    if not word:
        raise ToolError("Word likho. Example: /define hello")
    if len(word) > 60:
        raise ToolError("Word bahut lamba hai.")
    data = await _get_json(
        f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(word, safe='')}",
        allow_404=True,
    )
    if not data or not isinstance(data, list):
        raise ToolError(f"'{word}' ka meaning nahi mila.")
    entry = data[0] if isinstance(data[0], dict) else {}
    phonetic = entry.get("phonetic") or ""
    if not phonetic:
        for item in entry.get("phonetics") or []:
            if isinstance(item, dict) and item.get("text"):
                phonetic = item["text"]
                break
    meanings = []
    for meaning in (entry.get("meanings") or [])[:3]:
        if not isinstance(meaning, dict):
            continue
        part = meaning.get("partOfSpeech") or ""
        for definition in (meaning.get("definitions") or [])[:2]:
            if not isinstance(definition, dict):
                continue
            text = definition.get("definition") or ""
            if not text:
                continue
            meanings.append(
                {
                    "part": part,
                    "definition": text,
                    "example": definition.get("example") or "",
                }
            )
    if not meanings:
        raise ToolError(f"'{word}' ka meaning nahi mila.")
    return {
        "word": entry.get("word") or word,
        "phonetic": phonetic,
        "meanings": meanings,
    }


async def wiki_summary(query: str) -> dict[str, Any]:
    query = query.strip()
    if not query:
        raise ToolError("Wikipedia topic likho. Example: /wiki India")
    title = query.replace(" ", "_")
    data = await _get_json(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title, safe='_')}",
        allow_404=True,
    )
    if not data or not data.get("extract"):
        search = await _get_json(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": 1,
                "namespace": 0,
                "format": "json",
            },
        )
        titles = search[1] if isinstance(search, list) and len(search) > 1 else []
        if titles:
            title = str(titles[0]).replace(" ", "_")
            data = await _get_json(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title, safe='_')}",
                allow_404=True,
            )
    if not data or not data.get("extract"):
        raise ToolError("Wikipedia page nahi mila. Doosre naam se try karo.")
    content_urls = data.get("content_urls") or {}
    desktop = content_urls.get("desktop") or {}
    return {
        "title": data.get("title") or title.replace("_", " "),
        "extract": data.get("extract") or "",
        "url": desktop.get("page")
        or f"https://en.wikipedia.org/wiki/{quote(title, safe='_')}",
    }


async def translate_text(target: str, text: str) -> tuple[str, str]:
    target = target.strip()
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2})?", target):
        raise ToolError("Language code galat hai. Example: /translate hi hello")
    text = text.strip()
    if not text:
        raise ToolError("Translate karne ke liye text likho ya message reply karo.")
    if len(text) > MAX_TEXT:
        raise ToolError(f"Text {MAX_TEXT} characters se zyada hai.")
    try:
        data = await _get_json(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": f"autodetect|{target}"},
            timeout=20,
        )
        payload = (data or {}).get("responseData") or {}
        translated = str(payload.get("translatedText") or "").strip()
        source = str(payload.get("detectedLanguage") or "auto")
        if (
            translated
            and "MYMEMORY WARNING" not in translated.upper()
            and "INVALID SOURCE LANGUAGE" not in translated.upper()
        ):
            return translated, source
    except ToolError:
        pass
    try:
        response = await _request(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": target,
                "dt": "t",
                "q": text,
            },
            timeout=20,
        )
        if response.status_code == 200:
            data = response.json()
            segments = data[0] if isinstance(data, list) and data else []
            translated = "".join(
                str(segment[0])
                for segment in segments
                if isinstance(segment, list) and segment and segment[0]
            ).strip()
            source = str(data[2]) if isinstance(data, list) and len(data) > 2 else "auto"
            if translated:
                return translated, source
    except (ToolError, ValueError, IndexError, TypeError):
        pass
    raise ToolError("Translation service abhi available nahi hai.")


def parse_currency_args(args: list[str]) -> tuple[float, str, str]:
    raw = " ".join(args).strip()
    match = re.fullmatch(
        r"(?:(\d+(?:\.\d+)?)\s*)?([A-Za-z]{3})\s*(?:(?:in|to|->|→)\s*)?([A-Za-z]{3})",
        raw,
        re.IGNORECASE,
    )
    if not match:
        raise ToolError("Format: /currency 100 usd inr")
    amount = float(match.group(1) or 1)
    base = match.group(2).upper()
    target = match.group(3).upper()
    if amount <= 0 or amount > 1_000_000_000_000:
        raise ToolError("Amount valid nahi hai.")
    return amount, base, target


async def convert_currency(args: list[str]) -> dict[str, Any]:
    amount, base, target = parse_currency_args(args)
    data = await _get_json(
        f"https://open.er-api.com/v6/latest/{base}",
        timeout=20,
    )
    if (data or {}).get("result") != "success":
        raise ToolError("Currency rates abhi available nahi hain.")
    rate = (data.get("rates") or {}).get(target)
    if rate is None:
        raise ToolError(f"{target} currency support nahi karti.")
    converted = amount * float(rate)
    return {
        "amount": amount,
        "base": base,
        "target": target,
        "rate": float(rate),
        "converted": converted,
        "updated": data.get("time_last_update_utc") or "",
    }


async def shorten_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ToolError("Valid http/https link bhejo.")
    if len(url) > 2000:
        raise ToolError("Link bahut lamba hai.")
    try:
        response = await _request(
            "https://tinyurl.com/api-create.php",
            params={"url": url},
        )
        short = response.text.strip()
        if response.status_code == 200 and short.startswith("http"):
            return short
    except ToolError:
        pass
    response = await _request(
        "https://is.gd/create.php",
        params={"format": "simple", "url": url},
    )
    short = response.text.strip()
    if response.status_code == 200 and short.startswith("http"):
        return short
    raise ToolError("Short link ban nahi paya. Dobara try karo.")


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_CALC_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}
_CALC_NAMES = {"pi": math.pi, "e": math.e}


def _calc_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _calc_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CALC_NAMES:
        return _CALC_NAMES[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_calc_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _calc_node(node.left)
        right = _calc_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ToolError("Exponent bahut bada hai.")
        result = _BINARY_OPERATORS[type(node.op)](left, right)
        if isinstance(result, int) and result.bit_length() > 10000:
            raise ToolError("Result bahut bada hai.")
        return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function = _CALC_FUNCTIONS.get(node.func.id)
        if function is None or node.keywords:
            raise ToolError("Sirf basic math functions allowed hain.")
        return function(*(_calc_node(argument) for argument in node.args))
    raise ToolError("Sirf numbers aur basic math allowed hai.")


def safe_calc(expression: str) -> int | float:
    expression = expression.strip()
    if not expression:
        raise ToolError("Expression likho. Example: /calc 2+2*5")
    if len(expression) > 200:
        raise ToolError("Expression bahut lamba hai.")
    try:
        tree = ast.parse(expression, mode="eval")
        result = _calc_node(tree)
    except ToolError:
        raise
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise ToolError("Expression valid nahi hai.") from exc
    if isinstance(result, float) and not math.isfinite(result):
        raise ToolError("Result valid nahi hai.")
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result


def format_calc_result(value: int | float) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:.10g}"


def make_qr(text: str) -> bytes:
    text = text.strip()
    if not text:
        raise ToolError("QR ke liye text ya link likho.")
    if len(text) > MAX_QR:
        raise ToolError(f"QR text {MAX_QR} characters se zyada hai.")
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def parse_tts_args(args: list[str], reply_text: str = "") -> tuple[str, str]:
    remaining = list(args)
    language = "en"
    if remaining and re.fullmatch(r"[A-Za-z]{2}(?:-[A-Za-z]{2})?", remaining[0]):
        language = remaining.pop(0).lower()
    text = " ".join(remaining).strip() or reply_text.strip()
    if not text:
        raise ToolError("Format: /tts [lang] text")
    if len(text) > MAX_TTS:
        raise ToolError(f"TTS text {MAX_TTS} characters se zyada hai.")
    return text, language


def _tts_bytes(text: str, language: str) -> bytes:
    tts = gTTS(
        text=text,
        lang=language,
        slow=False,
        timeout=20,
        tld="co.in" if language == "en" else "com",
    )
    buffer = io.BytesIO()
    tts.write_to_fp(buffer)
    return buffer.getvalue()


async def make_tts(text: str, language: str) -> bytes:
    try:
        return await asyncio.to_thread(_tts_bytes, text, language)
    except (gTTSError, ValueError, TypeError) as exc:
        raise ToolError("TTS audio ban nahi paya. Language code check karo.") from exc


def parse_translate_args(args: list[str], reply_text: str = "") -> tuple[str, str]:
    remaining = list(args)
    if not remaining:
        if reply_text.strip():
            return "en", reply_text.strip()
        raise ToolError("Format: /translate <lang> text")
    target = remaining.pop(0).strip()
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2})?", target):
        raise ToolError("Language code galat hai. Example: /translate hi hello")
    text = " ".join(remaining).strip() or reply_text.strip()
    if not text:
        raise ToolError("Translate karne ke liye text likho ya message reply karo.")
    return target, text


def parse_poll(text: str) -> tuple[str, list[str]]:
    parts = [part.strip() for part in text.split("|")]
    if len(parts) < 3:
        raise ToolError("Format: /poll question | option 1 | option 2")
    question = parts[0]
    options = [part for part in parts[1:] if part]
    if not question:
        raise ToolError("Poll ka question likho.")
    if not 2 <= len(options) <= 10:
        raise ToolError("Poll me 2 se 10 options hone chahiye.")
    if len(question) > 300:
        raise ToolError("Poll question 300 characters se zyada hai.")
    if any(len(option) > 100 for option in options):
        raise ToolError("Har option 100 characters se kam hona chahiye.")
    return question, options


def image_format(value: str) -> tuple[str, str, str]:
    key = value.strip().lower().lstrip(".")
    if key not in SUPPORTED_IMAGE_FORMATS:
        raise ToolError("Format png, jpg ya webp me se choose karo.")
    return SUPPORTED_IMAGE_FORMATS[key]


def convert_image(data: bytes, target: str) -> tuple[bytes, str, str]:
    if len(data) > MAX_IMAGE_BYTES:
        raise ToolError("Image 20 MB se badi hai.")
    fmt, extension, label = image_format(target)
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ToolError("Valid image nahi mili. Photo reply karke try karo.") from exc
    if fmt == "JPEG":
        if image.mode in {"RGBA", "LA", "P"}:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
    elif image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA")
    buffer = io.BytesIO()
    save_options: dict[str, Any] = {}
    if fmt in {"JPEG", "WEBP"}:
        save_options["quality"] = 92
    image.save(buffer, format=fmt, **save_options)
    return buffer.getvalue(), f"zara-image.{extension}", label


def is_safe_image_prompt(prompt: str) -> bool:
    words = set(re.findall(r"[a-z]+", prompt.lower()))
    return not bool(words & BLOCKED_IMAGE_WORDS)


async def generate_image(prompt: str) -> bytes:
    prompt = prompt.strip()
    if not prompt:
        raise ToolError("Image prompt likho. Example: /image cute cat")
    if len(prompt) > MAX_IMAGE_PROMPT:
        raise ToolError(f"Prompt {MAX_IMAGE_PROMPT} characters se zyada hai.")
    if not is_safe_image_prompt(prompt):
        raise ToolError("Yeh image prompt allowed nahi hai.")
    url = f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
    params = {
        "width": 1024,
        "height": 1024,
        "nologo": "true",
        "seed": random.randint(1, 1_000_000),
    }
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = await _request(url, params=params, timeout=IMAGE_TIMEOUT)
            content_type = response.headers.get("content-type", "")
            if (
                response.status_code == 200
                and content_type.startswith("image/")
                and response.content
                and len(response.content) <= MAX_IMAGE_BYTES
            ):
                return response.content
            last_error = ToolError("Image service ne image nahi bheji.")
        except ToolError as exc:
            last_error = exc
        if attempt == 0:
            await asyncio.sleep(1)
    raise ToolError("Image generate nahi hui. Dobara try karo.") from last_error
