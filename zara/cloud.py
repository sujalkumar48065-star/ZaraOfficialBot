"""Telegram-channel backed snapshots for the main Zara database.

The Bot API cannot read channel history, but it can read the pinned message and
channel description via ``getChat``.  A gzipped JSON export is uploaded as a
document, pinned when possible, and its file_id is also kept in the channel
description as a fallback pointer.  This keeps the existing SQLite/TiDB storage
as the working copy while the channel acts as the durable cloud copy.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from telegram import InputFile
from telegram.error import TelegramError

from .db import Database
from .db.maintenance import export_payload, import_payload

log = logging.getLogger(__name__)

DEFAULT_CHANNEL_ID = -1004477716091
CHANNEL_ID = int(os.getenv("ZARA_DB_CHANNEL_ID", str(DEFAULT_CHANNEL_ID)))
SNAPSHOT_PREFIX = "zara-db-"
SNAPSHOT_MARKER = "ZARA_DB_SNAPSHOT_V1"
POINTER_MARKER = "ZARA_DB_POINTER_V1:"
SYNC_INTERVAL = int(os.getenv("ZARA_DB_SYNC_INTERVAL", "120"))

_RESTORE_OK = False
_LAST_CHECKSUM: str | None = None
_LAST_SNAPSHOT_MESSAGE_ID: int | None = None


def enabled() -> bool:
    return CHANNEL_ID != 0


def ready() -> bool:
    return _RESTORE_OK


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _pinned_candidate(pinned: Any) -> tuple[str, str] | None:
    if pinned is None:
        return None
    document = getattr(pinned, "document", None)
    if document is None:
        return None
    name = getattr(document, "file_name", "") or ""
    caption = getattr(pinned, "caption", "") or ""
    if not name.startswith(SNAPSHOT_PREFIX) and SNAPSHOT_MARKER not in caption:
        return None
    file_id = getattr(document, "file_id", None)
    if not file_id:
        return None
    match = re.search(r"ZARA_DB_SNAPSHOT_V1\s+(\d{8}T\d{6}Z)", caption)
    return (match.group(1) if match else "", str(file_id))


def _pointer_candidate(chat: Any) -> tuple[str, str] | None:
    description = getattr(chat, "description", "") or ""
    marker = description.find(POINTER_MARKER)
    if marker < 0:
        return None
    value = description[marker + len(POINTER_MARKER) :].strip()
    match = re.match(r"(\d{8}T\d{6}Z):([A-Za-z0-9_-]+)", value)
    if match:
        return (match.group(1), match.group(2))
    file_id = value.split()[0] if value else ""
    return ("", file_id) if file_id else None


def _is_snapshot(pinned: Any) -> bool:
    return _pinned_candidate(pinned) is not None


def _snapshot_bytes(db: Database) -> tuple[dict[str, Any], bytes]:
    payload = export_payload(db)
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return payload, gzip.compress(raw, compresslevel=9)


async def restore(bot: Any, db: Database) -> dict[str, Any]:
    """Restore the latest pinned snapshot when the local database is empty."""
    global _RESTORE_OK, _LAST_CHECKSUM, _LAST_SNAPSHOT_MESSAGE_ID
    if not enabled():
        _RESTORE_OK = True
        log.info("Cloud database disabled; set ZARA_DB_CHANNEL_ID to enable it")
        return {"status": "disabled"}

    try:
        local_users = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        if local_users and int(local_users["n"]) > 0:
            _RESTORE_OK = True
            log.info("Cloud restore skipped; local database already contains users")
            return {"status": "skipped", "reason": "local database not empty"}

        chat = await bot.get_chat(CHANNEL_ID)
        pinned = getattr(chat, "pinned_message", None)
        candidates = [
            candidate
            for candidate in (
                _pinned_candidate(pinned),
                _pointer_candidate(chat),
            )
            if candidate is not None
        ]
        if not candidates:
            _RESTORE_OK = True
            log.info("No cloud snapshot found in channel %s", CHANNEL_ID)
            return {"status": "empty"}
        candidates.sort(key=lambda item: item[0], reverse=True)
        last_error: Exception | None = None
        for _, file_id in candidates:
            try:
                telegram_file = await bot.get_file(file_id)
                data = await telegram_file.download_as_bytearray()
                payload = json.loads(gzip.decompress(bytes(data)).decode("utf-8"))
                result = import_payload(db, payload, replace=True)
                _RESTORE_OK = True
                _LAST_CHECKSUM = str(payload.get("checksum") or "")
                if pinned is not None:
                    _LAST_SNAPSHOT_MESSAGE_ID = int(
                        getattr(pinned, "message_id", 0) or 0
                    )
                log.info(
                    "Cloud database restored from channel %s: %s tables, %s rows",
                    CHANNEL_ID,
                    result["tables"],
                    result["rows"],
                )
                return {"status": "restored", **result}
            except Exception as exc:
                last_error = exc
                log.warning(
                    "Cloud snapshot candidate %s failed: %s: %s",
                    file_id,
                    type(exc).__name__,
                    exc,
                )
        raise last_error or RuntimeError("no usable cloud snapshot")
    except Exception as exc:
        _RESTORE_OK = False
        log.warning("Cloud database restore failed: %s: %s", type(exc).__name__, exc)
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


async def save(bot: Any, db: Database, *, force: bool = False) -> dict[str, Any]:
    """Upload and pin a fresh snapshot unless the payload is unchanged."""
    global _LAST_CHECKSUM, _LAST_SNAPSHOT_MESSAGE_ID
    if not enabled():
        return {"status": "disabled"}
    if not _RESTORE_OK:
        log.warning("Cloud database save skipped; restore has not succeeded")
        return {"status": "skipped", "reason": "restore not ready"}

    try:
        payload, data = _snapshot_bytes(db)
        checksum = str(payload.get("checksum") or "")
        if not force and checksum and checksum == _LAST_CHECKSUM:
            return {"status": "unchanged", "checksum": checksum}

        old_message_id = _LAST_SNAPSHOT_MESSAGE_ID
        if old_message_id is None:
            chat = await bot.get_chat(CHANNEL_ID)
            pinned = getattr(chat, "pinned_message", None)
            if _is_snapshot(pinned):
                old_message_id = int(getattr(pinned, "message_id", 0) or 0)

        stamp = _timestamp()
        filename = f"{SNAPSHOT_PREFIX}{stamp}.json.gz"
        document = InputFile(io.BytesIO(data), filename=filename)
        rows = sum(payload.get("row_counts", {}).values())
        message = await bot.send_document(
            CHANNEL_ID,
            document=document,
            caption=f"{SNAPSHOT_MARKER} {stamp} rows={rows}",
            disable_notification=True,
        )
        pinned = False
        try:
            await bot.pin_chat_message(
                CHANNEL_ID, message.message_id, disable_notification=True
            )
            pinned = True
        except TelegramError as exc:
            log.warning("Could not pin cloud snapshot: %s", exc)

        # Some channel admins can post but not pin.  Store the file_id in the
        # channel description as a read-only pointer so restore still works.
        pointer_set = False
        try:
            await bot.set_chat_description(
                CHANNEL_ID,
                f"{POINTER_MARKER}{stamp}:{message.document.file_id}",
            )
            pointer_set = True
        except TelegramError as exc:
            log.warning("Could not store cloud snapshot pointer: %s", exc)

        if not pinned and not pointer_set:
            return {
                "status": "failed",
                "error": "snapshot uploaded but neither pin nor pointer could be set",
            }

        if old_message_id and old_message_id != message.message_id:
            try:
                await bot.delete_message(CHANNEL_ID, old_message_id)
            except TelegramError as exc:
                log.info("Could not remove previous cloud snapshot: %s", exc)

        _LAST_CHECKSUM = checksum
        _LAST_SNAPSHOT_MESSAGE_ID = int(message.message_id)
        log.info(
            "Cloud database snapshot uploaded to channel %s: %s bytes, %s rows",
            CHANNEL_ID,
            len(data),
            rows,
        )
        return {
            "status": "saved",
            "bytes": len(data),
            "rows": rows,
            "checksum": checksum,
            "message_id": int(message.message_id),
            "pinned": pinned,
            "pointer": pointer_set,
        }
    except Exception as exc:
        log.warning("Cloud database save failed: %s: %s", type(exc).__name__, exc)
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
