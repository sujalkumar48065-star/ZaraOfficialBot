"""Environment-backed configuration for Zara."""

from __future__ import annotations

import os
from pathlib import Path

from .env import load_env

load_env()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("ZARA_DB_PATH", str(DATA_DIR / "zara.sqlite3")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _admin_ids() -> set[int]:
    return {
        int(value)
        for value in os.getenv("ZARA_ADMIN_IDS", "8799679469").split(",")
        if value.strip().isdigit()
    }


OWNER_ID = int(os.getenv("ZARA_OWNER_ID", "8799679469"))
ADMIN_IDS = _admin_ids()
UPDATE_CHANNEL = os.getenv("ZARA_UPDATE_CHANNEL", "").lstrip("@")
UPDATE_CHANNEL_URL = (
    os.getenv("ZARA_UPDATE_CHANNEL_URL", "").strip()
    or (f"https://t.me/{UPDATE_CHANNEL}" if UPDATE_CHANNEL else "")
)
UPDATE_CHANNEL_LABEL = (
    os.getenv("ZARA_UPDATE_CHANNEL_LABEL", "").strip()
    or (f"@{UPDATE_CHANNEL}" if UPDATE_CHANNEL else "Zara updates")
)
_raw_update_chat_id = os.getenv("ZARA_UPDATE_CHAT_ID", "").strip()
if _raw_update_chat_id:
    try:
        UPDATE_CHANNEL_CHAT_ID = int(_raw_update_chat_id)
    except ValueError:
        UPDATE_CHANNEL_CHAT_ID = _raw_update_chat_id
else:
    UPDATE_CHANNEL_CHAT_ID = f"@{UPDATE_CHANNEL}" if UPDATE_CHANNEL else ""
SUPPORT_USERNAME = os.getenv("ZARA_SUPPORT_USERNAME", "Tilx1").lstrip("@")
DEVELOPER_USERNAME = os.getenv("ZARA_DEVELOPER_USERNAME", "Tilx1").lstrip("@")
DEVELOPER_URL = (
    os.getenv("ZARA_DEVELOPER_URL", "").strip()
    or f"https://t.me/{DEVELOPER_USERNAME or SUPPORT_USERNAME}"
)
START_IMAGE = Path(os.getenv("ZARA_START_IMAGE", str(ROOT / "assets" / "start.jpg")))
POLICY_URL = os.getenv("ZARA_POLICY_URL", "").strip()
PAYMENT_URL = os.getenv("ZARA_PAYMENT_URL", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
_dev_domain = os.getenv("REPLIT_DEV_DOMAIN", "").strip()
VERIFY_WEBAPP_URL = os.getenv(
    "ZARA_VERIFY_WEBAPP_URL",
    f"https://{_dev_domain}/api/verify" if _dev_domain else "",
).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


# Optional keep-alive. Leave ZARA_SELF_PING_URL empty until the web URL is
# available; ZARA_WEB_URL and Render's RENDER_EXTERNAL_URL are accepted as
# fallbacks so the same code works on Render without extra configuration.
SELF_PING_URL = (
    os.getenv("ZARA_SELF_PING_URL", "").strip()
    or os.getenv("ZARA_WEB_URL", "").strip()
    or os.getenv("RENDER_EXTERNAL_URL", "").strip()
)
SELF_PING_INTERVAL = max(30, _env_int("ZARA_SELF_PING_INTERVAL", 240))
SELF_PING_DELAY = max(1, _env_int("ZARA_SELF_PING_DELAY", 30))

STICKER_PACK_LINKS = (
    "https://t.me/addstickers/funny_cute_cats",
    "https://t.me/addstickers/spaceystickers_by_fStikBot",
    "https://t.me/addstickers/VZEpOeg_by_sticbot",
    "https://t.me/addstickers/kang_6389040964video_by_Sticker_kang_robot",
    "https://t.me/addstickers/peach_goma_animation",
    "https://t.me/addstickers/ddOmGe_by_fStikBot",
    "https://t.me/addstickers/Webp_15",
    "https://t.me/addstickers/PeachCatPack14",
    "https://t.me/addstickers/peachcatsticker",
    "https://t.me/addstickers/animation_0_8_Cat",
    "https://t.me/addstickers/PeachGomaPack11",
    "https://t.me/addstickers/SKYstrickers_by_fStikBot",
    "https://t.me/addstickers/kang_6644255517video_by_Sticker_kang_robot",
    "https://t.me/addstickers/AnyaVid",
)