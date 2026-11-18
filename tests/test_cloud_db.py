from __future__ import annotations

import gzip
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import TelegramError

import bot as zara_bot
from zara import cloud


class FakeCloudBot:
    id = 999

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.filenames: dict[str, str] = {}
        self.pinned = None
        self.description = ""
        self.next_message_id = 100
        self.deleted: list[int] = []

    async def get_chat(self, chat_id):
        return SimpleNamespace(pinned_message=self.pinned, description=self.description)

    async def send_document(
        self, chat_id, document, caption=None, disable_notification=None
    ):
        message_id = self.next_message_id
        self.next_message_id += 1
        file_id = f"file-{message_id}"
        self.files[file_id] = document.input_file_content
        self.filenames[file_id] = document.filename
        return SimpleNamespace(
            message_id=message_id,
            document=SimpleNamespace(file_id=file_id, file_name=document.filename),
        )

    async def pin_chat_message(self, chat_id, message_id, disable_notification=None):
        file_id = f"file-{message_id}"
        self.pinned = SimpleNamespace(
            message_id=message_id,
            caption="",
            document=SimpleNamespace(
                file_id=file_id,
                file_name=self.filenames.get(file_id, ""),
            ),
        )

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)

    async def set_chat_description(self, chat_id, description):
        self.description = description

    async def get_file(self, file_id):
        return SimpleNamespace(
            download_as_bytearray=AsyncMock(return_value=bytearray(self.files[file_id]))
        )


@pytest.fixture
def store(tmp_path, monkeypatch):
    instance = zara_bot.Store(tmp_path / "cloud.sqlite3")
    monkeypatch.setattr(zara_bot, "STORE", instance)
    zara_bot._CHANNEL_LAST_MEDIA.clear()
    yield instance
    instance.db.close()


@pytest.fixture(autouse=True)
def reset_cloud(monkeypatch):
    monkeypatch.setattr(cloud, "_RESTORE_OK", True)
    monkeypatch.setattr(cloud, "_LAST_CHECKSUM", None)
    monkeypatch.setattr(cloud, "_LAST_SNAPSHOT_MESSAGE_ID", None)


def test_action_caption_aliases():
    assert zara_bot.action_from_caption("High Five") == "highfive"
    assert zara_bot.action_from_caption("high-fi reaction") == "highfive"
    assert zara_bot.action_from_caption("head pat") == "pat"
    assert zara_bot.action_from_caption("anime kiss reaction") == "kiss"
    assert zara_bot.action_from_caption("nothing useful") is None


@pytest.mark.asyncio
async def test_channel_media_and_caption_store_action_gifs(store):
    media = SimpleNamespace(
        chat_id=cloud.CHANNEL_ID,
        message_id=10,
        caption="reaction",
        animation=SimpleNamespace(file_id="animation-highfive"),
        video=None,
        document=None,
        reply_to_message=None,
    )
    update = SimpleNamespace(channel_post=media)
    await zara_bot.channel_gif_capture(update, SimpleNamespace())

    text = SimpleNamespace(
        chat_id=cloud.CHANNEL_ID,
        message_id=11,
        text="high five",
        animation=None,
        video=None,
        document=None,
        reply_to_message=None,
    )
    await zara_bot.channel_caption_capture(
        SimpleNamespace(channel_post=text), SimpleNamespace()
    )

    assert store.db.get_meta("action_gif:highfive") == "animation-highfive"


class NoPinCloudBot(FakeCloudBot):
    async def pin_chat_message(self, chat_id, message_id, disable_notification=None):
        raise TelegramError("not enough rights to pin")


@pytest.mark.asyncio
async def test_cloud_save_uses_description_pointer_without_pin(store, tmp_path):
    store.user(SimpleNamespace(id=7, username="pointer", full_name="Pointer User"))
    bot = NoPinCloudBot()

    saved = await cloud.save(bot, store.db, force=True)

    assert saved["status"] == "saved"
    assert saved["pinned"] is False
    assert saved["pointer"] is True
    assert bot.description.startswith(cloud.POINTER_MARKER)

    restored = zara_bot.Store(tmp_path / "pointer-restored.sqlite3")
    try:
        result = await cloud.restore(bot, restored.db)
        row = restored.db.execute("SELECT * FROM users WHERE user_id=7").fetchone()
        assert result["status"] == "restored"
        assert row is not None
    finally:
        restored.db.close()


@pytest.mark.asyncio
async def test_cloud_save_and_restore_pinned_snapshot(store, tmp_path):
    user = SimpleNamespace(id=42, username="clouduser", full_name="Cloud User")
    store.user(user)
    bot = FakeCloudBot()

    saved = await cloud.save(bot, store.db, force=True)
    assert saved["status"] == "saved"
    assert bot.pinned is not None
    payload = json.loads(
        gzip.decompress(bot.files[bot.pinned.document.file_id]).decode("utf-8")
    )
    assert payload["tables"]["users"][0]["user_id"] == 42

    restored = zara_bot.Store(tmp_path / "restored.sqlite3")
    try:
        result = await cloud.restore(bot, restored.db)
        row = restored.db.execute("SELECT * FROM users WHERE user_id=42").fetchone()
        assert result["status"] == "restored"
        assert row is not None
        assert row["username"] == "clouduser"
    finally:
        restored.db.close()
